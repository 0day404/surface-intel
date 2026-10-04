"""B7: 差量复查报告——把 SnapshotDiff 渲染成"新出现/消失/变化/重现"一页。

从一次性收集走向资产监控的桥:baseline-diff 出 JSON,这里出人读的
markdown 单页(全量列出,不截断)。纯函数,无 IO;CLI 层负责落盘。
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..monitor import SnapshotDiff


def render_diff_report(
    diff: SnapshotDiff,
    *,
    task_id: str,
    baseline_label: str,
    against_label: str,
    generated_at: str | None = None,
) -> str:
    """Render a one-page markdown delta report. All sections always render
    (空节写"（无）"), so a steady state still reads as an explicit result."""
    lines = [
        f"# 差量复查报告 — {task_id}",
        "",
        f"- 基线: {baseline_label}",
        f"- 对比: {against_label}",
        f"- scope 版本: {diff.baseline_scope_version or '—'} → {diff.scope_version or '—'}"
        f"（匹配={'是' if diff.scope_version_match else '否'}）",
        f"- 生成时间: {generated_at or '—'}",
        f"- 汇总: 新出现 {len(diff.added)} · 消失 {len(diff.disappeared)}"
        f" · 变化 {len(diff.changed)} · 重现 {len(diff.reappeared)}"
        f" · 未变 {diff.unchanged}",
        "",
    ]
    lines.extend(_section("新出现", diff.added,
                          ("资产类型", "值", "归属", "置信度"), _added_cells))
    lines.extend(_section("消失", diff.disappeared,
                          ("资产类型", "值", "scope 状态"), _disappeared_cells))
    lines.extend(_section("变化", diff.changed,
                          ("资产类型", "值", "变化"), _changed_cells))
    lines.extend(_section("重现", diff.reappeared,
                          ("资产类型", "值"), _reappeared_cells))
    return "\n".join(lines).rstrip() + "\n"


def _section(title: str, items: list[dict[str, Any]], columns: tuple[str, ...],
             cell_renderer: Callable[[dict[str, Any]], tuple[Any, ...]]) -> list[str]:
    header = [f"## {title}（{len(items)}）", ""]
    if not items:
        return header + ["（无）", ""]
    rows = [cell_renderer(item) for item in items]
    widths = [max(len(str(row[i])) for row in rows) for i in range(len(columns))]

    def _fmt(cells: tuple[Any, ...]) -> str:
        return "| " + " | ".join(str(cell).ljust(widths[i])
                                 for i, cell in enumerate(cells)) + " |"

    lines = header + [_fmt(columns),
                      "|" + "|".join("-" * (w + 2) for w in widths) + "|"]
    lines.extend(_fmt(row) for row in rows)
    return lines + [""]


def _added_cells(item: dict[str, Any]) -> tuple[Any, ...]:
    return (item.get("asset_type", ""), item.get("normalized_value", ""),
            item.get("architecture_status", ""), item.get("confidence", ""))


def _disappeared_cells(item: dict[str, Any]) -> tuple[Any, ...]:
    return (item.get("asset_type", ""), item.get("normalized_value", ""),
            item.get("scope_status", ""))


def _reappeared_cells(item: dict[str, Any]) -> tuple[Any, ...]:
    return (item.get("asset_type", ""), item.get("normalized_value", ""))


def _changed_cells(item: dict[str, Any]) -> tuple[Any, ...]:
    fields = item.get("fields", [])
    transitions = "; ".join(
        f"{name}: {item.get('baseline', {}).get(name)} → "
        f"{item.get('current', {}).get(name)}" for name in fields)
    return (item.get("asset_type", ""), item.get("normalized_value", ""),
            transitions)
