from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
import warnings
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
from .policy.scope import Scope, scope_warnings
from .state.database import Database
from .reporting.export import build_report, report_output_dir
from .state.recovery import create_checkpoint, latest_checkpoint, recover_started_actions, set_task_status
from .states import TaskStatus


def _config(args: argparse.Namespace) -> Config:
    return Config.from_task_root(args.task_root)


def cmd_init(args: argparse.Namespace) -> int:
    config = _config(args)
    config.write()
    db = Database(config.database_path)
    db.initialize()
    scaffold = None
    if getattr(args, "scaffold", False):
        # D-1: pull up the project skeleton beside the task root
        # (projects/<name>/task -> projects/<name>/{inputs,reports,data} + NOTES.md);
        # idempotent - existing dirs and notes are left untouched
        project_root = Path(args.task_root).parent
        for name in ("inputs", "reports", "data"):
            (project_root / name).mkdir(parents=True, exist_ok=True)
        notes_path = project_root / "NOTES.md"
        if not notes_path.exists():
            notes_path.write_text(
                f"# {project_root.name}\n\n"
                "## 判定口径\n\n(待填写)\n\n"
                "## ROE\n\n(待填写)\n\n"
                "## 轮次\n\n| 轮次 | 日期 | 主题 | 交付物 |\n"
                "| ---- | ---- | ---- | ------ |\n",
                encoding="utf-8")
        scaffold = str(project_root)
    print(json.dumps({"ok": True, **db.health(), "scaffold": scaffold}, ensure_ascii=True))
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
    warnings = scope_warnings(scope)
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
    print(json.dumps({"ok": True, "task_id": task_id, "status": status.value,
                      "warnings": warnings}, ensure_ascii=True))
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
    payload = json.loads(scope["payload_json"]) if scope else {}
    result = {
        "ok": confirmed,
        "task_id": args.task_id,
        "status": "valid" if confirmed else "needs_review",
        # C2: a real boolean - the old "not_implemented" sentinel was wrong,
        # the scope gates have always been live (target_out_of_scope etc.)
        "external_calls_allowed": confirmed,
        "active_testing_allowed": bool(payload.get("active_testing_allowed", False)),
        "active_testing_level": payload.get("active_testing_level", "passive_only"),
    }
    print(json.dumps(result, ensure_ascii=True))
    return 0 if confirmed else 2


def cmd_preflight(args: argparse.Namespace) -> int:
    db = Database(_config(args).database_path)
    task = next(db.rows("SELECT * FROM task WHERE task_id=?", (args.task_id,)), None)
    scope_row = next(db.rows("SELECT * FROM scope_snapshot WHERE task_id=? ORDER BY rowid DESC", (args.task_id,)), None)
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
    scope_row = next(db.rows("SELECT * FROM scope_snapshot WHERE task_id=? ORDER BY rowid DESC", (args.task_id,)), None)
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
        "SELECT * FROM scope_snapshot WHERE task_id=? ORDER BY rowid DESC", (args.task_id,)
    ), None)
    if task is None or scope_row is None or not scope_row["confirmed"]:
        print(json.dumps({"ok": False, "error": "scope_not_confirmed"}, ensure_ascii=True))
        return 2
    scope = Scope.from_dict(json.loads(scope_row["payload_json"]))
    # comma-separated domains run as one OR query (B6); a single value is
    # byte-identical to the old per-domain query
    domains = [item.strip().rstrip(".").lower() for item in str(args.domain).split(",") if item.strip()]
    if not domains:
        print(json.dumps({"ok": False, "error": "domain_required"}, ensure_ascii=True))
        return 2
    out_of_scope = [item for item in domains if not scope.is_in_scope(item)]
    if out_of_scope:
        print(json.dumps({"ok": False, "error": "target_out_of_scope", "domains": out_of_scope},
                         ensure_ascii=True))
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
                query=" || ".join(f'domain="{item}"' for item in domains),
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
        "domains": domains,
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
        "SELECT * FROM scope_snapshot WHERE task_id=? ORDER BY rowid DESC", (args.task_id,)
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
    if getattr(args, "probe", False) and not args.confirm_real:
        # D1: --probe is the only health path that touches the network; it
        # demands the full real-call authorization pair
        print(json.dumps({"ok": False, "error": "real_and_confirm_real_required"}))
        return 2
    try:
        client = configured_ct_client()
    except Exception as exc:
        print(json.dumps({"ok": False, "status": "not_configured", "error": type(exc).__name__}))
        return 2

    probe = getattr(args, "probe", False)

    async def call():
        if probe:
            return await client.probe()
        return await CertificateTransparencyAdapter(client).health(TaskContext("ct-health", "health"))

    result = asyncio.run(call())
    if probe:
        print(json.dumps({"ok": bool(result.get("ok")), "probe": True,
                          "network_requests_sent": 1, "detail": result}, ensure_ascii=True, default=str))
        return 0 if result.get("ok") else 2
    detail = result.observations[0] if result.observations else {}
    print(json.dumps({"ok": result.status == "completed", "status": result.status,
                      "verified": bool(detail.get("verified", False)),
                      "network_requests_sent": 0, "detail": detail}, ensure_ascii=True))
    return 0 if result.status == "completed" else 2


