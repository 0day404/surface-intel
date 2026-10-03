from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from . import SCHEMA_VERSION
from .config import Config
from .coordinator import run_offline_task
from .adapters.base import TaskContext
from .adapters.dns import DNS_RECORD_TYPES, DnsAdapter
from .dns_client import configured_dns_client
from .dns_runner import run_dns_lookup
from .adapters.ct import CertificateTransparencyAdapter
from .ct_client import configured_ct_client
from .ct_runner import run_ct_search
from .adapters.rdap import TARGET_KINDS, RdapAdapter
from .rdap_client import configured_rdap_client
from .rdap_runner import run_rdap_lookup
from .fofa_client import configured_fofa_client
from .fofa_runner import run_fofa_query
from .logging import configure_logging
from .normalization import normalize_targets
from .planning import build_passive_plan, persist_actions, serialize_plan
from .policy.scope import Scope
from .state.database import Database
from .reporting.export import build_report
from .state.recovery import create_checkpoint, latest_checkpoint, recover_started_actions, set_task_status
from .states import TaskStatus


def _config(args: argparse.Namespace) -> Config:
    return Config.from_task_root(args.task_root)


def cmd_init(args: argparse.Namespace) -> int:
    config = _config(args)
    config.write()
    db = Database(config.database_path)
    db.initialize()
    print(json.dumps({"ok": True, **db.health()}, ensure_ascii=True))
    return 0


def cmd_create(args: argparse.Namespace) -> int:
    config = _config(args)
    db = Database(config.database_path)
    db.initialize()
    task_id = f"task-{uuid.uuid4().hex[:16]}"
    try:
        scope = json.loads(Path(args.scope).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": "invalid_scope_file", "detail": type(exc).__name__}))
        return 2
    confirmed = bool(scope.get("confirmed", False))
    if not scope.get("authorized_targets") and not scope.get("authorized_domains") and not scope.get("authorized_ips"):
        print(json.dumps({"ok": False, "error": "scope_has_no_authorized_target"}))
        return 2
    status = TaskStatus.CREATED if confirmed else TaskStatus.AWAITING_SCOPE
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO task(task_id,status,schema_version,scope_version) VALUES(?,?,?,?)",
            (task_id, status.value, SCHEMA_VERSION, scope.get("scope_version", "scope-v0001")),
        )
        connection.execute(
            "INSERT INTO scope_snapshot(scope_id,task_id,scope_version,payload_json,confirmed) VALUES(?,?,?,?,?)",
            (f"scope-{uuid.uuid4().hex[:16]}", task_id, scope.get("scope_version", "scope-v0001"),
             json.dumps(scope, ensure_ascii=True), int(confirmed)),
        )
    print(json.dumps({"ok": True, "task_id": task_id, "status": status.value}))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    db = Database(_config(args).database_path)
    row = next(db.rows("SELECT * FROM task WHERE task_id=?", (args.task_id,)), None)
    if row is None:
        print(json.dumps({"ok": False, "error": "task_not_found"}))
        return 2
    print(json.dumps({"ok": True, "task": dict(row)}, ensure_ascii=True))
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    db = Database(_config(args).database_path)
    row = next(db.rows("SELECT * FROM task WHERE task_id=?", (args.task_id,)), None)
    if row is None:
        print(json.dumps({"ok": False, "error": "task_not_found"}))
        return 2
    scope = next(db.rows("SELECT * FROM scope_snapshot WHERE task_id=?", (args.task_id,)), None)
    confirmed = bool(scope["confirmed"]) if scope else False
    result = {
        "ok": confirmed,
        "task_id": args.task_id,
        "status": "valid" if confirmed else "needs_review",
        "external_calls_allowed": False if not confirmed else "not_implemented",
    }
    print(json.dumps(result, ensure_ascii=True))
    return 0 if confirmed else 2


