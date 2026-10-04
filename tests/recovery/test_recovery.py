import json

from surface_intel.state.database import Database
from surface_intel.state.recovery import create_checkpoint, latest_checkpoint, recover_started_actions


def test_started_action_becomes_unknown_and_checkpoint_is_readable(tmp_path):
    db = Database(tmp_path / "task.sqlite")
    db.initialize()
    with db.connect() as connection:
        connection.execute("INSERT INTO task(task_id,status,schema_version,scope_version) VALUES('t','planned','1.0','s')")
        connection.execute(
            "INSERT INTO action(action_id,task_id,idempotency_key,adapter,status,scope_version) VALUES('a','t','k','safe_http','started','s')"
        )
    checkpoint_id = create_checkpoint(db, "t", phase="planned", payload={"pending": ["a"]})
    assert checkpoint_id.startswith("checkpoint-")
    assert latest_checkpoint(db, "t")["payload"]["pending"] == ["a"]
    assert recover_started_actions(db, "t") == ["a"]
    assert db.health()["ok"] is True
    row = next(db.rows("SELECT status,error_code FROM action WHERE action_id='a'"))
    assert row["status"] == "unknown"
    assert row["error_code"] == "external_state_unknown"


def test_baseline_checkpoint_does_not_shadow_resume_checkpoint(tmp_path):
    """D11：baseline-save 之后 latest_checkpoint 仍返回进度断点，FOFA 页码不清零。"""
    from surface_intel.monitor import save_baseline

    db = Database(tmp_path / "task.sqlite")
    db.initialize()
    with db.connect() as connection:
        connection.execute("INSERT INTO task(task_id,status,schema_version,scope_version) VALUES('t','planned','1.0','s')")
    create_checkpoint(db, "t", phase="collecting_fofa", payload={"fofa_pages": 3})
    save_baseline(db, "t", "s")
    checkpoint = latest_checkpoint(db, "t")
    assert checkpoint["phase"] == "collecting_fofa"
    assert checkpoint["payload"]["fofa_pages"] == 3
    # 再存一次 baseline 也不会遮蔽进度断点
    save_baseline(db, "t", "s")
    assert latest_checkpoint(db, "t")["phase"] == "collecting_fofa"


def test_diff_checkpoints_compares_any_two_baselines(tmp_path):
    """D11：任意两次 baseline 对比 + 与当前资产对比（差量复查地基）。"""
    from surface_intel.monitor import diff_checkpoints, save_baseline

    db = Database(tmp_path / "task.sqlite")
    db.initialize()
    with db.connect() as connection:
        connection.execute("INSERT INTO task(task_id,status,schema_version,scope_version) VALUES('t','completed','1.0','s')")
        connection.execute(
            """INSERT INTO asset(asset_id,task_id,asset_type,normalized_value,scope_status,confidence)
               VALUES('a1','t','domain','old.example.com','confirmed',0.9)""")

    def _asset(value, status="confirmed"):
        return {"asset_type": "domain", "normalized_value": value, "scope_status": status,
                "confidence": 0.9, "architecture_status": "unknown", "current_status": "current"}

    first = save_baseline(db, "t", "s")
    with db.connect() as connection:
        connection.execute("DELETE FROM asset WHERE asset_id='a1'")
        connection.execute(
            """INSERT INTO asset(asset_id,task_id,asset_type,normalized_value,scope_status,confidence)
               VALUES('a2','t','domain','new.example.com','confirmed',0.9)""")
    second = save_baseline(db, "t", "s")

    # baseline=first, against=second：old 消失，new 新增
    diff = diff_checkpoints(db, "t", first, second)
    assert [item["normalized_value"] for item in diff.disappeared] == ["old.example.com"]
    assert [item["normalized_value"] for item in diff.added] == ["new.example.com"]
    assert diff.scope_version_match is True

    # against 缺省=当前资产表
    current_diff = diff_checkpoints(db, "t", second)
    assert current_diff.added == []
    assert current_diff.disappeared == []

    # checkpoint 不属于该 task → 拒绝
    import pytest

    with pytest.raises(ValueError, match="checkpoint_not_found"):
        diff_checkpoints(db, "t", "checkpoint-does-not-exist")


def test_delegated_subtask_insert_is_idempotent(tmp_path):
    """D13：coordinator 走 delegation.create_subtask 后幂等语义不变。"""
    from surface_intel.coordinator import _ensure_delegated_subtask

    db = Database(tmp_path / "task.sqlite")
    db.initialize()
    with db.connect() as connection:
        connection.execute("INSERT INTO task(task_id,status,schema_version,scope_version) VALUES('t','planned','1.0','s')")
    first = _ensure_delegated_subtask(db, "t", "mock_passive", "s")
    second = _ensure_delegated_subtask(db, "t", "mock_passive", "s")
    other = _ensure_delegated_subtask(db, "t", "mock_fofa", "s")
    assert first == second
    assert first != other
    rows = list(db.rows("SELECT subtask_id FROM subtask"))
    assert len(rows) == 2