def cmd_ct_search(args: argparse.Namespace) -> int:
    if not args.real or not args.confirm_real:
        print(json.dumps({"ok": False, "error": "real_and_confirm_real_required"}))
        return 2
    db = Database(_config(args).database_path)
    db.initialize()
    task = next(db.rows("SELECT * FROM task WHERE task_id=?", (args.task_id,)), None)
    scope_row = next(db.rows(
        "SELECT * FROM scope_snapshot WHERE task_id=? ORDER BY rowid DESC", (args.task_id,)
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
        "failovers": result.failovers,
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
        "SELECT * FROM scope_snapshot WHERE task_id=? ORDER BY rowid DESC", (args.task_id,)
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
        "SELECT * FROM scope_snapshot WHERE task_id=? ORDER BY rowid DESC", (args.task_id,)
    ), None)
    if task is None or scope_row is None:
        print(json.dumps({"ok": False, "error": "task_or_scope_not_found"}))
        return 2
    scope = Scope.from_dict(json.loads(scope_row["payload_json"]))
    try:
        summary = merge_task_observations(db, args.task_id, scope)
        architecture = annotate_architecture(db, args.task_id)
    except Exception as exc:
        print(json.dumps({"ok": False, "status": "failed", "error": type(exc).__name__,
                          "detail": str(exc)[:300]}, ensure_ascii=True))
        return 2
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
    targets_file = getattr(args, "targets", None)
    if targets_file and args.url:
        print(json.dumps({"ok": False, "error": "url_and_targets_mutually_exclusive"}))
        return 2
    if targets_file:
        db = Database(_config(args).database_path)
        db.initialize()
        scope_row = next(db.rows(
            "SELECT * FROM scope_snapshot WHERE task_id=? ORDER BY rowid DESC", (args.task_id,)
        ), None)
        if scope_row is None or not scope_row["confirmed"]:
            print(json.dumps({"ok": False, "error": "scope_not_confirmed"}))
            return 2
        scope = Scope.from_dict(json.loads(scope_row["payload_json"]))
        return _http_observe_batch(db, scope, args, _read_targets_file(targets_file))
    if not args.url:
        print(json.dumps({"ok": False, "error": "url_or_targets_required"}))
        return 2
    db = Database(_config(args).database_path)
    db.initialize()
    scope_row = next(db.rows(
        "SELECT * FROM scope_snapshot WHERE task_id=? ORDER BY rowid DESC", (args.task_id,)
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
                                      url=args.url, method=args.method,
                                      allow_hosted_third_party=getattr(args, "allow_hosted_third_party", False))

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
        "override_used": result.override_used,
    }, ensure_ascii=True))
    return 0 if result.status == "completed" else 2


def _read_targets_file(path: str) -> list[str]:
    """B6: targets.txt——每行一条,空行与 # 注释行跳过。"""
    entries = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            entry = line.strip()
            if entry and not entry.startswith("#"):
                entries.append(entry)
    return entries