def cmd_preflight(args: argparse.Namespace) -> int:
    db = Database(_config(args).database_path)
    task = next(db.rows("SELECT * FROM task WHERE task_id=?", (args.task_id,)), None)
    scope_row = next(db.rows("SELECT * FROM scope_snapshot WHERE task_id=? ORDER BY created_at DESC", (args.task_id,)), None)
    if task is None or scope_row is None:
        print(json.dumps({"ok": False, "error": "task_not_found"}))
        return 2
    scope = Scope.from_dict(json.loads(scope_row["payload_json"]))
    targets = list(scope.authorized_targets) + list(scope.authorized_domains) + list(scope.authorized_ips)
    result = {
        "ok": scope.confirmed and bool(targets),
        "task_id": args.task_id,
        "scope_version": scope.scope_version,
        "normalized_targets": normalize_targets(targets),
        "external_calls_allowed": False,
        "reasons": [] if scope.confirmed else ["scope_not_confirmed"],
    }
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result["ok"] else 2


def cmd_plan(args: argparse.Namespace) -> int:
    db = Database(_config(args).database_path)
    task = next(db.rows("SELECT * FROM task WHERE task_id=?", (args.task_id,)), None)
    scope_row = next(db.rows("SELECT * FROM scope_snapshot WHERE task_id=? ORDER BY created_at DESC", (args.task_id,)), None)
    if task is None or scope_row is None:
        print(json.dumps({"ok": False, "error": "task_not_found"}))
        return 2
    scope = Scope.from_dict(json.loads(scope_row["payload_json"]))
    if not scope.confirmed:
        print(json.dumps({"ok": False, "status": "blocked", "reason": "scope_not_confirmed"}))
        return 2
    targets = list(scope.authorized_targets) + list(scope.authorized_domains) + list(scope.authorized_ips)
    plan = build_passive_plan(args.task_id, scope, targets)
    inserted = persist_actions(db, args.task_id, scope, plan)
    plan_path = _config(args).task_root / "plans"
    plan_path.mkdir(parents=True, exist_ok=True)
    (plan_path / f"plan-{scope.scope_version}.json").write_text(serialize_plan(plan), encoding="utf-8")
    with db.connect() as connection:
        connection.execute("UPDATE task SET status=?, updated_at=CURRENT_TIMESTAMP WHERE task_id=?", (TaskStatus.PLANNED.value, args.task_id))
    print(json.dumps({"ok": True, "task_id": args.task_id, "actions_inserted": inserted, "queries": len(plan)}))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    db = Database(_config(args).database_path)
    try:
        result = asyncio.run(run_offline_task(db, args.task_id, mode=args.mode))
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2
    print(json.dumps({"ok": True, "mode": args.mode, **result}, ensure_ascii=True))
    return 0


def cmd_fofa_health(args: argparse.Namespace) -> int:
    if not args.real:
        print(json.dumps({"ok": False, "error": "real_flag_required"}, ensure_ascii=True))
        return 2
    client = configured_fofa_client()
    async def call():
        try:
            return await client.call_tool("fofa_health", {})
        finally:
            await client.close()
    try:
        result = asyncio.run(call())
    except Exception as exc:
        print(json.dumps({"ok": False, "status": "not_configured", "error": type(exc).__name__}, ensure_ascii=True))
        return 2
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result.get("ok", False) else 2


