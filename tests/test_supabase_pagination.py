from typing import Any, List
from unittest.mock import patch

import pytest

from app.database.supabase_client import fetch_all


# ---------------------------------------------------------------------------
# Mock Supabase client
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, data: List[dict]):
        self.data = data


class _FakeTable:
    def __init__(self, pages: List[List[dict]]):
        self._pages = list(pages)
        self._call_idx = 0
        # Observation lists
        self.range_calls: List[tuple] = []
        self.eq_calls: List[tuple] = []
        self.in_calls: List[tuple] = []
        self.order_calls: List[tuple] = []
        self.select_calls: List[str] = []
        self.is_calls: List[tuple] = []

    def select(self, select_str: str):
        self.select_calls.append(select_str)
        return self

    def eq(self, key: str, value: Any):
        self.eq_calls.append((key, value))
        return self

    def in_(self, key: str, values):
        self.in_calls.append((key, list(values)))
        return self

    def order(self, col: str, desc: bool = False):
        self.order_calls.append((col, desc))
        return self

    def range(self, start: int, end: int):
        self.range_calls.append((start, end))
        return self

    def execute(self):
        if self._call_idx >= len(self._pages):
            raise AssertionError(
                f"Unexpected execute() call #{self._call_idx + 1} — "
                f"only {len(self._pages)} page(s) configured"
            )
        data = self._pages[self._call_idx]
        self._call_idx += 1
        return _FakeResponse(data)
    
    def is_(self, key: str, value: str):   # ← NEW
        self.is_calls.append((key, value))
        return self


class _FakeSupabase:
    def __init__(self, pages: List[List[dict]]):
        self.table_mock = _FakeTable(pages)

    def table(self, name: str):
        return self.table_mock


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _patch_client(pages: List[List[dict]]):
    """Patch get_supabase_client to return a _FakeSupabase with pages."""
    fake = _FakeSupabase(pages)
    return patch(
        "app.database.supabase_client.get_supabase_client",
        return_value=fake,
    ), fake


def _rows(n: int, start_id: int = 0) -> List[dict]:
    return [{"id": i, "val": f"v{i}"} for i in range(start_id, start_id + n)]


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

def test_page_size_zero_raises():
    with pytest.raises(ValueError, match="page_size must be positive"):
        fetch_all("t", page_size=0)


def test_page_size_negative_raises():
    with pytest.raises(ValueError, match="page_size must be positive"):
        fetch_all("t", page_size=-1)


def test_page_size_exceeds_max_raises():
    with pytest.raises(ValueError, match="hard limit"):
        fetch_all("t", page_size=1001)


def test_empty_table_name_raises():
    with pytest.raises(ValueError, match="table name"):
        fetch_all("")


def test_whitespace_table_name_raises():
    with pytest.raises(ValueError, match="table name"):
        fetch_all("   ")


def test_empty_order_by_raises():
    with pytest.raises(ValueError, match="order_by"):
        fetch_all("t", order_by="")


# ---------------------------------------------------------------------------
# Happy path — pagination mechanics
# ---------------------------------------------------------------------------

def test_empty_result():
    patcher, fake = _patch_client([[]])
    with patcher:
        result = fetch_all("t", page_size=100)
    assert result == []
    assert len(fake.table_mock.range_calls) == 1
    assert fake.table_mock.range_calls[0] == (0, 99)


def test_single_page():
    patcher, fake = _patch_client([_rows(10)])
    with patcher:
        result = fetch_all("t", page_size=100)
    assert len(result) == 10
    assert len(fake.table_mock.range_calls) == 1


def test_multiple_pages():
    page1 = _rows(1000, start_id=0)
    page2 = _rows(500, start_id=1000)
    patcher, fake = _patch_client([page1, page2])
    with patcher:
        result = fetch_all("t", page_size=1000)
    assert len(result) == 1500
    assert len(fake.table_mock.range_calls) == 2
    assert fake.table_mock.range_calls == [(0, 999), (1000, 1999)]


def test_exact_page_size_triggers_extra_empty_query():
    """Exactly 1000 rows = 2 queries: page1 (1000) + page2 (0)."""
    patcher, fake = _patch_client([_rows(1000), []])
    with patcher:
        result = fetch_all("t", page_size=1000)
    assert len(result) == 1000
    assert len(fake.table_mock.range_calls) == 2
    assert fake.table_mock.range_calls == [(0, 999), (1000, 1999)]


def test_pagination_stops_correctly():
    """Verify loop terminates without an extra query when partial page returned."""
    page1 = _rows(1000)
    page2 = _rows(200, start_id=1000)  # partial → stop
    patcher, fake = _patch_client([page1, page2])
    with patcher:
        result = fetch_all("t", page_size=1000)
    assert len(result) == 1200
    # Should be exactly 2 calls, no third
    assert len(fake.table_mock.range_calls) == 2


# ---------------------------------------------------------------------------
# Query construction
# ---------------------------------------------------------------------------