def _http_observe_batch(db, scope, args: argparse.Namespace, targets: list[str]) -> int:
    """B6: 批量 http-observe——串行逐台(不并发轰目标),单台失败不中断,
    结果逐台入 results;退出码 0=全部 completed,1=存在失败。"""
    from surface_intel.safe_http_client import SafeHttpFetchClient
    from surface_intel.safe_http_runner import run_http_observe

    if not targets:
        print(json.dumps({"ok": False, "error": "targets_empty"}))
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
    results: list[dict] = []

    async def run_batch():
        for entry in targets:
            url = entry if "://" in entry else "https://" + entry
            host = (urlsplit(url).hostname or "").lower()
            if not host or not scope.is_in_scope(host):
                results.append({"target": entry, "status": "failed",
                                "error": "target_out_of_scope"})
                continue
            try:
                result = await run_http_observe(
                    db, client, task_id=args.task_id, scope=scope, url=url,
                    method=args.method,
                    allow_hosted_third_party=getattr(args, "allow_hosted_third_party", False))
                results.append({"target": url, "status": result.status,
                                "observations": result.observations,
                                "error": result.error,
                                "override_used": result.override_used})
            except Exception as exc:
                results.append({"target": url, "status": "failed",
                                "error": type(exc).__name__})

    asyncio.run(run_batch())
    completed = sum(1 for r in results if r["status"] == "completed")
    failed = len(results) - completed
    print(json.dumps({"ok": failed == 0, "total": len(results),
                      "completed": completed, "failed": failed,
                      "results": results}, ensure_ascii=True))
    return 0 if failed == 0 else 1


def cmd_port_check(args: argparse.Namespace) -> int:
    from surface_intel.safe_port_client import SafePortProbeClient
    from surface_intel.safe_port_runner import run_port_check

    if not args.real or not args.confirm_real:
        print(json.dumps({"ok": False, "error": "real_and_confirm_real_required"}))
        return 2
    targets_file = getattr(args, "targets", None)
    if targets_file and (args.target or args.port is not None):
        print(json.dumps({"ok": False, "error": "single_target_and_targets_mutually_exclusive"}))
        return 2
    if targets_file:
        db = Database(_config(args).database_path)
        db.initialize()
        scope_row = next(db.rows(
            "SELECT * FROM scope_snapshot WHERE task_id=? ORDER BY rowid DESC", (args.task_id,)
        ), None)
        if scope_row is None or not scope_row["confirmed"]:
            print(json.dumps({"ok": False, "error": "scope_not_confirmed"}))
            return 2
        scope = Scope.from_dict(json.loads(scope_row["payload_json"]))
        return _port_check_batch(db, scope, args, _read_targets_file(targets_file))
    if not args.target or args.port is None:
        print(json.dumps({"ok": False, "error": "target_and_port_required"}))
        return 2
    db = Database(_config(args).database_path)
    db.initialize()
    scope_row = next(db.rows(
        "SELECT * FROM scope_snapshot WHERE task_id=? ORDER BY rowid DESC", (args.task_id,)
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
                                    target=args.target, port=args.port,
                                    allow_hosted_third_party=getattr(args, "allow_hosted_third_party", False))

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
        "override_used": result.override_used,
    }, ensure_ascii=True))
    return 0 if result.status == "completed" else 2


def _port_check_batch(db, scope, args: argparse.Namespace, targets: list[str]) -> int:
    """B6: 批量 port-check——行格式 host:port(缺格式该行记
    invalid_target_format),串行逐台,失败不中断。"""
    from surface_intel.safe_port_client import SafePortProbeClient
    from surface_intel.safe_port_runner import run_port_check

    if not targets:
        print(json.dumps({"ok": False, "error": "targets_empty"}))
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
    results: list[dict] = []

    async def run_batch():
        for entry in targets:
            host_part, _, port_part = entry.rpartition(":")
            if not host_part or not port_part.isdigit():
                results.append({"target": entry, "status": "failed",
                                "error": "invalid_target_format"})
                continue
            host = host_part.strip().lower().rstrip(".")
            if not host or not scope.is_in_scope(host):
                results.append({"target": entry, "status": "failed",
                                "error": "target_out_of_scope"})
                continue
            try:
                result = await run_port_check(
                    db, client, task_id=args.task_id, scope=scope, target=host,
                    port=int(port_part),
                    allow_hosted_third_party=getattr(args, "allow_hosted_third_party", False))
                results.append({"target": entry, "status": result.status,
                                "observations": result.observations,
                                "error": result.error,
                                "override_used": result.override_used})
            except Exception as exc:
                results.append({"target": entry, "status": "failed",
                                "error": type(exc).__name__})

    asyncio.run(run_batch())
    completed = sum(1 for r in results if r["status"] == "completed")
    failed = len(results) - completed
    print(json.dumps({"ok": failed == 0, "total": len(results),
                      "completed": completed, "failed": failed,
                      "results": results}, ensure_ascii=True))
    return 0 if failed == 0 else 1


