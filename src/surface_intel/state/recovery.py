from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from ..states import ActionStatus, TaskStatus
from .database import Database


def create_checkpoint(
    db: Database,
    task_id: str,
    *,
    phase: str,
    payload: dict[str, Any],
    status: str = "valid",
) -> str:
    checkpoint_id = f"checkpoint-{uuid.uuid4().hex[:16]}"
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO checkpoint(checkpoint_id,task_id,phase,status,payload_json) VALUES(?,?,?,?,?)",
            (checkpoint_id, task_id, phase, status, json.dumps(payload, ensure_ascii=True, sort_keys=True)),
        )
        connection.execute(
            "INSERT INTO event(event_id,task_id,event_type,payload_json) VALUES(?,?,?,?)",
            (f"event-{uuid.uuid4().hex[:16]}", task_id, "checkpoint_created",
             json.dumps({"checkpoint_id": checkpoint_id, "phase": phase}, ensure_ascii=True)),
        )
    return checkpoint_id


def latest_checkpoint(db: Database, task_id: str) -> dict[str, Any] | None:
    row = next(
        db.rows(
            "SELECT * FROM checkpoint WHERE task_id=? AND status='valid' ORDER BY created_at DESC LIMIT 1",
            (task_id,),
        ),
        None,
    )
    if row is None:
        return None
    value = dict(row)
    value["payload"] = json.loads(value.pop("payload_json"))
    return value


def recover_started_actions(db: Database, task_id: str) -> list[str]:
    rows = list(db.rows(
        "SELECT action_id FROM action WHERE task_id=? AND status=?",
        (task_id, ActionStatus.STARTED.value),
    ))
    recovered: list[str] = []
    with db.connect() as connection:
        for row in rows:
            connection.execute(
                """UPDATE action
                   SET status=?, error_code=?, completed_at=?, updated_at=CURRENT_TIMESTAMP
                   WHERE action_id=? AND status=?""",
                (
                    ActionStatus.UNKNOWN.value,
                    "external_state_unknown",
                    datetime.now(timezone.utc).isoformat(),
                    row["action_id"],
                    ActionStatus.STARTED.value,
                ),
            )
            connection.execute(
                "INSERT INTO event(event_id,task_id,event_type,payload_json) VALUES(?,?,?,?)",
                (
                    f"event-{uuid.uuid4().hex[:16]}",
                    task_id,
                    "action_recovered_unknown",
                    json.dumps({"action_id": row["action_id"]}, ensure_ascii=True),
                ),
            )
            recovered.append(row["action_id"])
    return recovered


def set_task_status(db: Database, task_id: str, status: TaskStatus, reason: str = "") -> None:
    with db.connect() as connection:
        connection.execute(
            "UPDATE task SET status=?, updated_at=CURRENT_TIMESTAMP WHERE task_id=?",
            (status.value, task_id),
        )
        connection.execute(
            "INSERT INTO event(event_id,task_id,event_type,payload_json) VALUES(?,?,?,?)",
            (
                f"event-{uuid.uuid4().hex[:16]}",
                task_id,
                f"task_{status.value}",
                json.dumps({"reason": reason}, ensure_ascii=True),
            ),
        )