def cmd_fofa_search(args: argparse.Namespace) -> int:
    if not args.real or not args.confirm_real:
        print(json.dumps({"ok": False, "error": "real_and_confirm_real_required"}, ensure_ascii=True))
        return 2
    db = Database(_config(args).database_path)
    db.initialize()
    task = next(db.rows("SELECT * FROM task WHERE task_id=?", (args.task_id,)), None)
    scope_row = next(db.rows(
        "SELECT * FROM scope_snapshot WHERE task_id=? ORDER BY created_at DESC", (args.task_id,)
    ), None)
    if task is None or scope_row is None or not scope_row["confirmed"]:
        print(json.dumps({"ok": False, "error": "scope_not_confirmed"}, ensure_ascii=True))
        return 2
    scope = Scope.from_dict(json.loads(scope_row["payload_json"]))
    if not scope.is_in_scope(args.domain):
        print(json.dumps({"ok": False, "error": "target_out_of_scope"}, ensure_ascii=True))
        return 2
    with db.connect() as connection:
        connection.execute(
            "UPDATE task SET status=?, updated_at=CURRENT_TIMESTAMP WHERE task_id=?",
            (TaskStatus.COLLECTING_FOFA.value, args.task_id),
        )
    client = configured_fofa_client()
    async def call():
        try:
            return await run_fofa_query(
                db,
                client,
                task_id=args.task_id,
                scope_version=scope.scope_version,
                query=f'domain="{args.domain}"',
                context=TaskContext(args.task_id, scope.scope_version, {"scope_confirmed": True}),
                size=args.size,
            )
        finally:
            await client.close()
    try:
        result = asyncio.run(call())
    except Exception as exc:
        print(json.dumps({"ok": False, "status": "failed", "error": type(exc).__name__}, ensure_ascii=True))
        return 2
    final_status = (
        TaskStatus.COMPLETED.value if result.status in {"completed", "already_completed"}
        else TaskStatus.PARTIAL.value if result.status == "partial"
        else TaskStatus.FAILED.value
    )
    with db.connect() as connection:
        connection.execute(
            "UPDATE task SET status=?, updated_at=CURRENT_TIMESTAMP WHERE task_id=?",
            (final_status, args.task_id),
        )
    print(json.dumps({
        "ok": result.status == "completed",
        "status": result.status,
        "observations": result.observations,
        "pages": result.pages,
        "batch_id": result.batch_id,
        "consumed_fpoints": result.consumed_fpoints,
        "required_fpoints": result.required_fpoints,
        "cost_units": result.cost_units,
        "error": result.error,
    }, ensure_ascii=True))
    return 0 if result.status == "completed" else 2


def cmd_dns_health(args: argparse.Namespace) -> int:
    if not args.real:
        print(json.dumps({"ok": False, "error": "real_flag_required"}))
        return 2
    try:
        client = configured_dns_client()
    except Exception as exc:
        print(json.dumps({"ok": False, "status": "not_configured", "error": type(exc).__name__}))
        return 2

    async def call():
        return await DnsAdapter(client).health(TaskContext("dns-health", "health"))

    result = asyncio.run(call())
    detail = result.observations[0] if result.observations else {}
    print(json.dumps({"ok": result.status == "completed", "status": result.status, "detail": detail}, ensure_ascii=True))
    return 0 if result.status == "completed" else 2


def cmd_dns_lookup(args: argparse.Namespace) -> int:
    if not args.real or not args.confirm_real:
        print(json.dumps({"ok": False, "error": "real_and_confirm_real_required"}))
        return 2
    record_types = [item.strip().upper() for item in args.types.split(",") if item.strip()]
    invalid = [item for item in record_types if item not in DNS_RECORD_TYPES]
    if invalid:
        print(json.dumps({"ok": False, "error": "invalid_record_type", "detail": invalid}))
        return 2
    db = Database(_config(args).database_path)
    db.initialize()
    task = next(db.rows("SELECT * FROM task WHERE task_id=?", (args.task_id,)), None)
    scope_row = next(db.rows(
        "SELECT * FROM scope_snapshot WHERE task_id=? ORDER BY created_at DESC", (args.task_id,)
    ), None)
    if task is None or scope_row is None or not scope_row["confirmed"]:
        print(json.dumps({"ok": False, "error": "scope_not_confirmed"}))
        return 2
    scope = Scope.from_dict(json.loads(scope_row["payload_json"]))
    if not scope.is_in_scope(args.domain):
        print(json.dumps({"ok": False, "error": "target_out_of_scope"}))
        return 2
    with db.connect() as connection:
        connection.execute(
            "UPDATE task SET status=?, updated_at=CURRENT_TIMESTAMP WHERE task_id=?",
            (TaskStatus.COLLECTING_PASSIVE.value, args.task_id),
        )
    client = configured_dns_client()

    async def call():
        return await run_dns_lookup(
            db, client, task_id=args.task_id, scope=scope,
            domain=args.domain, record_types=record_types,
        )

    try:
        result = asyncio.run(call())
    except Exception as exc:
        print(json.dumps({"ok": False, "status": "failed", "error": type(exc).__name__}))
        return 2
    final_status = {
        "completed": TaskStatus.COMPLETED.value,
        "partial": TaskStatus.PARTIAL.value,
        "blocked": TaskStatus.BLOCKED.value,
    }.get(result.status, TaskStatus.FAILED.value)
    with db.connect() as connection:
        connection.execute(
            "UPDATE task SET status=?, updated_at=CURRENT_TIMESTAMP WHERE task_id=?",
            (final_status, args.task_id),
        )
    print(json.dumps({
        "ok": result.status == "completed",
        "status": result.status,
        "observations": result.observations,
        "queries": result.queries,
        "error": result.error,
    }, ensure_ascii=True))
    return 0 if result.status == "completed" else 2


