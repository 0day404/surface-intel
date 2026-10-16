from fofa_mcp.budget import Budget
import pytest

from fofa_mcp.errors import ResponseSchemaError
from fofa_mcp.formatters import format_search, format_stats


def test_search_result_exposes_bounded_collection_state():
    budget = Budget(20, 30, 2000)
    budget.reserve_request()
    budget.record_response(
        {
            "results": [["1.2.3.4", "443"]],
            "size": 1,
            "page": 1,
            "next": "cursor-1",
            "consumed_fpoint": 1,
        }
    )
    result = format_search(
        {
            "results": [["1.2.3.4", "443"]],
            "size": 1,
            "page": 1,
            "next": "cursor-1",
            "consumed_fpoint": 1,
        },
        ["ip", "port"],
        budget,
        page_size=1,
        pagination_mode="cursor",
    )
    assert result["collection"]["completeness"] == "bounded"
    assert result["collection"]["has_more"] is True
    assert result["pagination"]["next_token"] == "cursor-1"
    assert result["account"]["switched"] is False


def test_raw_output_is_opt_in():
    budget = Budget(20, 30, 2000)
    result = format_search(
        {"results": [["1.2.3.4", "443"]]},
        ["ip", "port"],
        budget,
        page_size=100,
        pagination_mode="page",
        output_format="raw",
    )
    assert result["format"] == "raw"
    assert result["data"] == [["1.2.3.4", "443"]]
    assert "raw_results" not in result


def test_has_more_uses_total_not_row_count():
    budget = Budget(20, 30, 2000)
    rows = [["1.2.3.4", "443"]] * 100
    # 恰好整页但命中总数=100：不再有更多（旧行为按行数猜会误报 True）。
    full_page = format_search(
        {"results": rows, "size": 100, "page": 1},
        ["ip", "port"],
        budget,
        page_size=100,
        pagination_mode="page",
    )
    assert full_page["summary"]["has_more"] is False
    # 命中总数 200：还有更多。
    more = format_search(
        {"results": rows, "size": 200, "page": 1},
        ["ip", "port"],
        budget,
        page_size=100,
        pagination_mode="page",
    )
    assert more["summary"]["has_more"] is True
    # 缺 size：退化为仅看 next 游标。
    no_total = format_search(
        {"results": rows},
        ["ip", "port"],
        budget,
        page_size=100,
        pagination_mode="page",
    )
    assert no_total["summary"]["has_more"] is False


def test_search_mapping_rejects_field_count_mismatch():
    with pytest.raises(ResponseSchemaError):
        format_search(
            {"results": [["1.2.3.4"]]},
            ["ip", "port"],
            Budget(20, 30, 2000),
            page_size=100,
            pagination_mode="page",
        )


def test_stats_result_is_agent_ready():
    result = format_stats(
        {"results": {"country": [["CN", 4], ["US", 2]]}},
        fields=["country"],
        top_n=10,
        budget=Budget(20, 30, 2000),
    )
    assert result["data"] == [
        {"field": "country", "value": "CN", "count": 4},
        {"field": "country", "value": "US", "count": 2},
    ]
