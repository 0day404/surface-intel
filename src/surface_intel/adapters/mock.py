from __future__ import annotations

from .base import AdapterManifest, AdapterResult, TaskContext


class MockPassiveAdapter:
    manifest = AdapterManifest("mock_passive", "1.0", ("passive_observation",))

    async def health(self, context: TaskContext) -> AdapterResult:
        return AdapterResult("completed", request_id=f"health-{context.task_id}")

    async def validate(self, action: dict, context: TaskContext) -> AdapterResult:
        return AdapterResult("completed")

    async def execute(self, action: dict, context: TaskContext) -> AdapterResult:
        target = action.get("target", "")
        return AdapterResult(
            "completed",
            observations=[{"target": target, "source": self.manifest.name, "status": "empty"}],
            evidence_refs=[],
            request_id=f"mock-{context.task_id}",
        )

    async def cancel(self, action: dict, context: TaskContext) -> AdapterResult:
        return AdapterResult("cancelled")


class MockFofaAdapter:
    manifest = AdapterManifest(
        "mock_fofa", "1.0", ("search", "pagination", "cursor"), supports_pagination=True, supports_resume=True, cost_units=1
    )

    def __init__(self, pages: list[dict]):
        self.pages = pages
        self.calls: list[dict] = []

    async def health(self, context: TaskContext) -> AdapterResult:
        return AdapterResult("completed", request_id=f"health-{context.task_id}")

    async def validate(self, action: dict, context: TaskContext) -> AdapterResult:
        if not action.get("query"):
            return AdapterResult("blocked", error="query_required")
        return AdapterResult("completed")

    async def execute(self, action: dict, context: TaskContext) -> AdapterResult:
        page = int(action.get("page", 1))
        self.calls.append(dict(action))
        if page < 1 or page > len(self.pages):
            return AdapterResult("failed", error="page_not_found")
        payload = self.pages[page - 1]
        return AdapterResult(
            payload.get("status", "completed"),
            observations=payload.get("observations", []),
            next_token=payload.get("next_token"),
            has_more=bool(payload.get("has_more")),
            cost_units=int(payload.get("cost_units", 1)),
            request_id=f"fofa-{context.task_id}-{page}",
        )

    async def cancel(self, action: dict, context: TaskContext) -> AdapterResult:
        return AdapterResult("cancelled")
