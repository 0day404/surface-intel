import pytest

from surface_intel.fofa_queries import split_fofa_query


def test_long_query_splits_on_conjunctions_and_preserves_order():
    batches = split_fofa_query('domain="a.example" && port="443" && protocol="https"', 25)
    assert len(batches) == 3
    assert [batch.batch_index for batch in batches] == [1, 2, 3]
    assert all(len(batch.query) <= 35 for batch in batches)


def test_query_term_longer_than_limit_is_rejected():
    with pytest.raises(ValueError, match="query_term_exceeds"):
        split_fofa_query('body="xxxxxxxxxxxxxxxxxxxxxxxx"', 20)
