"""Worker-delegation primitives: subtasks, leases, results.

平台预留 API（D13）：0.2.0 未接入生产编排——生产路径是 recon 与各真实 runner。
唯一进程内调用方是 coordinator 的 delegated 演示模式（run --mode delegated）；
lease/result 原语为多 worker 在线编排预留，接口已按幂等键设计完毕。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from .database import Database


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_subtask(
    db: Database,
    *,
    parent_task_id: str,
    role: str,
    phase: str,
    scope_version: str,
    allowed_tools: list[str],
    forbidden_actions: list[str],
    idempotency_key: str,
) -> str:
    subtask_id = f"subtask-{uuid.uuid4().hex[:16]}"
    with db.connect() as connection:
        connection.execute(
            """INSERT INTO subtask
               (subtask_id,parent_task_id,role,phase,scope_version,allowed_tools_json,
                forbidden_actions_json,status,idempotency_key)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (subtask_id, parent_task_id, role, phase, scope_version,
             json.dumps(allowed_tools, ensure_ascii=True),
             json.dumps(forbidden_actions, ensure_ascii=True), "planned", idempotency_key),
        )
    return subtask_id


def acquire_lease(db: Database, subtask_id: str, worker_id: str, ttl_seconds: int = 60) -> str | None:
    now = _now()
    expires = now + timedelta(seconds=ttl_seconds)
    with db.connect() as connection:
        active = connection.execute(
            "SELECT lease_id FROM agent_lease WHERE subtask_id=? AND status='active'",
            (subtask_id,),
        ).fetchone()
        if active:
            return None
        lease_id = f"lease-{uuid.uuid4().hex[:16]}"
        connection.execute(
            """INSERT INTO agent_lease
               (lease_id,subtask_id,worker_id,expires_at,last_heartbeat_at)
               VALUES(?,?,?,?,?)""",
            (lease_id, subtask_id, worker_id, expires.isoformat(), now.isoformat()),
        )
        connection.execute(
            "UPDATE subtask SET status='running', started_at=COALESCE(started_at,CURRENT_TIMESTAMP), last_heartbeat_at=? WHERE subtask_id=?",
            (now.isoformat(), subtask_id),
        )
        return lease_id


def heartbeat(db: Database, lease_id: str, ttl_seconds: int = 60) -> bool:
    now = _now()
    expires = now + timedelta(seconds=ttl_seconds)
    with db.connect() as connection:
        cursor = connection.execute(
            """UPDATE agent_lease SET last_heartbeat_at=?, expires_at=?
               WHERE lease_id=? AND status='active' AND expires_at>?""",
            (now.isoformat(), expires.isoformat(), lease_id, now.isoformat()),
        )
        if cursor.rowcount:
            connection.execute(
                "UPDATE subtask SET last_heartbeat_at=? WHERE subtask_id=(SELECT subtask_id FROM agent_lease WHERE lease_id=?)",
                (now.isoformat(), lease_id),
            )
        return bool(cursor.rowcount)


def expire_leases(db: Database) -> list[str]:
    now = _now().isoformat()
    with db.connect() as connection:
        rows = connection.execute(
            "SELECT lease_id,subtask_id FROM agent_lease WHERE status='active' AND expires_at<=?",
            (now,),
        ).fetchall()
        for row in rows:
            connection.execute("UPDATE agent_lease SET status='expired' WHERE lease_id=?", (row["lease_id"],))
            connection.execute(
                "UPDATE subtask SET status='timed_out', error_code='lease_expired', completed_at=CURRENT_TIMESTAMP WHERE subtask_id=? AND status='running'",
                (row["subtask_id"],),
            )
        return [row["subtask_id"] for row in rows]


def submit_result(
    db: Database,
    *,
    subtask_id: str,
    idempotency_key: str,
    status: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    with db.connect() as connection:
        existing = connection.execute(
            "SELECT result_id,status,payload_json FROM subtask_result WHERE idempotency_key=?",
            (idempotency_key,),
        ).fetchone()
        if existing:
            return {"accepted": False, "duplicate": True, "result_id": existing["result_id"], "status": existing["status"]}
        subtask = connection.execute(
            "SELECT subtask_id,status FROM subtask WHERE subtask_id=?",
            (subtask_id,),
        ).fetchone()
        if subtask is None:
            raise ValueError("subtask_not_found")
        result_id = f"result-{uuid.uuid4().hex[:16]}"
        connection.execute(
            "INSERT INTO subtask_result(result_id,subtask_id,idempotency_key,status,payload_json) VALUES(?,?,?,?,?)",
            (result_id, subtask_id, idempotency_key, status, json.dumps(payload, ensure_ascii=True, sort_keys=True)),
        )
        connection.execute(
            "UPDATE subtask SET status=?,result_ref=?,completed_at=CURRENT_TIMESTAMP WHERE subtask_id=?",
            (status, result_id, subtask_id),
        )
        return {"accepted": True, "duplicate": False, "result_id": result_id, "status": status}