def cmd_ct_health(args: argparse.Namespace) -> int:
    if not args.real:
        print(json.dumps({"ok": False, "error": "real_flag_required"}))
        return 2
    try:
        client = configured_ct_client()
    except Exception as exc:
        print(json.dumps({"ok": False, "status": "not_configured", "error": type(exc).__name__}))
        return 2

    async def call():
        return await CertificateTransparencyAdapter(client).health(TaskContext("ct-health", "health"))

    result = asyncio.run(call())
    detail = result.observations[0] if result.observations else {}
    print(json.dumps({"ok": result.status == "completed", "status": result.status, "detail": detail}, ensure_ascii=True))
    return 0 if result.status == "completed" else 2


def cmd_ct_search(args: argparse.Namespace) -> int:
    if not args.real or not args.confirm_real:
        print(json.dumps({"ok": False, "error": "real_and_confirm_real_required"}))
        return 2
    db = Database(_config(args).database_path)
    db.initialize()
    task = next(db.rows("SELECT * FROM task WHERE task_id=?", (args.task_id,)), None)
    scope_row = next(db.rows(
        "SELECT * FROM scope_snapshot WHERE task_id=? ORDER BY created_at DESC", (args.task_id,)
    ), None)
    if task is None or scope_row is None or not scope_row["confirmed"]:
        print(json.dumps({"ok": False, "error": "scope_not_confirmed"}))
        return 2
    scope = Scope.from_dict(json.loads(scope_row["payload_json"]))
    if not scope.is_in_scope(args.domain):
        print(json.dumps({"ok": False, "error": "target_out_of_scope"}))
        return 2
    with db.connect() as connection:
        connection.execute(
            "UPDATE task SET status=?, updated_at=CURRENT_TIMESTAMP WHERE task_id=?",
            (TaskStatus.COLLECTING_PASSIVE.value, args.task_id),
        )
    client = configured_ct_client()

    async def call():
        return await run_ct_search(db, client, task_id=args.task_id, scope=scope, domain=args.domain)

    try:
        result = asyncio.run(call())
    except Exception as exc:
        print(json.dumps({"ok": False, "status": "failed", "error": type(exc).__name__}))
        return 2
    final_status = {
        "completed": TaskStatus.COMPLETED.value,
        "partial": TaskStatus.PARTIAL.value,
        "blocked": TaskStatus.BLOCKED.value,
    }.get(result.status, TaskStatus.FAILED.value)
    with db.connect() as connection:
        connection.execute(
            "UPDATE task SET status=?, updated_at=CURRENT_TIMESTAMP WHERE task_id=?",
            (final_status, args.task_id),
        )
    print(json.dumps({
        "ok": result.status == "completed",
        "status": result.status,
        "observations": result.observations,
        "certificates": result.certificates,
        "error": result.error,
    }, ensure_ascii=True))
    return 0 if result.status == "completed" else 2


def cmd_rdap_health(args: argparse.Namespace) -> int:
    if not args.real:
        print(json.dumps({"ok": False, "error": "real_flag_required"}))
        return 2
    try:
        client = configured_rdap_client()
    except Exception as exc:
        print(json.dumps({"ok": False, "status": "not_configured", "error": type(exc).__name__}))
        return 2

    async def call():
        return await RdapAdapter(client).health(TaskContext("rdap-health", "health"))

    result = asyncio.run(call())
    detail = result.observations[0] if result.observations else {}
    print(json.dumps({"ok": result.status == "completed", "status": result.status, "detail": detail}, ensure_ascii=True))
    return 0 if result.status == "completed" else 2


