"""C1: reports land under reports/<task-id>/ - consecutive report runs of
different tasks no longer overwrite each other."""
import json

from surface_intel.cli import main
from surface_intel.state.database import Database


def _seed_task(tmp_path, task_id):
    db = Database(tmp_path / "task.sqlite")
    db.initialize()
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO task(task_id,status,schema_version,scope_version) VALUES(?,'completed','1.0','scope-v1')",
            (task_id,))
        payload = {"target": "www.example.com", "record_type": "A",
                   "records": ["203.0.113.5"], "status": "observed",
                   "observed_at": "2026-10-04T10:00:00+00:00"}
        connection.execute(
            "INSERT INTO observation(observation_id,task_id,source,target,payload_json) VALUES(?,?,?,?,?)",
            (f"o-{task_id}", task_id, "dns_resolver", "www.example.com",
             json.dumps(payload)))


def test_report_writes_per_task_subdirectory(tmp_path):
    _seed_task(tmp_path, "t")
    code = main(["report", "t", "--task-root", str(tmp_path), "--xlsx"])
    assert code == 0
    for name in ("result.json", "result.xlsx", "analyst_report.xlsx"):
        assert (tmp_path / "reports" / "t" / name).exists(), name


def test_second_task_report_does_not_overwrite_first(tmp_path):
    _seed_task(tmp_path, "t1")
    _seed_task(tmp_path, "t2")
    assert main(["report", "t1", "--task-root", str(tmp_path)]) == 0
    first = (tmp_path / "reports" / "t1" / "result.json").read_bytes()
    assert main(["report", "t2", "--task-root", str(tmp_path)]) == 0
    # the t1 deliverable survives t2's report byte-for-byte
    assert (tmp_path / "reports" / "t1" / "result.json").read_bytes() == first


def test_report_without_task_id_or_merge_refused(tmp_path, capsys):
    code = main(["report", "--task-root", str(tmp_path)])
    assert code == 2
    assert json.loads(capsys.readouterr().out)["error"] == "task_id_or_merge_required"
