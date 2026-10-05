from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .errors import PolicyDeniedError

# BudgetRegistry 跟踪表上限；超出后淘汰最旧任务，防止内存无界增长。
MAX_TRACKED_TASKS = 256


def _evict_oldest(mapping: dict, cap: int) -> None:
    while len(mapping) >= cap:
        mapping.pop(next(iter(mapping)))


@dataclass
class Budget:
    max_requests: int
    max_estimated_fpoints: int
    max_total_results: int
    requests_used: int = 0
    estimated_fpoints_used: int = 0
    actual_fpoints_used: int = 0
    results_seen: int = 0
    task_id: str = "default"

    def reserve_request(self) -> None:
        if self.requests_used >= self.max_requests:
            # R3 lesson: a caller that meant to use its own pool but fell
            # into "default" only found out from this error. Name the pool
            # and name the escape hatch.
            raise PolicyDeniedError(
                f"request budget exhausted ({self.max_requests} requests) "
                f"for task '{self.task_id}' - pass a different task_id to "
                "start a fresh pool"
            )
        if self.estimated_fpoints_used >= self.max_estimated_fpoints:
            raise PolicyDeniedError(
                f"estimated F-point budget exhausted ({self.max_estimated_fpoints})"
            )
        self.requests_used += 1
        self.estimated_fpoints_used += 1

    def record_response(self, data: dict[str, Any]) -> None:
        rows = data.get("results")
        if isinstance(rows, list):
            self.results_seen += len(rows)
        consumed = data.get("consumed_fpoint")
        if isinstance(consumed, (int, float)) and consumed >= 0:
            self.actual_fpoints_used += int(consumed)
        required = data.get("required_fpoints")
        if isinstance(required, (int, float)) and required >= 0:
            self.estimated_fpoints_used = max(
                self.estimated_fpoints_used,
                self.actual_fpoints_used + int(required),
            )

    def check_result_capacity(self) -> None:
        if self.results_seen >= self.max_total_results:
            raise PolicyDeniedError(
                f"result budget exhausted ({self.max_total_results} records)"
            )

    def snapshot(self) -> dict[str, int]:
        return {
            "requests_used": self.requests_used,
            "max_requests": self.max_requests,
            "estimated_fpoints_used": self.estimated_fpoints_used,
            "actual_fpoints_used": self.actual_fpoints_used,
            "max_estimated_fpoints": self.max_estimated_fpoints,
            "results_seen": self.results_seen,
            "max_total_results": self.max_total_results,
        }


class BudgetRegistry:
    def __init__(self, *, max_requests: int, max_estimated_fpoints: int, max_total_results: int):
        self._limits = {
            "max_requests": max_requests,
            "max_estimated_fpoints": max_estimated_fpoints,
            "max_total_results": max_total_results,
        }
        self._budgets: dict[str, Budget] = {}

    def get(self, task_id: str) -> Budget:
        if task_id not in self._budgets:
            _evict_oldest(self._budgets, MAX_TRACKED_TASKS)
            self._budgets[task_id] = Budget(**self._limits, task_id=task_id)
        return self._budgets[task_id]

    def snapshot(self, task_id: str = "default") -> dict[str, int]:
        return self.get(task_id).snapshot()
