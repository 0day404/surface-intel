from __future__ import annotations

import ipaddress
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .policy.scope import Scope
from .state.database import Database

# Frozen asset taxonomy for the passive merge layer; service/url/app assets
# arrive with FOFA/active sources later.
ASSET_TYPES = ("domain", "ip", "cidr", "asn", "organization")

# Relation taxonomy per plan section 12.2. Weak relations are reserved for
# name/brand similarity and are never produced by passive structure data -
# the classifier refuses to confirm anything resting on weak evidence.
RELATION_STRENGTH_BASE = {"strong": 0.7, "medium": 0.5, "weak": 0.25}
STRONG_RELATION_TYPES = {
    "dns_a", "dns_aaaa", "dns_cname", "dns_ns", "dns_mx", "dns_soa", "cert_name",
}
MEDIUM_RELATION_TYPES = {"rdap_org", "rdap_registrar", "rdap_cidr", "rdap_asn"}
RELATION_TYPES = tuple(sorted(STRONG_RELATION_TYPES | MEDIUM_RELATION_TYPES))

CORROBORATION_BONUS = 0.1
CORROBORATION_CAP = 0.2
CANDIDATE_CONFIDENCE_CAP = 0.95
SCOPE_CONFIDENCE = 1.0

# Attributes tracked for source conflicts; conflicting values are preserved
# as unresolved conflict rows, never silently picked.
CONFLICT_ATTRIBUTES = ("organization", "registrar", "asn")


@dataclass(frozen=True)
class MergeSummary:
    status: str
    observations_processed: int
    observations_skipped: int
    assets_created: int
    assets_updated: int
    relations_created: int
    relations_updated: int
    conflicts_created: int
    skipped: tuple[dict[str, str], ...] = field(default_factory=tuple)


def normalize_asset_value(asset_type: str, value: str) -> str | None:
    """Idempotent normalization per plan 5.1; returns None when unnormalizable.

    Never raises: unnormalizable values are reported by the caller as
    `normalization_failed` instead of being silently dropped.
    """
    cleaned = str(value or "").strip()
    if not cleaned:
        return None
    if asset_type == "domain":
        normalized = cleaned.rstrip(".").lower()
        return normalized or None
    if asset_type == "ip":
        try:
            return str(ipaddress.ip_address(cleaned))
        except ValueError:
            return None
    if asset_type == "cidr":
        network = _parse_loose_cidr(cleaned)
        return str(network) if network is not None else None
    if asset_type == "asn":
        digits = cleaned.upper().removeprefix("AS")
        return digits if digits.isdigit() else None
    if asset_type == "organization":
        return cleaned or None
    return None


def _parse_loose_cidr(value: str):
    """Accept registry shorthand like `203.0.113/24` as well as full CIDRs.

    RDAP cidr0_cidrs entries drop trailing zero octets; padding with `.0` is
    unambiguous for the grouping the registries use. A bare address without a
    prefix length is not a network and is rejected.
    """
    text = value.strip()
    if "/" not in text:
        return None
    prefix, _, length = text.partition("/")
    if not length.isdigit():
        return None
    try:
        return ipaddress.ip_network(text, strict=False)
    except ValueError:
        pass
    octets = prefix.split(".")
    if octets and all(item.isdigit() for item in octets) and 1 <= len(octets) <= 4:
        padded = ".".join(octets + ["0"] * (4 - len(octets)))
        try:
            return ipaddress.ip_network(f"{padded}/{length}", strict=False)
        except ValueError:
            return None
    return None


def classify_asset_scope(scope: Scope, asset_type: str, normalized_value: str) -> str:
    """Scope status for a merged asset; attribution classes never confirm.

    Domains and IPs can be `confirmed` only through the scope document itself.
    Organizations, CIDRs and ASNs stay `candidate` forever: per the frozen
    rules, organization names and ASN equality are discovery leads, never
    authorization.
    """
    if asset_type == "domain":
        return "confirmed" if scope.is_in_scope(normalized_value) else "candidate"
    if asset_type == "ip":
        return "confirmed" if scope.is_in_scope(normalized_value) else "candidate"
    return "candidate"


def relation_strength(relation_type: str) -> str:
    if relation_type in STRONG_RELATION_TYPES:
        return "strong"
    if relation_type in MEDIUM_RELATION_TYPES:
        return "medium"
    return "weak"


