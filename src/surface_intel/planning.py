from __future__ import annotations

import hashlib
import json
import uuid
from typing import Iterable

from .policy.scope import Scope
from .state.database import Database
from .states import ActionStatus


def query_fingerprint(query: str) -> str:
    return hashlib.sha256(query.strip().encode("utf-8")).hexdigest()


def build_passive_plan(task_id: str, scope: Scope, targets: Iterable[str]) -> list[dict[str, str]]:
    plan = []
    for target in targets:
        if scope.is_in_scope(target):
            query = f'domain="{target}"'
            plan.append({"purpose": "domain_assets", "target": target, "query": query, "fingerprint": query_fingerprint(query)})
    return plan


def persist_actions(db: Database, task_id: str, scope: Scope, plan: list[dict[str, str]]) -> int:
    inserted = 0
    with db.connect() as connection:
        for item in plan:
            key = f"{task_id}:{item['fingerprint']}:passive"
            cursor = connection.execute(
                """INSERT OR IGNORE INTO action
                   (action_id,task_id,idempotency_key,adapter,status,scope_version,attempt,input_json,phase)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (f"action-{uuid.uuid4().hex[:16]}", task_id, key, "mock_passive",
                 ActionStatus.PLANNED.value, scope.scope_version, 0,
                 json.dumps({"target": item["target"], "capability": "passive_observation"}, ensure_ascii=True),
                 "collecting_passive"),
            )
            inserted += cursor.rowcount
    return inserted


def serialize_plan(plan: list[dict[str, str]]) -> str:
    return json.dumps({"queries": plan}, ensure_ascii=True, sort_keys=True)
