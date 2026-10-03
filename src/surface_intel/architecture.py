from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .state.database import Database

# Architecture statuses are intentionally two-level: passive signals can only
# ever produce *_candidate (one signal) or *_likely (corroborated signals).
# The plan's confirmed_direct / confirmed_hosted / origin_candidate statuses
# are reserved for the active phase (R6) and human review - this layer never
# writes them. `third_party` marks out-of-scope assets related from in-scope
# ones; `unknown` is the default.
ARCHITECTURE_STATUSES = (
    "unknown",
    "cdn_candidate", "cdn_likely",
    "waf_candidate", "waf_likely",
    "lb_candidate", "lb_likely",
    "cloud_hosting_candidate", "cloud_hosting_likely",
    "dns_candidate", "dns_likely",
    "email_candidate", "email_likely",
    "third_party",
)
CONFIRMED_STATUSES = ("confirmed_direct", "confirmed_hosted")  # never written here
LIKELIHOOD_THRESHOLD = 2  # corroborating signals needed for *_likely

# Category priority when several categories are likely at once (first wins).
CATEGORY_PRIORITY = ("cdn", "waf", "lb", "cloud_hosting", "dns", "email")

# (vendor, categories, domain suffixes). Suffix matching is exact-endswith on
# the lowercased value. The list is a frozen, documented heuristic seed -
# extend via new signatures, never by loosening the match rule.
VENDOR_SUFFIXES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("cloudflare", ("cdn", "waf"), (".cdn.cloudflare.net",)),
    ("cloudflare", ("dns",), (".ns.cloudflare.com",)),
    ("akamai", ("cdn", "waf"), (".akamaiedge.net", ".akamai.net", ".edgekey.net",
                                ".edgesuite.net", ".akamaihd.net")),
    ("cloudfront", ("cdn",), (".cloudfront.net",)),
    ("fastly", ("cdn",), (".fastly.net", ".fslb.net")),
    ("cdn77", ("cdn",), (".cdn77.org",)),
    ("azure-cdn", ("cdn",), (".azureedge.net", ".vo.msecnd.net")),
    ("imperva", ("waf",), (".impervadns.net", ".incapsula.com")),
    ("aws-elb", ("lb",), (".elb.amazonaws.com",)),
    ("azure-traffic-manager", ("lb",), (".trafficmanager.net",)),
    ("google-cloud", ("cloud_hosting",), (".googleusercontent.com",)),
    ("aws", ("cloud_hosting",), (".amazonaws.com",)),
    ("azure", ("cloud_hosting",), (".cloudapp.azure.com", ".azurewebsites.net")),
    ("google-dns", ("dns",), (".googledomains.com",)),
    ("route53", ("dns",), (".awsdns.net", ".awsdns.com", ".awsdns.org", ".awsdns.co.uk")),
    ("ultradns", ("dns",), (".ultradns.org", ".ultradns.net")),
    ("registrar-dns", ("dns",), (".registrar-servers.com", ".dnsmadeeasy.com")),
    ("outlook", ("email",), (".mail.protection.outlook.com", ".outlook.com")),
    ("google-workspace", ("email",), (".aspmx.l.google.com", ".googlemail.com")),
)

# Organization / issuer keyword matches (substring, case-insensitive).
ORG_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("cloudflare", ("cdn", "waf")),
    ("akamai", ("cdn", "waf")),
    ("fastly", ("cdn",)),
    ("imperva", ("waf",)),
    ("incapsula", ("waf",)),
    ("amazon", ("cloud_hosting",)),
    ("aws", ("cloud_hosting",)),
    ("microsoft", ("cloud_hosting",)),
    ("azure", ("cloud_hosting",)),
    ("google cloud", ("cloud_hosting",)),
    ("hetzner", ("cloud_hosting",)),
    ("ovh", ("cloud_hosting",)),
    ("digitalocean", ("cloud_hosting",)),
    ("linode", ("cloud_hosting",)),
    ("alibaba", ("cloud_hosting",)),
    ("tencent", ("cloud_hosting",)),
)

CATEGORY_STATUS = {
    "cdn": ("cdn_candidate", "cdn_likely"),
    "waf": ("waf_candidate", "waf_likely"),
    "lb": ("lb_candidate", "lb_likely"),
    "cloud_hosting": ("cloud_hosting_candidate", "cloud_hosting_likely"),
    "dns": ("dns_candidate", "dns_likely"),
    "email": ("email_candidate", "email_likely"),
}


@dataclass(frozen=True)
class ArchitectureSummary:
    status: str
    assets_classified: int
    assets_third_party: int
    assets_unchanged: int


def match_suffix(value: str) -> list[dict[str, Any]]:
    """Vendor signature matches for a DNS-style hostname value."""
    cleaned = value.strip().rstrip(".").lower()
    matches = []
    for vendor, categories, suffixes in VENDOR_SUFFIXES:
        for suffix in suffixes:
            if cleaned.endswith(suffix):
                matches.append({"vendor": vendor, "categories": list(categories),
                                "matched_on": suffix, "value": cleaned})
                break
    return matches


def match_keyword(text: str) -> list[dict[str, Any]]:
    """Vendor keyword matches for organization or certificate-issuer strings."""
    cleaned = str(text or "").lower()
    matches = []
    for keyword, categories in ORG_KEYWORDS:
        if keyword in cleaned:
            matches.append({"vendor": keyword, "categories": list(categories),
                            "matched_on": keyword, "value": cleaned[:120]})
    return matches