def asset_confidence(
    *,
    scope_status: str,
    scope_anchored: bool,
    strengths: set[str],
    source_count: int,
) -> float:
    """Deterministic confidence: strongest relation + multi-source corroboration.

    Weak relations alone never yield a confirmable score and candidates are
    capped below 1.0 - only user-scoped anchors reach 1.0.
    """
    if scope_anchored:
        return SCOPE_CONFIDENCE
    base = max((RELATION_STRENGTH_BASE[s] for s in strengths), default=RELATION_STRENGTH_BASE["weak"])
    bonus = min(CORROBORATION_CAP, max(0, source_count - 1) * CORROBORATION_BONUS)
    cap = CANDIDATE_CONFIDENCE_CAP if scope_status == "candidate" else 1.0
    return round(min(cap, base + bonus), 2)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class _AssetDraft:
    asset_type: str
    normalized_value: str
    scope_status: str
    scope_anchored: bool = False
    sources: list[dict[str, Any]] = field(default_factory=list)
    seen: list[str] = field(default_factory=list)
    strengths: set[str] = field(default_factory=set)
    attributes: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def key(self) -> tuple[str, str]:
        return (self.asset_type, self.normalized_value)


class _MergeWorkspace:
    """In-memory accumulation over one task's observations before persistence."""

    def __init__(self, scope: Scope):
        self.scope = scope
        self.assets: dict[tuple[str, str], _AssetDraft] = {}
        self.relations: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.skipped: list[dict[str, str]] = []

    def asset(
        self,
        asset_type: str,
        raw_value: str,
        *,
        source: str,
        observation_id: str,
        observed_at: str,
        scope_anchored: bool = False,
   ) -> tuple[str, str] | None:
        normalized = normalize_asset_value(asset_type, raw_value)
        if normalized is None or asset_type not in ASSET_TYPES:
            if raw_value:
                self.skipped.append({
                    "reason": "normalization_failed", "asset_type": asset_type,
                    "value": str(raw_value)[:200], "observation_id": observation_id,
                })
            return None
        key = (asset_type, normalized)
        draft = self.assets.get(key)
        if draft is None:
            draft = _AssetDraft(
                asset_type=asset_type,
                normalized_value=normalized,
                scope_status=classify_asset_scope(self.scope, asset_type, normalized),
                scope_anchored=scope_anchored,
            )
            self.assets[key] = draft
        if scope_anchored:
            draft.scope_anchored = True
            draft.scope_status = "confirmed"
        draft.sources.append({"source": source, "observation_id": observation_id})
        if observed_at:
            draft.seen.append(observed_at)
        return key

    def relation(
        self,
        from_key: tuple[str, str] | None,
        to_key: tuple[str, str] | None,
        relation_type: str,
        *,
        source: str,
        observation_id: str,
        observed_at: str,
        attribute: dict[str, Any] | None = None,
    ) -> None:
        if from_key is None or to_key is None or from_key == to_key:
            return
        key = (from_key, to_key, relation_type)
        entry = self.relations.get(key)
        provenance = {"source": source, "observation_id": observation_id, "observed_at": observed_at}
        if entry is None:
            entry = {
                "relation_type": relation_type,
                "provenance": [provenance],
                "attribute": attribute,
            }
            self.relations[key] = entry
        else:
            entry["provenance"].append(provenance)
        strength = relation_strength(relation_type)
        self.assets[to_key].strengths.add(strength)
        self.assets[from_key].strengths.add(strength)