def cmd_rdap_lookup(args: argparse.Namespace) -> int:
    if not args.real or not args.confirm_real:
        print(json.dumps({"ok": False, "error": "real_and_confirm_real_required"}))
        return 2
    kind = args.kind.strip().lower()
    if kind not in TARGET_KINDS:
        print(json.dumps({"ok": False, "error": "invalid_target_kind", "detail": kind}))
        return 2
    db = Database(_config(args).database_path)
    db.initialize()
    task = next(db.rows("SELECT * FROM task WHERE task_id=?", (args.task_id,)), None)
    scope_row = next(db.rows(
        "SELECT * FROM scope_snapshot WHERE task_id=? ORDER BY created_at DESC", (args.task_id,)
    ), None)
    if task is None or scope_row is None or not scope_row["confirmed"]:
        print(json.dumps({"ok": False, "error": "scope_not_confirmed"}))
        return 2
    scope = Scope.from_dict(json.loads(scope_row["payload_json"]))
    if kind != "asn" and not scope.is_in_scope(args.target):
        # ASN authorization (authorized_asns entries or a merge binding) is
        # decided inside the runner
        print(json.dumps({"ok": False, "error": "target_out_of_scope"}))
        return 2
    with db.connect() as connection:
        connection.execute(
            "UPDATE task SET status=?, updated_at=CURRENT_TIMESTAMP WHERE task_id=?",
            (TaskStatus.COLLECTING_PASSIVE.value, args.task_id),
        )
    client = configured_rdap_client()

    async def call():
        return await run_rdap_lookup(
            db, client, task_id=args.task_id, scope=scope, target=args.target, kind=kind,
        )

    try:
        result = asyncio.run(call())
    except Exception as exc:
        print(json.dumps({"ok": False, "status": "failed", "error": type(exc).__name__}))
        return 2
    final_status = {
        "completed": TaskStatus.COMPLETED.value,
        "partial": TaskStatus.PARTIAL.value,
        "blocked": TaskStatus.BLOCKED.value,
    }.get(result.status, TaskStatus.FAILED.value)
    with db.connect() as connection:
        connection.execute(
            "UPDATE task SET status=?, updated_at=CURRENT_TIMESTAMP WHERE task_id=?",
            (final_status, args.task_id),
        )
    print(json.dumps({
        "ok": result.status == "completed",
        "status": result.status,
        "observations": result.observations,
        "error": result.error,
    }, ensure_ascii=True))
    return 0 if result.status == "completed" else 2


def cmd_merge(args: argparse.Namespace) -> int:
    from surface_intel.architecture import annotate_architecture
    from surface_intel.merger import merge_task_observations

    db = Database(_config(args).database_path)
    db.initialize()
    task = next(db.rows("SELECT * FROM task WHERE task_id=?", (args.task_id,)), None)
    scope_row = next(db.rows(
        "SELECT * FROM scope_snapshot WHERE task_id=? ORDER BY created_at DESC", (args.task_id,)
    ), None)
    if task is None or scope_row is None:
        print(json.dumps({"ok": False, "error": "task_or_scope_not_found"}))
        return 2
    scope = Scope.from_dict(json.loads(scope_row["payload_json"]))
    summary = merge_task_observations(db, args.task_id, scope)
    architecture = annotate_architecture(db, args.task_id)
    print(json.dumps({
        "ok": summary.status == "completed" and architecture.status == "completed",
        "task_id": args.task_id,
        "observations_processed": summary.observations_processed,
        "observations_skipped": summary.observations_skipped,
        "assets_created": summary.assets_created,
        "assets_updated": summary.assets_updated,
        "relations_created": summary.relations_created,
        "relations_updated": summary.relations_updated,
        "conflicts_created": summary.conflicts_created,
        "architecture_classified": architecture.assets_classified,
        "architecture_third_party": architecture.assets_third_party,
        "skipped": list(summary.skipped),
    }, ensure_ascii=True))
    return 0 if summary.status == "completed" and architecture.status == "completed" else 2