def cmd_browser_observe(args: argparse.Namespace) -> int:
    from surface_intel.playwright_client import configured_playwright_client
    from surface_intel.playwright_runner import run_browser_observe

    if not args.real or not args.confirm_real:
        print(json.dumps({"ok": False, "error": "real_and_confirm_real_required"}))
        return 2
    db = Database(_config(args).database_path)
    db.initialize()
    scope_row = next(db.rows(
        "SELECT * FROM scope_snapshot WHERE task_id=? ORDER BY rowid DESC", (args.task_id,)
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
    # 截图落 <task-root>/reports/<task-id>/screenshots/（MCP 进程以该目录为 cwd）
    screenshots_dir = _config(args).task_root / "reports" / args.task_id / "screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    client = configured_playwright_client(cwd=screenshots_dir)

    async def call():
        try:
            return await run_browser_observe(db, client, task_id=args.task_id, scope=scope,
                                             url=args.url, max_requests=args.max_requests)
        finally:
            # 在事件循环内显式收掉 stdio 子进程，否则退出时留下
            # unclosed transport 噪声（npx 进程也可能滞留）
            await client.close()

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

    from surface_intel.monitor import diff_against_baseline, diff_checkpoints

    db = Database(_config(args).database_path)
    task = next(db.rows("SELECT scope_version FROM task WHERE task_id=?", (args.task_id,)), None)
    if task is None:
        print(json.dumps({"ok": False, "error": "task_not_found"}))
        return 2
    if getattr(args, "baseline", None):
        # D11: 任意两次 checkpoint 对比；against 侧缺省=当前资产集
        try:
            diff = diff_checkpoints(db, args.task_id, args.baseline,
                                    getattr(args, "against", None))
        except ValueError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}))
            return 2
    else:
        diff = diff_against_baseline(db, args.task_id, task["scope_version"])
    payload = {"ok": True, **asdict(diff)}
    output_path = getattr(args, "output", None)
    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"ok": True, "task_id": args.task_id, "output": str(out)}))
    else:
        print(json.dumps(payload, ensure_ascii=True))
    return 0


def cmd_diff_report(args: argparse.Namespace) -> int:
    """B7: 差量复查一页——baseline-diff 的 markdown 单页渲染并落盘。

    缺省对比 latest baseline vs 当前资产集;--baseline/--against 指定
    checkpoint 对(地基=diff_checkpoints)。缺省落 reports/<task-id>/,
    输出全量列出,不截断。
    """
    from datetime import datetime, timezone

    from surface_intel.monitor import diff_against_baseline, diff_checkpoints
    from surface_intel.reporting.diff_report import render_diff_report

    db = Database(_config(args).database_path)
    db.initialize()
    task = next(db.rows("SELECT scope_version FROM task WHERE task_id=?", (args.task_id,)), None)
    if task is None:
        print(json.dumps({"ok": False, "error": "task_not_found"}))
        return 2
    if getattr(args, "baseline", None):
        baseline_label = f"checkpoint {args.baseline}"
        against_label = (f"checkpoint {args.against}"
                         if getattr(args, "against", None) else "当前资产集")
        try:
            diff = diff_checkpoints(db, args.task_id, args.baseline,
                                    getattr(args, "against", None))
        except ValueError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}))
            return 2
    else:
        baseline_label = "latest baseline"
        against_label = "当前资产集"
        diff = diff_against_baseline(db, args.task_id, task["scope_version"])
    generated_at = datetime.now(timezone.utc).isoformat()
    markdown = render_diff_report(diff, task_id=args.task_id,
                                  baseline_label=baseline_label,
                                  against_label=against_label,
                                  generated_at=generated_at)
    output_path = getattr(args, "output", None)
    if output_path:
        out = Path(output_path)
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out = _config(args).task_root / "reports" / args.task_id / f"diff-report-{stamp}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(markdown, encoding="utf-8")
    print(json.dumps({"ok": True, "task_id": args.task_id, "output": str(out),
                      "added": len(diff.added), "disappeared": len(diff.disappeared),
                      "changed": len(diff.changed), "reappeared": len(diff.reappeared),
                      "unchanged": diff.unchanged}, ensure_ascii=True))
    return 0