def _extract_dns(workspace: _MergeWorkspace, payload: dict[str, Any], observation: dict[str, Any]) -> None:
    target = workspace.asset(
        "domain", payload.get("target", ""), source=observation["source"],
        observation_id=observation["observation_id"], observed_at=observation.get("observed_at", ""),
        scope_anchored=True,
    )
    if target is None:
        return
    record_type = str(payload.get("record_type", "")).upper()
    relation_type = f"dns_{record_type.lower()}" if record_type in {"A", "AAAA", "CNAME", "NS", "MX", "SOA"} else None
    for record in payload.get("records", []) or []:
        value = str(record)
        if record_type in {"A", "AAAA"}:
            key = workspace.asset("ip", value, source=observation["source"],
                                  observation_id=observation["observation_id"],
                                  observed_at=observation.get("observed_at", ""))
            workspace.relation(target, key, relation_type, source=observation["source"],
                               observation_id=observation["observation_id"],
                               observed_at=observation.get("observed_at", ""))
        elif record_type in {"CNAME", "NS"}:
            key = workspace.asset("domain", value, source=observation["source"],
                                  observation_id=observation["observation_id"],
                                  observed_at=observation.get("observed_at", ""))
            workspace.relation(target, key, relation_type, source=observation["source"],
                               observation_id=observation["observation_id"],
                               observed_at=observation.get("observed_at", ""))
        elif record_type == "MX":
            exchange = value.split(" ", 1)[-1].strip()
            key = workspace.asset("domain", exchange, source=observation["source"],
                                  observation_id=observation["observation_id"],
                                  observed_at=observation.get("observed_at", ""))
            workspace.relation(target, key, relation_type, source=observation["source"],
                               observation_id=observation["observation_id"],
                               observed_at=observation.get("observed_at", ""))
        elif record_type == "SOA":
            mname = value.split(" ", 1)[0].strip()
            key = workspace.asset("domain", mname, source=observation["source"],
                                  observation_id=observation["observation_id"],
                                  observed_at=observation.get("observed_at", ""))
            workspace.relation(target, key, relation_type, source=observation["source"],
                               observation_id=observation["observation_id"],
                               observed_at=observation.get("observed_at", ""))
        # TXT and unknown types carry no asset relations


def _extract_ct(workspace: _MergeWorkspace, payload: dict[str, Any], observation: dict[str, Any]) -> None:
    target = workspace.asset(
        "domain", payload.get("target", ""), source=observation["source"],
        observation_id=observation["observation_id"], observed_at=observation.get("observed_at", ""),
        scope_anchored=True,
    )
    if target is None:
        return
    for certificate in payload.get("certificates", []) or []:
        names = [certificate.get("common_name"), *(certificate.get("sans") or [])]
        for raw_name in names:
            if not raw_name:
                continue
            # certificates may carry IP SANs; route by value, not by field name
            name_type = "ip" if _parses_as_ip(str(raw_name)) else "domain"
            key = workspace.asset(name_type, str(raw_name), source=observation["source"],
                                  observation_id=observation["observation_id"],
                                  observed_at=observation.get("observed_at", ""))
            workspace.relation(
                target, key, "cert_name", source=observation["source"],
                observation_id=observation["observation_id"],
                observed_at=observation.get("observed_at", ""),
                attribute={
                    "issuer": certificate.get("issuer"),
                    "serial": certificate.get("serial"),
                    "not_before": certificate.get("not_before"),
                    "not_after": certificate.get("not_after"),
                    "san_completeness": certificate.get("san_completeness"),
                },
            )


def _parses_as_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value.strip())
        return True
    except ValueError:
        return False


def _extract_rdap(workspace: _MergeWorkspace, payload: dict[str, Any], observation: dict[str, Any]) -> None:
    if payload.get("status") != "observed":
        return  # definitive negatives and infra failures create no assets
    kind = str(payload.get("target_kind", ""))
    asset_type = {"domain": "domain", "ip": "ip", "asn": "asn"}.get(kind)
    if asset_type is None:
        return
    target = workspace.asset(
        asset_type, payload.get("target", ""), source=observation["source"],
        observation_id=observation["observation_id"], observed_at=observation.get("observed_at", ""),
        scope_anchored=asset_type in {"domain", "ip"},
    )
    if target is None:
        return
    record = payload.get("record") or {}
    observed_at = observation.get("observed_at", "")

    def relate_org(raw_value: str, relation_type: str, attribute_name: str) -> None:
        key = workspace.asset("organization", raw_value, source=observation["source"],
                              observation_id=observation["observation_id"], observed_at=observed_at)
        workspace.relation(target, key, relation_type, source=observation["source"],
                           observation_id=observation["observation_id"], observed_at=observed_at)
        if key is not None and raw_value:
            workspace.assets[target].attributes.setdefault(attribute_name, []).append({
                "value": str(raw_value), "source": observation["source"],
                "observation_id": observation["observation_id"], "observed_at": observed_at,
            })

    for org in record.get("organizations", []) or []:
        relate_org(str(org), "rdap_org", "organization")
    registrar = record.get("registrar")
    if registrar:
        relate_org(str(registrar), "rdap_registrar", "registrar")
    for cidr in record.get("cidrs", []) or []:
        key = workspace.asset("cidr", str(cidr), source=observation["source"],
                              observation_id=observation["observation_id"], observed_at=observed_at)
        workspace.relation(target, key, "rdap_cidr", source=observation["source"],
                           observation_id=observation["observation_id"], observed_at=observed_at)
    asn_value = record.get("asn_start")
    if asn_value is not None:
        key = workspace.asset("asn", str(asn_value), source=observation["source"],
                              observation_id=observation["observation_id"], observed_at=observed_at)
        workspace.relation(target, key, "rdap_asn", source=observation["source"],
                           observation_id=observation["observation_id"], observed_at=observed_at)
        workspace.assets[target].attributes.setdefault("asn", []).append({
            "value": str(asn_value), "source": observation["source"],
            "observation_id": observation["observation_id"], "observed_at": observed_at,
        })