def cmd_http_observe(args: argparse.Namespace) -> int:
    from surface_intel.safe_http_client import SafeHttpFetchClient
    from surface_intel.safe_http_runner import run_http_observe

    if not args.real or not args.confirm_real:
        print(json.dumps({"ok": False, "error": "real_and_confirm_real_required"}))
        return 2
    db = Database(_config(args).database_path)
    db.initialize()
    scope_row = next(db.rows(
        "SELECT * FROM scope_snapshot WHERE task_id=? ORDER BY created_at DESC", (args.task_id,)
    ), None)
    if scope_row is None or not scope_row["confirmed"]:
        print(json.dumps({"ok": False, "error": "scope_not_confirmed"}))
        return 2
    scope = Scope.from_dict(json.loads(scope_row["payload_json"]))
    host = (urlsplit(args.url).hostname or "").lower()
    if not host or not scope.is_in_scope(host):
        print(json.dumps({"ok": False, "error": "target_out_of_scope"}))
        return 2
    if not scope.active_testing_allowed or scope.active_testing_level.value == "passive_only":
        print(json.dumps({"ok": False, "error": "active_testing_not_authorized"}))
        return 2
    with db.connect() as connection:
        connection.execute(
            "UPDATE task SET status=?, updated_at=CURRENT_TIMESTAMP WHERE task_id=?",
            (TaskStatus.PROBING_AUTHORIZED.value, args.task_id),
        )
    client = SafeHttpFetchClient()

    async def call():
        return await run_http_observe(db, client, task_id=args.task_id, scope=scope,
                                      url=args.url, method=args.method)

    try:
        result = asyncio.run(call())
    except Exception as exc:
        print(json.dumps({"ok": False, "status": "failed", "error": type(exc).__name__}))
        return 2
    print(json.dumps({
        "ok": result.status == "completed",
        "status": result.status,
        "observations": result.observations,
        "error": result.error,
        "review_created": result.review_created,
    }, ensure_ascii=True))
    return 0 if result.status == "completed" else 2


def cmd_port_check(args: argparse.Namespace) -> int:
    from surface_intel.safe_port_client import SafePortProbeClient
    from surface_intel.safe_port_runner import run_port_check

    if not args.real or not args.confirm_real:
        print(json.dumps({"ok": False, "error": "real_and_confirm_real_required"}))
        return 2
    db = Database(_config(args).database_path)
    db.initialize()
    scope_row = next(db.rows(
        "SELECT * FROM scope_snapshot WHERE task_id=? ORDER BY created_at DESC", (args.task_id,)
    ), None)
    if scope_row is None or not scope_row["confirmed"]:
        print(json.dumps({"ok": False, "error": "scope_not_confirmed"}))
        return 2
    scope = Scope.from_dict(json.loads(scope_row["payload_json"]))
    host = args.target.strip().lower().rstrip(".")
    if not host or not scope.is_in_scope(host):
        print(json.dumps({"ok": False, "error": "target_out_of_scope"}))
        return 2
    if not scope.active_testing_allowed or scope.active_testing_level.value == "passive_only":
        print(json.dumps({"ok": False, "error": "active_testing_not_authorized"}))
        return 2
    with db.connect() as connection:
        connection.execute(
            "UPDATE task SET status=?, updated_at=CURRENT_TIMESTAMP WHERE task_id=?",
            (TaskStatus.PROBING_AUTHORIZED.value, args.task_id),
        )
    client = SafePortProbeClient()

    async def call():
        return await run_port_check(db, client, task_id=args.task_id, scope=scope,
                                    target=args.target, port=args.port)

    try:
        result = asyncio.run(call())
    except Exception as exc:
        print(json.dumps({"ok": False, "status": "failed", "error": type(exc).__name__}))
        return 2
    print(json.dumps({
        "ok": result.status == "completed",
        "status": result.status,
        "observations": result.observations,
        "error": result.error,
        "review_created": result.review_created,
    }, ensure_ascii=True))
    return 0 if result.status == "completed" else 2