def cmd_scope_amend(args: argparse.Namespace) -> int:
    """Controlled scope amendment (A1/A2).

    Values already discovered as candidates amend freely; --confirm-new-domain
    opens the explicit user-confirmation channel for never-seen domains; and
    active authorization rises only via --allow-active (+ --active-level /
    --ports). Every --confirm lands snapshot + task pointer + audit event in
    one transaction and freezes old-version actions (R5).
    """
    from surface_intel.policy.scope_amend import (
        AmendmentError,
        apply_scope_amendment,
        resolve_amendment_values,
    )

    db = Database(_config(args).database_path)
    db.initialize()
    task = next(db.rows("SELECT scope_version FROM task WHERE task_id=?", (args.task_id,)), None)
    scope_row = next(db.rows(
        "SELECT payload_json FROM scope_snapshot WHERE task_id=? ORDER BY rowid DESC",
        (args.task_id,),
    ), None)
    if task is None or scope_row is None:
        print(json.dumps({"ok": False, "error": "task_or_scope_not_found"}))
        return 2
    payload = json.loads(scope_row["payload_json"])
    add_ips = tuple(item.strip() for item in args.add_ips.split(",") if item.strip())
    add_domains = tuple(item.strip() for item in args.add_domains.split(",") if item.strip())
    ports: list[int] = []
    if args.ports:
        for item in args.ports.split(","):
            item = item.strip()
            if not item:
                continue
            try:
                value = int(item)
            except ValueError:
                value = -1
            if not 1 <= value <= 65535:
                print(json.dumps({"ok": False, "error": "invalid_ports", "detail": item}))
                return 2
            ports.append(value)
    if args.active_level and not args.allow_active:
        print(json.dumps({"ok": False, "error": "allow_active_required"}))
        return 2
    if ports and not args.allow_active:
        print(json.dumps({"ok": False, "error": "ports_require_allow_active"}))
        return 2

    try:
        values = resolve_amendment_values(
            db, args.task_id, add_ips=add_ips, add_domains=add_domains,
            confirm_new_domain=args.confirm_new_domain,
        )
    except AmendmentError as exc:
        print(json.dumps({"ok": False, "error": exc.code, "detail": exc.detail}))
        return 2
    if values.empty() and not args.allow_active:
        print(json.dumps({"ok": False, "error": "nothing_to_amend"}))
        return 2

    proposal = {
        "task_id": args.task_id,
        "base_scope_version": payload.get("scope_version"),
        "add_ips": list(values.add_ips),
        "add_domains": list(values.add_domains),
        "user_confirmed_new_domains": list(values.user_confirmed_new_domains),
        "allow_active": bool(args.allow_active),
        "active_level": args.active_level if args.allow_active else None,
        "ports": ports,
        "reason": args.reason,
    }
    if not args.confirm:
        print(json.dumps({"ok": True, "proposal": proposal,
                          "note": "re-run with --confirm to apply"}, ensure_ascii=True))
        return 0

    outcome = apply_scope_amendment(
        db, args.task_id, base_payload=payload,
        add_ips=values.add_ips, add_domains=values.add_domains,
        user_confirmed_new_domains=values.user_confirmed_new_domains,
        allow_active=args.allow_active,
        active_level=args.active_level or "safe_discovery",
        ports=tuple(ports), reason=args.reason, source="manual",
    )
    print(json.dumps({"ok": True, "scope_version": outcome.new_scope_version,
                      "added_ips": list(outcome.added_ips),
                      "added_domains": list(outcome.added_domains),
                      "user_confirmed_new_domains": list(outcome.user_confirmed_new_domains),
                      "active_enabled": outcome.active_enabled,
                      "active_level": outcome.active_level if outcome.active_enabled else None,
                      "added_ports": list(outcome.added_ports),
                      "frozen_actions": outcome.frozen_actions}, ensure_ascii=True))
    return 0


