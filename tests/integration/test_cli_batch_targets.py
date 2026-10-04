"""B6: http-observe / port-check 的 --targets 批量模式。

串行逐台、单台失败不中断、逐台 scope 复核(全通配 scope 下天然全过,
但 fail-closed 语义保留);runner 全部注入,零网络。
"""
import json

import surface_intel.cli as cli
import surface_intel.safe_http_runner
import surface_intel.safe_port_runner
from surface_intel.state.database import Database


def _seed(tmp_path, scope_version="scope-v1"):
    db = Database(tmp_path / "task.sqlite")
    db.initialize()
    payload = {"scope_version": scope_version, "confirmed": True,
               # is_in_scope 是 fnmatch 匹配——子域必须显式带 *.(与 SKILL.md scope 模板一致)
               "authorized_domains": ["example.com", "*.example.com",
                                      "example.net", "*.example.net"],
               "active_testing_allowed": True,
               "active_testing_level": "safe_probe"}
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO task(task_id,status,schema_version,scope_version) "
            "VALUES('t','planned','1.0',?)", (scope_version,))
        connection.execute(
            "INSERT INTO scope_snapshot(scope_id,task_id,scope_version,payload_json,confirmed) "
            "VALUES('s1','t',?,?,1)", (scope_version, json.dumps(payload)))
        connection.commit()
    # close 必须在 with 之外(with 的 __exit__ 还要对连接做 commit);
    # 显式关掉让 WAL 立即 checkpoint,不赌 GC 时机
    connection.close()
    return db


def _targets_file(tmp_path, lines):
    path = tmp_path / "targets.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def _completed_result():
    from types import SimpleNamespace
    return SimpleNamespace(status="completed", observations=1, error=None,
                           review_created=False, override_used=False)


def test_http_observe_batch_mixed_targets(tmp_path, monkeypatch, capsys):
    seen = []

    async def fake_run_http_observe(db, client, *, task_id, scope, url, method,
                                    allow_hosted_third_party):
        seen.append(url)
        return _completed_result()

    monkeypatch.setattr(surface_intel.safe_http_runner, "run_http_observe",
                        fake_run_http_observe)
    _seed(tmp_path)
    targets = _targets_file(tmp_path, [
        "# 注释行与空行跳过",
        "",
        "https://www.example.com/",
        "api.example.com",
    ])
    code = cli.main(["http-observe", "t", "--task-root", str(tmp_path),
                     "--targets", targets, "--real", "--confirm-real"])
    output = json.loads(capsys.readouterr().out)
    assert code == 0, output
    assert output["ok"] is True and output["total"] == 2
    assert output["completed"] == 2 and output["failed"] == 0
    # 裸 host 规范化为 https://
    assert seen == ["https://www.example.com/", "https://api.example.com"]


def test_http_observe_batch_failure_does_not_stop_batch(tmp_path, monkeypatch, capsys):
    async def fake_run_http_observe(db, client, *, task_id, scope, url, method,
                                    allow_hosted_third_party):
        if "slow" in url:
            raise TimeoutError("slow")
        return _completed_result()

    monkeypatch.setattr(surface_intel.safe_http_runner, "run_http_observe",
                        fake_run_http_observe)
    _seed(tmp_path)
    targets = _targets_file(tmp_path, [
        "https://slow.example.com/", "https://www.example.com/",
        "evil.example.org",
    ])
    code = cli.main(["http-observe", "t", "--task-root", str(tmp_path),
                     "--targets", targets, "--real", "--confirm-real"])
    output = json.loads(capsys.readouterr().out)
    assert code == 1 and output["ok"] is False
    assert output["completed"] == 1 and output["failed"] == 2
    errors = {r["target"]: r["error"] for r in output["results"]}
    assert errors["https://slow.example.com/"] == "TimeoutError"
    # 出 scope 的目标逐台拒绝,不中断后续
    assert errors["evil.example.org"] == "target_out_of_scope"
    # 失败不中断:三台都得到处理
    assert len(output["results"]) == 3


def test_port_check_batch_host_port_lines(tmp_path, monkeypatch, capsys):
    seen = []

    async def fake_run_port_check(db, client, *, task_id, scope, target, port,
                                  allow_hosted_third_party):
        seen.append((target, port))
        return _completed_result()

    monkeypatch.setattr(surface_intel.safe_port_runner, "run_port_check",
                        fake_run_port_check)
    _seed(tmp_path)
    targets = _targets_file(tmp_path, [
        "www.example.com:443",
        "not-a-target-line",
        "www.example.net:8899",
    ])
    code = cli.main(["port-check", "t", "--task-root", str(tmp_path),
                     "--targets", targets, "--real", "--confirm-real"])
    output = json.loads(capsys.readouterr().out)
    # 有格式坏行 → failed=1 → 退出码 1(部分失败),但成功的两台照常入库
    assert code == 1 and output["ok"] is False
    assert seen == [("www.example.com", 443), ("www.example.net", 8899)]
    errors = {r["target"]: r["error"] for r in output["results"]}
    assert errors["not-a-target-line"] == "invalid_target_format"
    assert output["completed"] == 2 and output["failed"] == 1


def test_batch_and_single_flags_are_mutually_exclusive(tmp_path, capsys):
    targets = _targets_file(tmp_path, ["www.example.com"])
    code = cli.main(["http-observe", "t", "--task-root", str(tmp_path),
                     "--url", "https://www.example.com/", "--targets", targets,
                     "--real", "--confirm-real"])
    output = json.loads(capsys.readouterr().out)
    assert code == 2
    assert output["error"] == "url_and_targets_mutually_exclusive"

    code = cli.main(["port-check", "t", "--task-root", str(tmp_path),
                     "--target", "www.example.com", "--port", "443",
                     "--targets", targets, "--real", "--confirm-real"])
    output = json.loads(capsys.readouterr().out)
    assert code == 2
    assert output["error"] == "single_target_and_targets_mutually_exclusive"


def test_empty_targets_file_rejected(tmp_path, capsys):
    _seed(tmp_path)
    targets = _targets_file(tmp_path, ["# 只有注释"])
    code = cli.main(["http-observe", "t", "--task-root", str(tmp_path),
                     "--targets", targets, "--real", "--confirm-real"])
    output = json.loads(capsys.readouterr().out)
    assert code == 2
    assert output["error"] == "targets_empty"