def cmd_browser_observe(args: argparse.Namespace) -> int:
    from surface_intel.playwright_client import configured_playwright_client
    from surface_intel.playwright_runner import run_browser_observe

    if not args.real or not args.confirm_real:
        print(json.dumps({"ok": False, "error": "real_and_confirm_real_required"}))
        return 2
    db = Database(_config(args).database_path)
    db.initialize()
    scope_row = next(db.rows(
        "SELECT * FROM scope_snapshot WHERE task_id=? ORDER BY created_at DESC", (args.task_id,)
    ), None)
    if scope_row is None or not scope_row["confirmed"]:
        print(json.dumps({"ok": False, "error": "scope_not_confirmed"}))
        return 2
    scope = Scope.from_dict(json.loads(scope_row["payload_json"]))
    host = urlsplit(args.url).hostname or ""
    if not host or not scope.is_in_scope(host):
        print(json.dumps({"ok": False, "error": "target_out_of_scope"}))
        return 2
    if not scope.active_testing_allowed or scope.active_testing_level.value == "passive_only":
        print(json.dumps({"ok": False, "error": "active_testing_not_authorized"}))
        return 2
    with db.connect() as connection:
        connection.execute(
            "UPDATE task SET status=?, updated_at=CURRENT_TIMESTAMP WHERE task_id=?",
            (TaskStatus.OBSERVING_BROWSER.value, args.task_id),
        )
    client = configured_playwright_client()

    async def call():
        return await run_browser_observe(db, client, task_id=args.task_id, scope=scope,
                                         url=args.url, max_requests=args.max_requests)

    try:
        result = asyncio.run(call())
    except Exception as exc:
        print(json.dumps({"ok": False, "status": "failed", "error": type(exc).__name__}))
        return 2
    print(json.dumps({
        "ok": result.status == "completed",
        "status": result.status,
        "observations": result.observations,
        "error": result.error,
        "review_created": result.review_created,
    }, ensure_ascii=True))
    return 0 if result.status == "completed" else 2


def cmd_baseline_save(args: argparse.Namespace) -> int:
    from surface_intel.monitor import save_baseline

    db = Database(_config(args).database_path)
    task = next(db.rows("SELECT scope_version FROM task WHERE task_id=?", (args.task_id,)), None)
    if task is None:
        print(json.dumps({"ok": False, "error": "task_not_found"}))
        return 2
    checkpoint_id = save_baseline(db, args.task_id, task["scope_version"])
    print(json.dumps({"ok": True, "task_id": args.task_id, "checkpoint_id": checkpoint_id}))
    return 0


def cmd_baseline_diff(args: argparse.Namespace) -> int:
    from dataclasses import asdict

    from surface_intel.monitor import diff_against_baseline

    db = Database(_config(args).database_path)
    task = next(db.rows("SELECT scope_version FROM task WHERE task_id=?", (args.task_id,)), None)
    if task is None:
        print(json.dumps({"ok": False, "error": "task_not_found"}))
        return 2
    diff = diff_against_baseline(db, args.task_id, task["scope_version"])
    print(json.dumps({"ok": True, **asdict(diff)}, ensure_ascii=True))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    db = Database(_config(args).database_path)
    try:
        report = build_report(db, args.task_id, _config(args).task_root / "reports")
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2
    print(json.dumps({"ok": True, "integrity_status": report["integrity_status"],
                      "summary": report["summary"], "export": report["export"]}, ensure_ascii=True))
    return 0


def cmd_checkpoint(args: argparse.Namespace) -> int:
    db = Database(_config(args).database_path)
    if next(db.rows("SELECT task_id FROM task WHERE task_id=?", (args.task_id,)), None) is None:
        print(json.dumps({"ok": False, "error": "task_not_found"}))
        return 2
    checkpoint_id = create_checkpoint(
        db,
        args.task_id,
        phase=args.phase,
        payload={"reason": args.reason, "manual": True},
    )
    print(json.dumps({"ok": True, "checkpoint_id": checkpoint_id}))
    return 0


def cmd_recover(args: argparse.Namespace) -> int:
    db = Database(_config(args).database_path)
    if next(db.rows("SELECT task_id FROM task WHERE task_id=?", (args.task_id,)), None) is None:
        print(json.dumps({"ok": False, "error": "task_not_found"}))
        return 2
    recovered = recover_started_actions(db, args.task_id)
    checkpoint = latest_checkpoint(db, args.task_id)
    print(json.dumps({"ok": True, "recovered_actions": recovered, "checkpoint": checkpoint}, ensure_ascii=True))
    return 0