def cmd_recon(args: argparse.Namespace) -> int:
    """Retrospective P1-5: tested full-domain passive recon orchestration."""
    from dataclasses import asdict

    from surface_intel.ct_client import configured_ct_client
    from surface_intel.dns_client import configured_dns_client
    from surface_intel.fofa_client import configured_fofa_client
    from surface_intel.orchestration.full_recon import run_full_passive_recon
    from surface_intel.rdap_client import configured_rdap_client

    if not args.real or not args.confirm_real:
        print(json.dumps({"ok": False, "error": "real_and_confirm_real_required"}))
        return 2
    db = Database(_config(args).database_path)
    db.initialize()
    scope_row = next(db.rows(
        "SELECT payload_json FROM scope_snapshot WHERE task_id=? ORDER BY rowid DESC",
        (args.task_id,),
    ), None)
    if scope_row is None or not json.loads(scope_row["payload_json"]).get("confirmed"):
        print(json.dumps({"ok": False, "error": "scope_not_confirmed"}))
        return 2
    scope = Scope.from_dict(json.loads(scope_row["payload_json"]))
    fofa_client = configured_fofa_client()
    ct_client = configured_ct_client()
    dns_client = configured_dns_client()
    rdap_client = configured_rdap_client()

    async def call():
        try:
            return await run_full_passive_recon(
                db, task_id=args.task_id, scope=scope,
                fofa_client=fofa_client, ct_client=ct_client,
                dns_client=dns_client, rdap_client=rdap_client,
                fofa_query=args.query, fofa_size=args.size,
                fofa_max_pages=args.max_pages, ct_domain=args.ct_domain,
                max_rdap_ips=args.max_rdap_ips,
                auto_amend_discovered_ips=args.auto_amend_discovered_ips,
            )
        finally:
            # P01: tear the fofa stdio subprocess down inside the event loop -
            # its pipe transports otherwise surface "unclosed transport" as
            # unraisables at interpreter shutdown, which entry-point
            # filterwarnings cannot suppress (F1 real-recon smoke finding)
            closer = getattr(fofa_client, "close", None)
            if closer is not None:
                try:
                    await closer()
                except Exception:
                    pass  # teardown must not mask the run result

    try:
        result = asyncio.run(call())
    except Exception as exc:
        print(json.dumps({"ok": False, "status": "failed", "error": type(exc).__name__}))
        return 2
    print(json.dumps({"ok": result.status == "completed",
                      **asdict(result)}, ensure_ascii=True, default=str))
    return 0 if result.status == "completed" else 2


def cmd_report(args: argparse.Namespace) -> int:
    db = Database(_config(args).database_path)
    if getattr(args, "merge_tasks", None):
        if args.task_id:
            print(json.dumps({"ok": False, "error": "task_id_and_merge_exclusive"}))
            return 2
        from .reporting.merged import build_merged_report

        try:
            result = build_merged_report(db, args.merge_tasks,
                                         Path(_config(args).task_root) / "reports" / "merged",
                                         reveal_emails=getattr(args, "reveal_emails", False))
        except ValueError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}))
            return 2
        print(json.dumps({"ok": result.get("xlsx_status") == "exported", **result},
                         ensure_ascii=True, default=str))
        return 0 if result.get("xlsx_status") == "exported" else 2
    if not args.task_id:
        print(json.dumps({"ok": False, "error": "task_id_or_merge_required"}))
        return 2
    try:
        # C1: per-task subdirectory - no cross-task overwrite
        report = build_report(db, args.task_id, report_output_dir(_config(args).task_root, args.task_id),
                              reveal_emails=getattr(args, "reveal_emails", False))
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2
    delivered = None
    if getattr(args, "deliver", False):
        delivered = _deliver_report(
            report, task_root=Path(_config(args).task_root), task_id=args.task_id,
            round_label=args.round, title=args.title)
    print(json.dumps({"ok": True, "integrity_status": report["integrity_status"],
                      "summary": report["summary"], "export": report["export"],
                      "delivered": delivered}, ensure_ascii=True))
    return 0