_EXTRACTORS = {
    "dns_resolver": _extract_dns,
    "ct_log": _extract_ct,
    "rdap_lookup": _extract_rdap,
}


def merge_task_observations(db: Database, task_id: str, scope: Scope) -> MergeSummary:
    """Merge all stored observations of one task into the unified asset model.

    Idempotent by construction: assets are keyed by (type, normalized_value),
    re-merge updates `last_seen` and re-derives confidence while `first_seen`
    is preserved, relations deduplicate with merged provenance, and attribute
    conflicts are rewritten as unresolved rows instead of being resolved.
    """
    workspace = _MergeWorkspace(scope)
    processed = skipped_observations = 0
    for row in db.rows(
        "SELECT observation_id,source,payload_json,created_at FROM observation WHERE task_id=? ORDER BY rowid",
        (task_id,),
    ):
        try:
            payload = json.loads(row["payload_json"])
        except json.JSONDecodeError:
            workspace.skipped.append({
                "reason": "invalid_payload_json", "observation_id": row["observation_id"],
                "source": row["source"], "value": "",
            })
            skipped_observations += 1
            continue
        extractor = _EXTRACTORS.get(row["source"])
        if extractor is None:
            # other sources (e.g. fofa) merge in later increments
            skipped_observations += 1
            continue
        observation = {
            "observation_id": row["observation_id"],
            "source": row["source"],
            "observed_at": payload.get("observed_at") or row["created_at"],
        }
        extractor(workspace, payload, observation)
        processed += 1

    created = updated = relations_created = relations_updated = conflicts_created = 0
    now = _now_iso()
    with db.connect() as connection:
        for (asset_type, normalized_value), draft in sorted(workspace.assets.items()):
            sources_payload = json.dumps(draft.sources, ensure_ascii=True, sort_keys=True)
            confidence = asset_confidence(
                scope_status=draft.scope_status,
                scope_anchored=draft.scope_anchored,
                strengths=draft.strengths,
                source_count=len({item["source"] for item in draft.sources}),
            )
            first_seen = min(draft.seen) if draft.seen else now
            last_seen = max(draft.seen) if draft.seen else now
            existing = connection.execute(
                "SELECT asset_id,first_seen FROM asset WHERE task_id=? AND asset_type=? AND normalized_value=?",
                (task_id, asset_type, normalized_value),
            ).fetchone()
            if existing is None:
                asset_id = f"asset-{uuid.uuid4().hex[:16]}"
                connection.execute(
                    """INSERT INTO asset(asset_id,task_id,asset_type,normalized_value,scope_status,
                                         confidence,first_seen,last_seen,sources_json)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    (asset_id, task_id, asset_type, normalized_value, draft.scope_status,
                     confidence, first_seen, last_seen, sources_payload),
                )
                created += 1
            else:
                asset_id = existing["asset_id"]
                connection.execute(
                    """UPDATE asset SET
                         scope_status=CASE WHEN scope_status='confirmed' THEN 'confirmed' ELSE ? END,
                         confidence=?,
                         first_seen=CASE WHEN first_seen IS NULL OR first_seen<? THEN first_seen ELSE ? END,
                         last_seen=CASE WHEN last_seen IS NULL OR last_seen<? THEN ? ELSE last_seen END,
                         sources_json=?
                       WHERE asset_id=?""",
                    (draft.scope_status, confidence,
                     first_seen, first_seen,
                     last_seen, last_seen,
                     sources_payload, asset_id),
                )
                updated += 1

            for attribute_name, values in sorted(draft.attributes.items()):
                if attribute_name not in CONFLICT_ATTRIBUTES:
                    continue
                distinct = {json.dumps(item, sort_keys=True) for item in values}
                distinct_values = [json.loads(item) for item in sorted(distinct)]
                distinct_values_by_value: dict[str, list[dict[str, Any]]] = {}
                for item in distinct_values:
                    distinct_values_by_value.setdefault(str(item["value"]), []).append(item)
                if len(distinct_values_by_value) > 1:
                    connection.execute(
                        "DELETE FROM asset_conflict WHERE task_id=? AND asset_id=? AND attribute=?",
                        (task_id, asset_id, attribute_name),
                    )
                    connection.execute(
                        """INSERT INTO asset_conflict(conflict_id,task_id,asset_id,attribute,values_json,status)
                           VALUES(?,?,?,?,?, 'unresolved')""",
                        (f"conflict-{uuid.uuid4().hex[:16]}", task_id, asset_id, attribute_name,
                         json.dumps(distinct_values, ensure_ascii=True, sort_keys=True)),
                    )
                    conflicts_created += 1

        for (from_key, to_key, relation_type), entry in sorted(workspace.relations.items()):
            from_asset = connection.execute(
                "SELECT asset_id FROM asset WHERE task_id=? AND asset_type=? AND normalized_value=?",
                (task_id, from_key[0], from_key[1]),
            ).fetchone()
            to_asset = connection.execute(
                "SELECT asset_id FROM asset WHERE task_id=? AND asset_type=? AND normalized_value=?",
                (task_id, to_key[0], to_key[1]),
            ).fetchone()
            if from_asset is None or to_asset is None:
                continue
            provenance = json.dumps(entry["provenance"], ensure_ascii=True, sort_keys=True)
            status = "candidate"
            strength = relation_strength(relation_type)
            if strength == "weak":
                status = "candidate"  # weak relations are never confirmed
            elif _stored_scope_status(connection, task_id, to_asset["asset_id"]) == "confirmed" and strength == "strong":
                status = "confirmed"
            existing_relation = connection.execute(
                """SELECT relation_id,provenance_json FROM asset_relation
                   WHERE from_asset_id=? AND to_asset_id=? AND relation_type=?""",
                (from_asset["asset_id"], to_asset["asset_id"], relation_type),
            ).fetchone()
            if existing_relation is None:
                connection.execute(
                    """INSERT INTO asset_relation(relation_id,task_id,from_asset_id,to_asset_id,
                                                  relation_type,relation_strength,status,provenance_json)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (f"relation-{uuid.uuid4().hex[:16]}", task_id, from_asset["asset_id"],
                     to_asset["asset_id"], relation_type, strength, status, provenance),
                )
                relations_created += 1
            else:
                merged = _merge_provenance(existing_relation["provenance_json"], entry["provenance"])
                connection.execute(
                    "UPDATE asset_relation SET provenance_json=? WHERE relation_id=?",
                    (json.dumps(merged, ensure_ascii=True, sort_keys=True), existing_relation["relation_id"]),
                )
                relations_updated += 1

    return MergeSummary(
        status="completed",
        observations_processed=processed,
        observations_skipped=skipped_observations,
        assets_created=created,
        assets_updated=updated,
        relations_created=relations_created,
        relations_updated=relations_updated,
        conflicts_created=conflicts_created,
        skipped=tuple(workspace.skipped),
    )


def _stored_scope_status(connection, task_id: str, asset_id: str) -> str:
    row = connection.execute(
        "SELECT scope_status FROM asset WHERE task_id=? AND asset_id=?",
        (task_id, asset_id),
    ).fetchone()
    return row["scope_status"] if row else "candidate"


def _merge_provenance(existing_json: str, additions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    try:
        existing = json.loads(existing_json)
    except json.JSONDecodeError:
        existing = []
    if not isinstance(existing, list):
        existing = []
    seen = {json.dumps(item, sort_keys=True) for item in existing}
    for item in additions:
        blob = json.dumps(item, sort_keys=True)
        if blob not in seen:
            existing.append(item)
            seen.add(blob)
    return existing
