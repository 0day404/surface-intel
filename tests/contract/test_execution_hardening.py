"""R5.1 executor hardening: rate limiting, action timeout/cancel semantics,
and the consecutive-error stop."""
import asyncio

import pytest

from surface_intel.adapters.base import AdapterResult, TaskContext
from surface_intel.execution import AdapterExecutor, ExecutionContext
from surface_intel.ratelimit import RateLimiter
from surface_intel.state.database import Database


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class RecordingSleeper:
    def __init__(self, clock):
        self.clock = clock
        self.calls = []

    async def __call__(self, seconds):
        self.calls.append(seconds)
        self.clock.now += seconds


class PassthroughAdapter:
    manifest = None  # set per test

    def __init__(self, result=None, delay=0.0):
        from surface_intel.adapters.base import AdapterManifest

        self.manifest = AdapterManifest("passthrough", "1.0", ("pass",), cost_units=0)
        self.result = result or AdapterResult("completed")
        self.delay = delay
        self.executed = 0
        self.cancelled = 0

    async def validate(self, action, context):
        return AdapterResult("completed")

    async def execute(self, action, context):
        self.executed += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.result

    async def cancel(self, action, context):
        self.cancelled += 1
        return AdapterResult("cancelled")


def _executor(tmp_path):
    db = Database(tmp_path / "task.sqlite")
    db.initialize()
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO task(task_id,status,schema_version,scope_version) VALUES('t','planned','1.0','v1')")
    from surface_intel.adapters.capabilities import CapabilityRegistry

    return db, AdapterExecutor(db, CapabilityRegistry([]))


def _context(**overrides):
    defaults = dict(task_id="t", scope_version="v1", scope_confirmed=True)
    defaults.update(overrides)
    return ExecutionContext(**defaults)


def test_rate_limiter_paces_per_key_and_keeps_timelines_independent():
    clock = FakeClock()
    sleeper = RecordingSleeper(clock)
    limiter = RateLimiter(5.0, clock=clock, sleeper=sleeper)
    assert limiter.min_interval == 5.0

    # the first acquire on a key never waits
    assert asyncio.run(limiter.acquire("adapter:x")) == 0.0
    # the second acquire on the same key waits the full interval
    assert asyncio.run(limiter.acquire("adapter:x")) == 5.0
    assert sleeper.calls == [5.0]
    # an independent key timeline starts fresh (no wait)
    assert asyncio.run(limiter.acquire("target:www.example.com")) == 0.0
    # the adapter key paces again while the target key is still inside its window
    assert asyncio.run(limiter.acquire("adapter:x", "target:www.example.com")) == 5.0


def test_rate_limiter_rejects_negative_interval():
    with pytest.raises(ValueError, match="invalid_min_interval"):
        RateLimiter(-1.0)


@pytest.mark.asyncio
async def test_executor_applies_rate_limit_before_execute(tmp_path):
    db, executor = _executor(tmp_path)
    adapter = PassthroughAdapter()
    executor.registry.register(adapter.manifest)
    clock = FakeClock()
    limiter = RateLimiter(10.0, clock=clock, sleeper=RecordingSleeper(clock))
    context = _context(rate_limiter=limiter)
    for _ in range(3):
        await executor.execute(adapter, {"action_id": "a", "target": "www.example.com",
                                         "capability": "pass"}, context)
    # three executions through the same adapter key -> two paced waits
    assert adapter.executed == 3
    assert limiter._last["adapter:passthrough"] == clock.now
    assert limiter._last["target:www.example.com"] == clock.now


@pytest.mark.asyncio
async def test_timeout_cancels_and_reports_cancelled_external_state(tmp_path):
    db, executor = _executor(tmp_path)
    adapter = PassthroughAdapter(delay=5.0)
    executor.registry.register(adapter.manifest)
    context = _context(action_timeout=0.01)
    result = await executor.execute(adapter, {"action_id": "a", "target": "x",
                                              "capability": "pass"}, context)
    assert result.status == "timed_out"
    assert result.error == "timeout"
    assert result.external_state == "cancelled"
    assert adapter.cancelled == 1


@pytest.mark.asyncio
async def test_failed_cancel_marks_external_state_unknown(tmp_path):
    class UncancellableAdapter(PassthroughAdapter):
        async def cancel(self, action, context):
            raise RuntimeError("cancel pipe broken")

    db, executor = _executor(tmp_path)
    adapter = UncancellableAdapter(delay=5.0)
    executor.registry.register(adapter.manifest)
    context = _context(action_timeout=0.01)
    result = await executor.execute(adapter, {"action_id": "a", "target": "x",
                                              "capability": "pass"}, context)
    assert result.status == "timed_out"
    # cancel failure must never pretend the external end-state is known
    assert result.external_state == "unknown"


@pytest.mark.asyncio
async def test_consecutive_errors_trip_stop_and_success_resets(tmp_path):
    db, executor = _executor(tmp_path)
    adapter = PassthroughAdapter(AdapterResult("failed", error="boom"))
    executor.registry.register(adapter.manifest)
    context = _context(max_consecutive_errors=3)
    for _ in range(3):
        await executor.execute(adapter, {"action_id": "a", "target": "x",
                                         "capability": "pass"}, context)
    assert context.consecutive_errors == 3
    assert context.stop_requested is True
    assert context.stop_reason == "consecutive_errors"

    fresh = _context(max_consecutive_errors=3)
    ok = PassthroughAdapter(AdapterResult("completed"))
    executor.registry.register(ok.manifest)
    await executor.execute(ok, {"action_id": "b", "target": "x", "capability": "pass"}, fresh)
    assert fresh.consecutive_errors == 0
    await executor.execute(adapter, {"action_id": "c", "target": "x", "capability": "pass"}, fresh)
    assert fresh.consecutive_errors == 1
    assert fresh.stop_requested is False
