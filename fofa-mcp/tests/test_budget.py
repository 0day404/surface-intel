import pytest

from fofa_mcp.budget import Budget, BudgetRegistry
from fofa_mcp.errors import PolicyDeniedError


def test_budget_tracks_actual_cost_and_results():
    budget = Budget(2, 3, 100)
    budget.reserve_request()
    budget.record_response({"results": [["a"], ["b"]], "consumed_fpoint": 2})
    assert budget.actual_fpoints_used == 2
    assert budget.results_seen == 2
    assert budget.snapshot()["requests_used"] == 1


def test_budget_rejects_request_limit():
    budget = Budget(1, 3, 100)
    budget.reserve_request()
    with pytest.raises(PolicyDeniedError):
        budget.reserve_request()


def test_budget_exhaustion_names_the_pool_and_the_escape_hatch():
    # R3 lesson: a caller that meant its own pool but fell into "default"
    # needs the pool name and the reset move in the error itself
    budget = Budget(1, 3, 100, task_id="ceramtec-r3")
    budget.reserve_request()
    with pytest.raises(PolicyDeniedError, match="task 'ceramtec-r3'"):
        budget.reserve_request()
    registry = BudgetRegistry(max_requests=0, max_estimated_fpoints=5, max_total_results=100)
    with pytest.raises(PolicyDeniedError, match="different task_id"):
        registry.get("default").reserve_request()


def test_budget_registry_isolates_tasks():
    registry = BudgetRegistry(
        max_requests=2,
        max_estimated_fpoints=3,
        max_total_results=10,
    )
    first = registry.get("first")
    second = registry.get("second")
    first.reserve_request()
    assert first.requests_used == 1
    assert second.requests_used == 0


def test_budget_registry_evicts_oldest_beyond_cap(monkeypatch):
    from fofa_mcp import budget as budget_module

    monkeypatch.setattr(budget_module, "MAX_TRACKED_TASKS", 2)
    registry = BudgetRegistry(max_requests=5, max_estimated_fpoints=5, max_total_results=100)
    registry.get("first")
    registry.get("second")
    registry.get("third")
    assert "first" not in registry._budgets
    assert registry.get("third").requests_used == 0