def test_default_order_by_id_ascending():
    patcher, fake = _patch_client([[]])
    with patcher:
        fetch_all("t")
    assert fake.table_mock.order_calls == [("id", False)]


def test_custom_order_by_descending():
    patcher, fake = _patch_client([[]])
    with patcher:
        fetch_all("t", order_by="tanggal", ascending=False)
    assert fake.table_mock.order_calls == [("tanggal", True)]


def test_scalar_filter_uses_eq():
    patcher, fake = _patch_client([[]])
    with patcher:
        fetch_all("t", filters={"kode_saham": "BBCA"})
    assert fake.table_mock.eq_calls == [("kode_saham", "BBCA")]
    assert fake.table_mock.in_calls == []


def test_list_filter_uses_in():
    patcher, fake = _patch_client([[]])
    with patcher:
        fetch_all("t", filters={"kode_saham": ["BBCA", "BBRI"]})
    assert fake.table_mock.in_calls == [("kode_saham", ["BBCA", "BBRI"])]
    assert fake.table_mock.eq_calls == []


def test_mixed_filters():
    patcher, fake = _patch_client([[]])
    with patcher:
        fetch_all(
            "t",
            filters={"kode_saham": ["BBCA", "BBRI"], "source": "CNBC"},
        )
    assert fake.table_mock.in_calls == [("kode_saham", ["BBCA", "BBRI"])]
    assert fake.table_mock.eq_calls == [("source", "CNBC")]


def test_select_projection_passed_through():
    patcher, fake = _patch_client([[]])
    with patcher:
        fetch_all("t", select="id, title")
    assert fake.table_mock.select_calls == ["id, title"]


# ---------------------------------------------------------------------------
# Error propagation
# ---------------------------------------------------------------------------

def test_error_from_execute_propagates():
    """Errors from Supabase .execute() must not be swallowed."""

    class _RaisingTable:
        def select(self, *a, **k):
            return self

        def eq(self, *a, **k):
            return self

        def in_(self, *a, **k):
            return self

        def order(self, *a, **k):
            return self

        def range(self, *a, **k):
            return self

        def execute(self):
            raise RuntimeError("Supabase connection lost")

    class _RaisingSupabase:
        def table(self, name):
            return _RaisingTable()

    with patch(
        "app.database.supabase_client.get_supabase_client",
        return_value=_RaisingSupabase(),
    ):
        with pytest.raises(RuntimeError, match="Supabase connection lost"):
            fetch_all("t")
            
            
# ---------------------------------------------------------------------------
# NULL filter (IS NULL) support — TASK 001E
# ---------------------------------------------------------------------------

def test_none_value_maps_to_is_null():
    patcher, fake = _patch_client([[]])
    with patcher:
        fetch_all("t", filters={"sentiment_label": None})
    assert fake.table_mock.is_calls == [("sentiment_label", "null")]
    assert fake.table_mock.eq_calls == []
    assert fake.table_mock.in_calls == []


def test_none_filter_combined_with_scalar():
    patcher, fake = _patch_client([[]])
    with patcher:
        fetch_all(
            "t",
            filters={"sentiment_label": None, "source": "CNBC"},
        )
    assert fake.table_mock.is_calls == [("sentiment_label", "null")]
    assert fake.table_mock.eq_calls == [("source", "CNBC")]


def test_none_filter_combined_with_list():
    patcher, fake = _patch_client([[]])
    with patcher:
        fetch_all(
            "t",
            filters={"sentiment_label": None, "kode_saham": ["BBCA", "BBRI"]},
        )
    assert fake.table_mock.is_calls == [("sentiment_label", "null")]
    assert fake.table_mock.in_calls == [("kode_saham", ["BBCA", "BBRI"])]


def test_multiple_none_filters():
    patcher, fake = _patch_client([[]])
    with patcher:
        fetch_all(
            "t",
            filters={"sentiment_label": None, "confidence": None},
        )
    assert fake.table_mock.is_calls == [
        ("sentiment_label", "null"),
        ("confidence", "null"),
    ]


def test_empty_list_filter_raises():
    with pytest.raises(ValueError, match="empty list"):
        fetch_all("t", filters={"kode_saham": []})


def test_empty_tuple_filter_raises():
    with pytest.raises(ValueError, match="empty list"):
        fetch_all("t", filters={"kode_saham": ()})


def test_empty_list_filter_fails_before_network():
    """Validation should fail before get_supabase_client is called."""
    with patch(
        "app.database.supabase_client.get_supabase_client"
    ) as mock_client:
        with pytest.raises(ValueError):
            fetch_all("t", filters={"col": []})
        mock_client.assert_not_called()


def test_scalar_filter_unaffected_by_none_support():
    """Regression — scalar string still uses .eq(), not .is_()."""
    patcher, fake = _patch_client([[]])
    with patcher:
        fetch_all("t", filters={"kode_saham": "BBCA"})
    assert fake.table_mock.eq_calls == [("kode_saham", "BBCA")]
    assert fake.table_mock.is_calls == []