def _deliver_report(report: dict, *, task_root: Path, task_id: str,
                    round_label: str | None, title: str | None) -> dict:
    """D-2 (rev 2026-10-05): copy the tool workbook to <task-root>/../reports/
    as {round}_{date}_{title}.xlsx - a placeholder at the tool-export format.
    The delivered xlsx has a FIXED format: the 5-sheet workbook from
    scripts/build_deliverable_xlsx.py (project data variant) overwrites this
    placeholder under the same name as the final delivery step. The analyst
    export is no longer copied into the delivery directory (dropped per
    operator decision - analyst.xlsx format is not a deliverable). The
    narrative md stays agent-authored and is written straight into the same
    directory."""
    import shutil
    from datetime import datetime

    export = report.get("export") or {}
    date = datetime.now().strftime("%Y-%m-%d")
    stem = f"{round_label or 'deliver'}_{date}_{title or task_id[:8]}"
    deliver_dir = task_root.parent / "reports"
    deliver_dir.mkdir(parents=True, exist_ok=True)
    copied: dict[str, str] = {}
    if export.get("xlsx_status") == "exported" and export.get("xlsx"):
        src = Path(export["xlsx"])
        if src.exists():
            shutil.copy2(src, deliver_dir / f"{stem}.xlsx")
            copied[f"{stem}.xlsx"] = str(deliver_dir / f"{stem}.xlsx")
    return {"dir": str(deliver_dir), "files": copied}


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
    for name, handler in (("init", cmd_init), ("create", cmd_create), ("preflight", cmd_preflight), ("plan", cmd_plan), ("run", cmd_run), ("fofa-health", cmd_fofa_health), ("fofa-search", cmd_fofa_search), ("dns-health", cmd_dns_health), ("dns-lookup", cmd_dns_lookup), ("ct-health", cmd_ct_health), ("ct-search", cmd_ct_search), ("rdap-health", cmd_rdap_health), ("rdap-lookup", cmd_rdap_lookup), ("merge", cmd_merge), ("http-observe", cmd_http_observe), ("port-check", cmd_port_check), ("browser-observe", cmd_browser_observe), ("baseline-save", cmd_baseline_save), ("baseline-diff", cmd_baseline_diff), ("scope-amend", cmd_scope_amend), ("recon", cmd_recon), ("report", cmd_report), ("checkpoint", cmd_checkpoint), ("recover", cmd_recover), ("pause", lambda args: _task_control(args, TaskStatus.PAUSED)), ("resume", lambda args: _task_control(args, TaskStatus.PLANNED)), ("cancel", lambda args: _task_control(args, TaskStatus.CANCELLED)), ("status", cmd_status), ("validate", cmd_validate), ("diff-report", cmd_diff_report)):
        command = sub.add_parser(name)
        command.set_defaults(handler=handler)
        if name == "init":
            command.add_argument("task_root")
            # D-1: also create the project skeleton beside the task root
            command.add_argument("--scaffold", action="store_true")
        elif name not in {"fofa-health", "dns-health", "ct-health", "rdap-health"}:
            command.add_argument("--task-root", required=True)
        if name == "create":
            command.add_argument("--scope", required=True)
        if name == "run":
            command.add_argument("--mode", choices=("serial", "delegated", "hybrid"), default="serial")
        if name == "fofa-health":
            command.add_argument("--real", action="store_true")
        if name == "dns-health":
            command.add_argument("--real", action="store_true")
        if name == "ct-health":
            command.add_argument("--real", action="store_true")
            # D1: opt-in real reachability probe; requires --confirm-real too
            command.add_argument("--probe", action="store_true")
            command.add_argument("--confirm-real", action="store_true")
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
            # B6: --url 与 --targets 二选一(批量为 targets.txt,每行一个 URL/host)
            command.add_argument("--url", required=False, default=None)
            command.add_argument("--targets", default=None)
            command.add_argument("--method", default="GET", choices=("GET", "HEAD"))
            # A4: company sites hosted on third-party ranges - explicit override
            command.add_argument("--allow-hosted-third-party", dest="allow_hosted_third_party",
                                 action="store_true")
            command.add_argument("--real", action="store_true")
            command.add_argument("--confirm-real", action="store_true")
        if name == "port-check":
            command.add_argument("task_id")
            # B6: 批量模式行格式 host:port;单目标仍用 --target/--port
            command.add_argument("--target", required=False, default=None)
            command.add_argument("--port", type=int, required=False, default=None)
            command.add_argument("--targets", default=None)
            command.add_argument("--allow-hosted-third-party", dest="allow_hosted_third_party",
                                 action="store_true")
            command.add_argument("--real", action="store_true")
            command.add_argument("--confirm-real", action="store_true")
        if name == "browser-observe":
            command.add_argument("task_id")
            command.add_argument("--url", required=True)
            command.add_argument("--max-requests", type=int, default=100)
            command.add_argument("--real", action="store_true")
            command.add_argument("--confirm-real", action="store_true")
        if name == "scope-amend":
            command.add_argument("--add-ips", default="")
            command.add_argument("--add-domains", default="")
            command.add_argument("--reason", default="")
            command.add_argument("--confirm", action="store_true")
            # A1/A2: active authorization and never-seen domains only rise
            # through these explicit flags; intrusive_test is unreachable
            command.add_argument("--allow-active", action="store_true")
            command.add_argument("--active-level", choices=("safe_discovery", "safe_probe"), default=None)
            command.add_argument("--ports", default="")
            command.add_argument("--confirm-new-domain", action="store_true")
        if name == "recon":
            command.add_argument("task_id")
            command.add_argument("--fofa-query", "--query", dest="query", required=True)
            command.add_argument("--size", type=int, default=100)
            command.add_argument("--max-pages", type=int, default=5)
            command.add_argument("--ct-domain", default=None)
            command.add_argument("--max-rdap-ips", type=int, default=5)
            # A3: opt-in self-amendment for DNS-revealed IPs before the RDAP stage
            command.add_argument("--auto-amend-discovered-ips", action="store_true")
            command.add_argument("--real", action="store_true")
            command.add_argument("--confirm-real", action="store_true")
        if name == "report":
            # C1: positional task_id (single report) XOR --merge (merged workbook)
            command.add_argument("task_id", nargs="?")
            command.add_argument("--merge", nargs="+", dest="merge_tasks")
            # C3: explicit opt-in - restore redaction_map plaintexts into deliverables
            command.add_argument("--reveal-emails", action="store_true")
            # D-2: double-write deliverables to <task-root>/../reports/
            command.add_argument("--deliver", action="store_true")
            command.add_argument("--round", default=None)
            command.add_argument("--title", default=None)
        if name in {"status", "validate", "preflight", "plan", "run", "checkpoint", "recover", "pause", "resume", "cancel", "merge", "baseline-save", "baseline-diff", "diff-report", "scope-amend"}:
            command.add_argument("task_id")
        if name == "checkpoint":
            command.add_argument("--phase", default="manual")
            command.add_argument("--reason", default="")
        if name == "baseline-diff":
            # D11: 任意两次 checkpoint 对比 + diff 结果导出（缺省=latest baseline vs 当前）
            command.add_argument("--baseline", default=None)
            command.add_argument("--against", default=None)
            command.add_argument("--output", default=None)
        if name == "diff-report":
            # B7: 差量复查一页(markdown 落盘;缺省 latest baseline vs 当前资产集)
            command.add_argument("--baseline", default=None)
            command.add_argument("--against", default=None)
            command.add_argument("--output", default=None)
        if name in {"pause", "cancel", "resume"}:
            command.add_argument("--reason", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    # D2-3: Windows proactor transports on Python 3.14 emit harmless
    # "unclosed transport" ResourceWarnings at interpreter shutdown (recon
    # ends); they drown real output, so silence exactly that message
    warnings.filterwarnings("ignore", message=".*unclosed transport.*",
                            category=ResourceWarning)
    args = build_parser().parse_args(argv)
    configure_logging()
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