def classify_signals(signals: list[dict[str, Any]]) -> str:
    """Deterministic status from collected signals; never a confirmed_* value.

    A category needs `LIKELIHOOD_THRESHOLD` corroborating signals for *_likely;
    a single signal yields *_candidate; several likely categories resolve by
    CATEGORY_PRIORITY. Signals may carry a specific `category` (as the
    annotator does) or only a `categories` list (direct callers) - exactly one
    expansion happens either way.
    """
    by_category: dict[str, list[dict[str, Any]]] = {}
    for signal in signals:
        categories = [signal["category"]] if "category" in signal else signal["categories"]
        for category in categories:
            by_category.setdefault(category, []).append(signal)
    likely = [
        category for category, entries in by_category.items()
        if len({json.dumps(entry, sort_keys=True) for entry in entries}) >= LIKELIHOOD_THRESHOLD
    ]
    if likely:
        winner = next(c for c in CATEGORY_PRIORITY if c in likely)
        return CATEGORY_STATUS[winner][1]
    candidates = [category for category, entries in by_category.items() if entries]
    if candidates:
        winner = next(c for c in CATEGORY_PRIORITY if c in candidates)
        return CATEGORY_STATUS[winner][0]
    return "unknown"


def annotate_architecture(db: Database, task_id: str) -> ArchitectureSummary:
    """Write architecture_status / architecture_json for one task's assets.

    Purely derived from stored observations and relations - idempotent, no
    network. Evidence entries always reference the observation ids they came
    from so every classification stays traceable.
    """
    # pass 1: collect signals per asset value from observations
    signals_by_asset: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in db.rows(
        "SELECT observation_id,source,payload_json,created_at FROM observation WHERE task_id=? ORDER BY rowid",
        (task_id,),
    ):
        try:
            payload = json.loads(row["payload_json"])
        except json.JSONDecodeError:
            continue
        source = row["source"]
        observation_ref = {"observation_id": row["observation_id"], "source": source}
        anchor = _anchor_key(payload, source)
        if anchor is None:
            continue
        if source == "dns_resolver":
            record_type = str(payload.get("record_type", "")).upper()
            if record_type in {"CNAME", "NS"}:
                for record in payload.get("records", ()) or []:
                    for match in match_suffix(str(record)):
                        for category in match["categories"]:
                            signals_by_asset.setdefault(anchor, []).append({
                                **match, "category": category, **observation_ref,
                                "signal": f"dns_{record_type.lower()}",
                            })
        elif source == "ct_log":
            for certificate in payload.get("certificates", ()) or []:
                for match in match_keyword(str(certificate.get("issuer", ""))):
                    for category in match["categories"]:
                        signals_by_asset.setdefault(anchor, []).append({
                            **match, "category": category, **observation_ref,
                            "signal": "cert_issuer",
                        })
        elif source == "rdap_lookup":
            if payload.get("status") != "observed":
                continue
            record = payload.get("record") or {}
            for org in [*(record.get("organizations") or ()), record.get("registrar")]:
                for match in match_keyword(str(org)):
                    for category in match["categories"]:
                        signals_by_asset.setdefault(anchor, []).append({
                            **match, "category": category, **observation_ref,
                            "signal": "rdap_org",
                        })

    # pass 2: resolve asset ids and write statuses
    classified = third_party = unchanged = 0
    with db.connect() as connection:
        assets = connection.execute(
            "SELECT asset_id,asset_type,normalized_value,scope_status,architecture_status "
            "FROM asset WHERE task_id=?", (task_id,),
        ).fetchall()
        for asset in assets:
            key = (asset["asset_type"], asset["normalized_value"])
            signals = signals_by_asset.get(key, [])
            if signals:
                status = classify_signals(signals)
                # no derivation timestamp: re-annotation must be byte-identical
                evidence = [dict(signal) for signal in signals]
            elif asset["scope_status"] == "candidate" and _related_from_confirmed(connection, asset["asset_id"]):
                status = "third_party"
                evidence = []
            else:
                unchanged += 1
                continue
            connection.execute(
                "UPDATE asset SET architecture_status=?, architecture_json=? WHERE asset_id=?",
                (status, json.dumps(evidence, ensure_ascii=True, sort_keys=True), asset["asset_id"]),
            )
            if status == "third_party":
                third_party += 1
            else:
                classified += 1
    return ArchitectureSummary(
        status="completed",
        assets_classified=classified,
        assets_third_party=third_party,
        assets_unchanged=unchanged,
    )


def _anchor_key(payload: dict[str, Any], source: str) -> tuple[str, str] | None:
    """The (asset_type, value) key a signal attaches to, mirroring merger anchors."""
    target = str(payload.get("target", "")).strip().rstrip(".").lower()
    if not target:
        return None
    if source == "rdap_lookup":
        kind = {"domain": "domain", "ip": "ip", "asn": "asn"}.get(str(payload.get("target_kind", "")))
        return (kind, target) if kind else None
    return ("domain", target)


def _related_from_confirmed(connection, asset_id: str) -> bool:
    row = connection.execute(
        """SELECT 1 FROM asset_relation r
           JOIN asset src ON src.asset_id = r.from_asset_id
           WHERE r.to_asset_id=? AND src.scope_status='confirmed' LIMIT 1""",
        (asset_id,),
    ).fetchone()
    return row is not None
