from surface_intel.state.database import Database
from surface_intel.state.fofa import FofaBatchRepository


def test_fofa_batch_and_cursor_are_idempotent_and_resumable(tmp_path):
    db = Database(tmp_path / "task.sqlite")
    db.initialize()
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO task(task_id,status,schema_version,scope_version) VALUES('t','planned','1.0','s')"
        )
    repository = FofaBatchRepository(db)
    batch_id = repository.create_batch("t", "fp", 'domain="example.com"', "s")
    assert repository.create_batch("t", "fp", 'domain="example.com"', "s") == batch_id
    assert repository.start(batch_id)
    assert repository.complete(
        batch_id, request_id="req-1", next_token="n1", cost_units=2, result_count=1
    )
    cursor_id = repository.save_cursor(batch_id, "t", "fp", "n1", 2, "req-1")
    assert cursor_id.startswith("fofa-cursor-")
    assert repository.resume_cursor(batch_id)["next_token"] == "n1"


def test_fofa_batches_fail_independently(tmp_path):
    db = Database(tmp_path / "task.sqlite")
    db.initialize()
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO task(task_id,status,schema_version,scope_version) VALUES('t','planned','1.0','s')"
        )
    repository = FofaBatchRepository(db)
    first = repository.create_batch("t", "fp1", "q1", "s", batch_index=1)
    second = repository.create_batch("t", "fp2", "q2", "s", batch_index=2)
    assert repository.start(first) and repository.start(second)
    assert repository.fail(first, "rate_limited")
    assert next(db.rows("SELECT status FROM fofa_query_batch WHERE batch_id=?", (second,)))["status"] == "started"