def _task_control(args: argparse.Namespace, status: TaskStatus) -> int:
    db = Database(_config(args).database_path)
    if next(db.rows("SELECT task_id FROM task WHERE task_id=?", (args.task_id,)), None) is None:
        print(json.dumps({"ok": False, "error": "task_not_found"}))
        return 2
    set_task_status(db, args.task_id, status, args.reason)
    print(json.dumps({"ok": True, "task_id": args.task_id, "status": status.value}))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="surface-intel")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, handler in (("init", cmd_init), ("create", cmd_create), ("preflight", cmd_preflight), ("plan", cmd_plan), ("run", cmd_run), ("fofa-health", cmd_fofa_health), ("fofa-search", cmd_fofa_search), ("dns-health", cmd_dns_health), ("dns-lookup", cmd_dns_lookup), ("ct-health", cmd_ct_health), ("ct-search", cmd_ct_search), ("rdap-health", cmd_rdap_health), ("rdap-lookup", cmd_rdap_lookup), ("merge", cmd_merge), ("http-observe", cmd_http_observe), ("port-check", cmd_port_check), ("browser-observe", cmd_browser_observe), ("baseline-save", cmd_baseline_save), ("baseline-diff", cmd_baseline_diff), ("report", cmd_report), ("checkpoint", cmd_checkpoint), ("recover", cmd_recover), ("pause", lambda args: _task_control(args, TaskStatus.PAUSED)), ("resume", lambda args: _task_control(args, TaskStatus.PLANNED)), ("cancel", lambda args: _task_control(args, TaskStatus.CANCELLED)), ("status", cmd_status), ("validate", cmd_validate)):
        command = sub.add_parser(name)
        command.set_defaults(handler=handler)
        if name == "init":
            command.add_argument("task_root")
        elif name not in {"fofa-health", "dns-health", "ct-health", "rdap-health"}:
            command.add_argument("--task-root", required=True)
        if name == "create":
            command.add_argument("--input", required=False)
            command.add_argument("--scope", required=True)
        if name == "run":
            command.add_argument("--mode", choices=("serial", "delegated", "hybrid"), default="serial")
        if name == "fofa-health":
            command.add_argument("--real", action="store_true")
        if name == "dns-health":
            command.add_argument("--real", action="store_true")
        if name == "ct-health":
            command.add_argument("--real", action="store_true")
        if name == "rdap-health":
            command.add_argument("--real", action="store_true")
        if name == "fofa-search":
            command.add_argument("task_id")
            command.add_argument("--domain", required=True)
            command.add_argument("--size", type=int, default=100)
            command.add_argument("--real", action="store_true")
            command.add_argument("--confirm-real", action="store_true")
        if name == "dns-lookup":
            command.add_argument("task_id")
            command.add_argument("--domain", required=True)
            command.add_argument("--types", default=",".join(DNS_RECORD_TYPES))
            command.add_argument("--real", action="store_true")
            command.add_argument("--confirm-real", action="store_true")
        if name == "ct-search":
            command.add_argument("task_id")
            command.add_argument("--domain", required=True)
            command.add_argument("--real", action="store_true")
            command.add_argument("--confirm-real", action="store_true")
        if name == "rdap-lookup":
            command.add_argument("task_id")
            command.add_argument("--target", required=True)
            command.add_argument("--kind", choices=TARGET_KINDS, default="domain")
            command.add_argument("--real", action="store_true")
            command.add_argument("--confirm-real", action="store_true")
        if name == "http-observe":
            command.add_argument("task_id")
            command.add_argument("--url", required=True)
            command.add_argument("--method", default="GET", choices=("GET", "HEAD"))
            command.add_argument("--real", action="store_true")
            command.add_argument("--confirm-real", action="store_true")
        if name == "port-check":
            command.add_argument("task_id")
            command.add_argument("--target", required=True)
            command.add_argument("--port", type=int, required=True)
            command.add_argument("--real", action="store_true")
            command.add_argument("--confirm-real", action="store_true")
        if name == "browser-observe":
            command.add_argument("task_id")
            command.add_argument("--url", required=True)
            command.add_argument("--max-requests", type=int, default=100)
            command.add_argument("--real", action="store_true")
            command.add_argument("--confirm-real", action="store_true")
        if name in {"status", "validate", "preflight", "plan", "run", "report", "checkpoint", "recover", "pause", "resume", "cancel", "merge", "baseline-save", "baseline-diff"}:
            command.add_argument("task_id")
        if name == "checkpoint":
            command.add_argument("--phase", default="manual")
            command.add_argument("--reason", default="")
        if name in {"pause", "cancel", "resume"}:
            command.add_argument("--reason", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging()
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
