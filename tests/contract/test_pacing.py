"""D9: adapter pacing factory defaults, env knobs, and real runner wiring."""
import pytest

from surface_intel.pacing import (
    DEFAULT_MIN_INTERVALS,
    ENV_PREFIX,
    configured_rate_limiter,
)
from surface_intel.ratelimit import RateLimiter


def test_defaults_pace_ct_and_rdap_only():
    assert DEFAULT_MIN_INTERVALS == {"ct": 2.0, "rdap": 1.0}
    for key, expected in (("ct", 2.0), ("rdap", 1.0), ("dns", 0.0),
                          ("safe_http", 0.0), ("safe_port", 0.0),
                          ("playwright", 0.0), ("coordinator", 0.0)):
        limiter = configured_rate_limiter(key)
        assert limiter.min_interval == expected


def test_env_overrides_default(monkeypatch):
    monkeypatch.setenv(ENV_PREFIX + "CT", "5.5")
    assert configured_rate_limiter("ct").min_interval == 5.5
    monkeypatch.setenv(ENV_PREFIX + "SAFE_HTTP", "0.25")
    assert configured_rate_limiter("safe_http").min_interval == 0.25


def test_invalid_env_value_fails_closed_with_variable_name(monkeypatch):
    monkeypatch.setenv(ENV_PREFIX + "RDAP", "abc")
    with pytest.raises(ValueError, match="invalid_" + ENV_PREFIX + "RDAP"):
        configured_rate_limiter("rdap")
    monkeypatch.setenv(ENV_PREFIX + "RDAP", "-1")
    with pytest.raises(ValueError, match="invalid_" + ENV_PREFIX + "RDAP"):
        configured_rate_limiter("rdap")


def test_factory_accepts_injected_clock_and_sleeper():
    class _Clock:
        def __init__(self):
            self.now = 0.0

        def __call__(self):
            return self.now

    class _Sleeper:
        def __init__(self):
            self.sleeps = []

        async def __call__(self, seconds):
            self.sleeps.append(seconds)
            self_clock.now += seconds

    self_clock = _Clock()
    sleeper = _Sleeper()
    limiter = RateLimiter(2.0, clock=self_clock, sleeper=sleeper)

    import asyncio

    async def _drive():
        await limiter.acquire("adapter:ct")
        self_clock.now = 100.0
        await limiter.acquire("adapter:ct")

    asyncio.run(_drive())
    assert sleeper.sleeps == []


@pytest.mark.asyncio
async def test_dns_runner_actions_are_actually_paced(tmp_path, monkeypatch):
    """接线证明：dns_runner 两个 record_type 动作之间真的经过了限速器。"""
    from surface_intel.dns_runner import run_dns_lookup
    from surface_intel.policy.scope import Scope
    from surface_intel.state.database import Database

    class _Clock:
        def __init__(self):
            self.now = 0.0

        def __call__(self):
            return self.now

    class _Sleeper:
        def __init__(self):
            self.sleeps = []

        async def __call__(self, seconds):
            self.sleeps.append(seconds)
            clock.now += seconds

    class _StubDns:
        async def resolve(self, name, record_type):
            return {"status": "observed", "records": ["93.184.216.34"],
                    "resolver": "stub", "resolved_at": "t0", "resolution_ms": 1}

    clock = _Clock()
    sleeper = _Sleeper()
    monkeypatch.setattr(
        "surface_intel.dns_runner.configured_rate_limiter",
        lambda key, **kw: RateLimiter(2.0, clock=clock, sleeper=sleeper),
    )

    scope = Scope(authorized_domains=("example.com", "*.example.com"), confirmed=True,
                  scope_version="scope-v1")
    db = Database(tmp_path / "task.sqlite")
    db.initialize()
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO task(task_id,status,schema_version,scope_version) VALUES('t','planned','1.0','scope-v1')"
        )
    result = await run_dns_lookup(
        db, _StubDns(), task_id="t", scope=scope, domain="www.example.com",
        record_types=["A", "CNAME"],
    )
    assert result.status == "completed"
    # 第一个动作免等，第二个动作在 target 键上等满 min_interval
    assert sleeper.sleeps == [2.0]


@pytest.mark.asyncio
async def test_ct_runner_receives_its_own_limiter(tmp_path, monkeypatch):
    from surface_intel.ct_runner import run_ct_search
    from surface_intel.policy.scope import Scope
    from surface_intel.state.database import Database

    seen_keys = []

    def _spy(key, **kwargs):
        seen_keys.append(key)
        return RateLimiter(0.0)

    monkeypatch.setattr("surface_intel.ct_runner.configured_rate_limiter", _spy)

    class _StubCt:
        async def search(self, domain):
            return {"status": "empty", "certificates": []}

    scope = Scope(authorized_domains=("example.com",), confirmed=True,
                  scope_version="scope-v1")
    db = Database(tmp_path / "task.sqlite")
    db.initialize()
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO task(task_id,status,schema_version,scope_version) VALUES('t','planned','1.0','scope-v1')"
        )
    await run_ct_search(db, _StubCt(), task_id="t", scope=scope, domain="example.com")
    assert seen_keys == ["ct"]
