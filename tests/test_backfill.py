"""Tests for historical backfill. Mock yfinance + Supabase — no network."""
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from app.models.schemas import StockPrice
from ml.preprocessing.backfill import (
    ChangeRecord,
    _pct_diff,
    backfill_ticker,
    detect_changes,
    fetch_ticker_with_retry,
    upsert_records,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _price(ticker: str, d: date, close: float = 100.0, volume: int = 1000) -> StockPrice:
    return StockPrice(
        kode_saham=ticker, tanggal=d,
        harga_buka=close, harga_tertinggi=close, harga_terendah=close,
        harga_tutup=close, volume=volume,
    )


def _row(ticker: str, d: str, close: float = 100.0, volume: int = 1000) -> dict:
    return {
        "kode_saham": ticker, "tanggal": d,
        "harga_buka": close, "harga_tertinggi": close, "harga_terendah": close,
        "harga_tutup": close, "volume": volume,
    }


# ---------------------------------------------------------------------------
# _pct_diff
# ---------------------------------------------------------------------------

def test_pct_diff_basic():
    assert _pct_diff(100.0, 101.0) == pytest.approx(1.0)
    assert _pct_diff(100.0, 99.0) == pytest.approx(1.0)


def test_pct_diff_zero_old():
    assert _pct_diff(0.0, 0.0) == 0.0
    assert _pct_diff(0.0, 5.0) == float("inf")


# ---------------------------------------------------------------------------
# detect_changes
# ---------------------------------------------------------------------------

def test_detect_changes_no_change():
    new = [_row("BBCA", "2024-01-02", close=100.0)]
    existing = [_row("BBCA", "2024-01-02", close=100.0)]
    assert detect_changes("BBCA", new, existing) == []


def test_detect_changes_above_threshold():
    new = [_row("BBCA", "2024-01-02", close=101.0)]  # +1%
    existing = [_row("BBCA", "2024-01-02", close=100.0)]
    changes = detect_changes("BBCA", new, existing)
    assert len(changes) >= 1
    close_changes = [c for c in changes if c.field == "harga_tutup"]
    assert len(close_changes) == 1
    assert close_changes[0].pct_diff == pytest.approx(1.0)


def test_detect_changes_below_threshold_ignored():
    new = [_row("BBCA", "2024-01-02", close=100.2)]  # +0.2% < 0.5
    existing = [_row("BBCA", "2024-01-02", close=100.0)]
    changes = detect_changes("BBCA", new, existing)
    assert all(c.field != "harga_tutup" for c in changes)


def test_detect_changes_new_date_no_comparison():
    new = [_row("BBCA", "2024-01-02")]
    existing = []  # nothing in DB
    assert detect_changes("BBCA", new, existing) == []


def test_detect_changes_volume_threshold():
    new = [_row("BBCA", "2024-01-02", close=100.0, volume=2000)]  # +100%
    existing = [_row("BBCA", "2024-01-02", close=100.0, volume=1000)]
    changes = detect_changes("BBCA", new, existing)
    vol_changes = [c for c in changes if c.field == "volume"]
    assert len(vol_changes) == 1
    assert vol_changes[0].pct_diff == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# fetch_ticker_with_retry
# ---------------------------------------------------------------------------

def test_fetch_success_first_try():
    with patch("ml.preprocessing.backfill.YahooFinanceScraper") as mock_cls:
        mock_scraper = MagicMock()
        mock_scraper.fetch_historical_data.return_value = [
            _price("BBCA", date(2024, 1, 2))
        ]
        mock_cls.return_value = mock_scraper

        result = fetch_ticker_with_retry("BBCA", period="1y")
        assert len(result) == 1
        assert mock_scraper.fetch_historical_data.call_count == 1


def test_fetch_retries_then_succeeds():
    with patch("ml.preprocessing.backfill.YahooFinanceScraper") as mock_cls, \
         patch("ml.preprocessing.backfill.time.sleep") as mock_sleep:
        mock_scraper = MagicMock()
        mock_scraper.fetch_historical_data.side_effect = [
            RuntimeError("transient"),
            [_price("BBCA", date(2024, 1, 2))],
        ]
        mock_cls.return_value = mock_scraper

        result = fetch_ticker_with_retry("BBCA", max_retries=3)
        assert len(result) == 1
        assert mock_scraper.fetch_historical_data.call_count == 2
        assert mock_sleep.call_count == 1  # slept once before retry


def test_fetch_exhausts_retries():
    with patch("ml.preprocessing.backfill.YahooFinanceScraper") as mock_cls, \
         patch("ml.preprocessing.backfill.time.sleep"):
        mock_scraper = MagicMock()
        mock_scraper.fetch_historical_data.side_effect = RuntimeError("permanent")
        mock_cls.return_value = mock_scraper

        with pytest.raises(RuntimeError, match="permanent"):
            fetch_ticker_with_retry("BBCA", max_retries=2)
        assert mock_scraper.fetch_historical_data.call_count == 2


# ---------------------------------------------------------------------------
# upsert_records
# ---------------------------------------------------------------------------

def test_upsert_records_empty():
    assert upsert_records([]) == 0


def test_upsert_records_calls_supabase():
    with patch("ml.preprocessing.backfill.get_supabase_client") as mock_client:
        mock_table = MagicMock()
        mock_response = MagicMock()
        mock_response.data = [{"id": 1}, {"id": 2}]
        mock_table.upsert.return_value.execute.return_value = mock_response
        mock_client.return_value.table.return_value = mock_table

        count = upsert_records([{"kode_saham": "BBCA", "tanggal": "2024-01-02"}])
        assert count == 2
        mock_table.upsert.assert_called_once()
        # Verify on_conflict string was passed
        call_kwargs = mock_table.upsert.call_args.kwargs
        assert call_kwargs.get("on_conflict") == "kode_saham,tanggal"


# ---------------------------------------------------------------------------
# backfill_ticker (integration of pieces)
# ---------------------------------------------------------------------------

def test_backfill_ticker_ok_new_rows():
    with patch("ml.preprocessing.backfill.fetch_ticker_with_retry") as mock_fetch, \
         patch("ml.preprocessing.backfill.fetch_all", return_value=[]), \
         patch("ml.preprocessing.backfill.upsert_records", return_value=3) as mock_upsert:

        mock_fetch.return_value = [
            _price("BBCA", date(2024, 1, 2)),
            _price("BBCA", date(2024, 1, 3)),
            _price("BBCA", date(2024, 1, 4)),
        ]

        result = backfill_ticker("BBCA", period="1y")

        assert result.status == "ok"
        assert result.rows_fetched == 3
        assert result.rows_new == 3
        assert result.rows_overlap == 0
        assert result.changes == []
        mock_upsert.assert_called_once()


def test_backfill_ticker_dry_run_skips_upsert():
    with patch("ml.preprocessing.backfill.fetch_ticker_with_retry") as mock_fetch, \
         patch("ml.preprocessing.backfill.fetch_all", return_value=[]), \
         patch("ml.preprocessing.backfill.upsert_records") as mock_upsert:

        mock_fetch.return_value = [_price("BBCA", date(2024, 1, 2))]
        result = backfill_ticker("BBCA", dry_run=True)

        assert result.status == "ok"
        assert result.rows_new == 1
        mock_upsert.assert_not_called()


def test_backfill_ticker_empty_fetch():
    with patch("ml.preprocessing.backfill.fetch_ticker_with_retry", return_value=[]):
        result = backfill_ticker("BBCA")
        assert result.status == "empty"


def test_backfill_ticker_fetch_error():
    with patch(
        "ml.preprocessing.backfill.fetch_ticker_with_retry",
        side_effect=RuntimeError("boom"),
    ):
        result = backfill_ticker("BBCA")
        assert result.status == "error"
        assert "boom" in (result.error or "")


def test_backfill_ticker_logs_material_changes():
    existing = [_row("BBCA", "2024-01-02", close=100.0)]
    with patch("ml.preprocessing.backfill.fetch_ticker_with_retry") as mock_fetch, \
         patch("ml.preprocessing.backfill.fetch_all", return_value=existing), \
         patch("ml.preprocessing.backfill.upsert_records", return_value=1):

        mock_fetch.return_value = [
            _price("BBCA", date(2024, 1, 2), close=101.0)  # +1% > 0.5%
        ]
        result = backfill_ticker("BBCA")

        assert result.status == "ok"
        assert result.rows_overlap == 1
        assert len(result.changes) >= 1