from enum import StrEnum


class ActionStatus(StrEnum):
    PLANNED = "planned"
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    NEEDS_REVIEW = "needs_review"
    TIMED_OUT = "timed_out"
    UNKNOWN = "unknown"
    PARTIAL = "partial"


class TaskStatus(StrEnum):
    CREATED = "created"
    PREFLIGHT = "preflight"
    AWAITING_SCOPE = "awaiting_scope"
    PLANNED = "planned"
    COLLECTING_PASSIVE = "collecting_passive"
    COLLECTING_FOFA = "collecting_fofa"
    OBSERVING_BROWSER = "observing_browser"
    ANALYZING_ARCHITECTURE = "analyzing_architecture"
    PROBING_AUTHORIZED = "probing_authorized"
    CORRELATING = "correlating"
    EXPORTING = "exporting"
    COMPLETED = "completed"
    PAUSED = "paused"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    NEEDS_REVIEW = "needs_review"
    PARTIAL = "partial"
    FAILED = "failed"


class ActiveLevel(StrEnum):
    PASSIVE_ONLY = "passive_only"
    SAFE_DISCOVERY = "safe_discovery"
    SAFE_PROBE = "safe_probe"
    INTRUSIVE_TEST = "intrusive_test"


class IntegrityStatus(StrEnum):
    COMPLETE = "complete"
    BOUNDED = "bounded"
    PARTIAL = "partial"
    FAILED = "failed"
    UNKNOWN = "unknown"


class CapabilityStatus(StrEnum):
    AVAILABLE = "available"
    DEGRADED = "degraded"
    NOT_CONFIGURED = "not_configured"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"
