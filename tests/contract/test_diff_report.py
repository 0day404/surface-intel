"""B7: 差量复查报告——SnapshotDiff → markdown 一页的渲染契约。"""
from surface_intel.monitor import SnapshotDiff
from surface_intel.reporting.diff_report import render_diff_report


def _diff(**overrides):
    base = dict(
        scope_version="scope-v2",
        baseline_scope_version="scope-v1",
        scope_version_match=False,
        added=[{"asset_type": "domain", "normalized_value": "new.example.com",
                "scope_status": "confirmed", "confidence": "high",
                "architecture_status": "owned", "current_status": "current"}],
        disappeared=[{"asset_type": "host", "normalized_value": "old.example.com",
                      "scope_status": "confirmed"}],
        changed=[{"asset_type": "ip", "normalized_value": "203.0.113.5",
                  "fields": ["architecture_status"],
                  "baseline": {"architecture_status": "shared"},
                  "current": {"architecture_status": "exclusive"}}],
        reappeared=[{"asset_type": "domain", "normalized_value": "back.example.com"}],
        unchanged=7,
    )
    base.update(overrides)
    return SnapshotDiff(**base)


def test_full_diff_renders_all_four_sections():
    markdown = render_diff_report(
        _diff(), task_id="t-1", baseline_label="checkpoint cp-1",
        against_label="当前资产集", generated_at="2026-10-05T00:00:00+00:00")
    assert "# 差量复查报告 — t-1" in markdown
    assert "- 基线: checkpoint cp-1" in markdown
    assert "- 对比: 当前资产集" in markdown
    assert "- 汇总: 新出现 1 · 消失 1 · 变化 1 · 重现 1 · 未变 7" in markdown
    assert "匹配=否" in markdown
    assert "## 新出现（1）" in markdown
    assert "new.example.com" in markdown
    assert "## 消失（1）" in markdown
    assert "old.example.com" in markdown
    # 变化行带字段级 基线→当前 迁移
    assert "architecture_status: shared → exclusive" in markdown
    assert "## 重现（1）" in markdown
    assert "back.example.com" in markdown
    # 表格管道行存在(三列表至少一根分隔行)
    assert markdown.count("|---") >= 3 or markdown.count("| ---") >= 3


def test_empty_diff_still_renders_explicit_sections():
    markdown = render_diff_report(
        _diff(added=[], disappeared=[], changed=[], reappeared=[], unchanged=12),
        task_id="t-2", baseline_label="latest baseline",
        against_label="当前资产集")
    assert "- 汇总: 新出现 0 · 消失 0 · 变化 0 · 重现 0 · 未变 12" in markdown
    assert markdown.count("（无）") == 4


def test_changed_row_lists_multiple_field_transitions():
    diff = _diff(changed=[{
        "asset_type": "domain", "normalized_value": "multi.example.com",
        "fields": ["confidence", "current_status"],
        "baseline": {"confidence": "high", "current_status": "current"},
        "current": {"confidence": "low", "current_status": "disappeared"},
    }])
    markdown = render_diff_report(diff, task_id="t", baseline_label="b",
                                  against_label="a")
    assert ("confidence: high → low; "
            "current_status: current → disappeared") in markdown
