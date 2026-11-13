"""Tests for TASK 106 dataset validator. In-memory + tmp_path only."""
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ml.validation.validate_dataset import (
    EXPECTED_COLUMNS,
    ValidationReport,
    _check_dtypes,
    build_coverage_check,
    compute_consistency_metrics,
    render_markdown,
    run_all_checks,
    save_report_atomic,
    validate_freshness,
    validate_price_integrity,
    validate_schema_structure,
    validate_sentiment_integrity,
    main as cli_main,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

BASE_DATE = date(2025, 1, 6)


def _base_row(
    ticker="BBCA",
    trading_date="2025-01-06",
    open_=100.0, high=105.0, low=95.0, close=100.0,
    volume=1000, return_1d=0.0,
    news_count=0, sentiment_mean=None, sentiment_std=None,
    positive_ratio=None, negative_ratio=None,
    availability_live_ratio=None,
):
    return {
        "ticker": ticker,
        "trading_date": trading_date,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "return_1d": return_1d,
        "news_count": news_count,
        "sentiment_mean": sentiment_mean,
        "sentiment_std": sentiment_std,
        "positive_ratio": positive_ratio,
        "negative_ratio": negative_ratio,
        "availability_live_ratio": availability_live_ratio,
    }


def _valid_base() -> pd.DataFrame:
    df = pd.DataFrame([
        _base_row(ticker="BBCA", trading_date="2025-01-06"),
        _base_row(ticker="BBCA", trading_date="2025-01-07"),
        _base_row(
            ticker="BBRI", trading_date="2025-01-06",
            news_count=2, sentiment_mean=0.8, sentiment_std=0.5,
            positive_ratio=0.5, negative_ratio=0.5,
            availability_live_ratio=1.0,
        ),
    ])
    df["trading_date"] = pd.to_datetime(df["trading_date"])
    return df


def _write_parquet(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)


# ---------------------------------------------------------------------------
# Golden schema test — drift detection
# ---------------------------------------------------------------------------

def test_expected_columns_matches_builder():
    """Golden test: EXPECTED_COLUMNS must match TASK 105's OUTPUT_COLUMNS.

    If builder contract changes and validator isn't updated, this test
    fails — catching drift before it silently breaks validation.
    """
    from ml.preprocessing.build_base_dataset import OUTPUT_COLUMNS
    assert EXPECTED_COLUMNS == OUTPUT_COLUMNS


def test_expected_columns_count_is_14():
    assert len(EXPECTED_COLUMNS) == 14


# ---------------------------------------------------------------------------
# Checkpoint A — Schema & structure
# ---------------------------------------------------------------------------

def test_schema_valid_dataset_passes():
    base = _valid_base()
    checks = validate_schema_structure(base, prices=None)
    assert not any(c.severity == "FAIL" for c in checks)


def test_schema_missing_column_fails():
    base = _valid_base().drop(columns=["return_1d"])
    checks = validate_schema_structure(base, prices=None)
    assert any(c.severity == "FAIL" and c.name == "column_order" for c in checks)


def test_schema_extra_column_fails():
    base = _valid_base()
    base["debug_col"] = 1
    checks = validate_schema_structure(base, prices=None)
    assert any(c.severity == "FAIL" for c in checks)


def test_schema_wrong_order_fails():
    base = _valid_base()
    # Swap columns
    cols = list(base.columns)
    cols[0], cols[1] = cols[1], cols[0]
    base = base[cols]
    checks = validate_schema_structure(base, prices=None)
    assert any(c.severity == "FAIL" and c.name == "column_order" for c in checks)


def test_schema_duplicate_keys_fail():
    base = pd.concat([_valid_base(), _valid_base().iloc[[0]]], ignore_index=True)
    checks = validate_schema_structure(base, prices=None)
    assert any(c.severity == "FAIL" and c.name == "unique_keys" for c in checks)


def test_schema_null_ticker_fails():
    base = _valid_base()
    base.loc[0, "ticker"] = None
    checks = validate_schema_structure(base, prices=None)
    assert any(c.severity == "FAIL" and c.name == "no_null_ticker" for c in checks)


def test_schema_empty_ticker_fails():
    base = _valid_base()
    base.loc[0, "ticker"] = "   "
    checks = validate_schema_structure(base, prices=None)
    assert any(c.severity == "FAIL" and c.name == "no_empty_ticker" for c in checks)


def test_schema_null_date_fails():
    base = _valid_base()
    base.loc[0, "trading_date"] = pd.NaT
    checks = validate_schema_structure(base, prices=None)
    assert any(c.severity == "FAIL" and c.name == "no_null_trading_date" for c in checks)


def test_schema_tz_aware_date_fails():
    base = _valid_base()
    base["trading_date"] = base["trading_date"].dt.tz_localize("UTC")
    checks = validate_schema_structure(base, prices=None)
    assert any(c.severity == "FAIL" and c.name == "trading_date_tz_naive" for c in checks)


def test_schema_empty_dataset_fails():
    base = pd.DataFrame(columns=EXPECTED_COLUMNS)
    base["trading_date"] = pd.to_datetime(base["trading_date"])
    checks = validate_schema_structure(base, prices=None)
    assert any(c.severity == "FAIL" and c.name == "dataset_non_empty" for c in checks)


def test_schema_missing_columns_do_not_crash():
    base = pd.DataFrame({"ticker": ["BBCA"], "open": [100.0]})
    checks = validate_schema_structure(base, prices=None)
    assert any(c.severity == "FAIL" and c.name == "column_order" for c in checks)


def test_schema_row_count_mismatch_with_prices_fails():
    base = _valid_base()
    prices = _valid_base().iloc[:2]
    checks = validate_schema_structure(base, prices=prices)
    assert any(
        c.severity == "FAIL" and c.name == "row_count_vs_prices" for c in checks
    )


# ---------------------------------------------------------------------------
# Checkpoint B — Price integrity
# ---------------------------------------------------------------------------

def test_price_valid_passes():
    base = _valid_base()
    checks = validate_price_integrity(base)
    assert not any(c.severity == "FAIL" for c in checks)


def test_price_high_lt_open_fails():
    base = _valid_base()
    base.loc[0, "high"] = base.loc[0, "open"] - 1
    checks = validate_price_integrity(base)
    assert any(c.severity == "FAIL" for c in checks)


def test_price_zero_price_fails():
    base = _valid_base()
    base.loc[0, "close"] = 0.0
    checks = validate_price_integrity(base)
    assert any(c.severity == "FAIL" for c in checks)


def test_price_negative_price_fails():
    base = _valid_base()
    base.loc[0, "close"] = -10.0
    checks = validate_price_integrity(base)
    assert any(c.severity == "FAIL" for c in checks)


def test_price_infinite_value_fails():
    base = _valid_base()
    base.loc[0, "close"] = np.inf
    checks = validate_price_integrity(base)
    assert any(c.severity == "FAIL" for c in checks)


def test_price_negative_volume_fails():
    base = _valid_base()
    base.loc[0, "volume"] = -100
    checks = validate_price_integrity(base)
    assert any(c.severity == "FAIL" for c in checks)


def test_price_high_lt_low_fails():
    base = _valid_base()
    base.loc[0, "high"] = 90.0
    base.loc[0, "low"] = 100.0
    checks = validate_price_integrity(base)
    assert any(c.severity == "FAIL" for c in checks)


# ---------------------------------------------------------------------------
# Checkpoint C — Sentiment integrity
# ---------------------------------------------------------------------------

def test_sentiment_valid_passes():
    base = _valid_base()
    checks = validate_sentiment_integrity(base)
    assert not any(c.severity == "FAIL" for c in checks)


def test_sentiment_negative_news_count_fails():
    base = _valid_base()
    base.loc[0, "news_count"] = -1
    checks = validate_sentiment_integrity(base)
    assert any(c.severity == "FAIL" for c in checks)


def test_sentiment_fractional_news_count_fails():
    base = _valid_base()
    # Cast to float first — int64 columns in pandas 2.x reject fractional .loc assignment
    base["news_count"] = base["news_count"].astype("float64")
    base.loc[0, "news_count"] = 1.5
    checks = validate_sentiment_integrity(base)
    assert any(c.severity == "FAIL" for c in checks)


def test_sentiment_no_news_with_non_null_features_fails():
    base = _valid_base()
    base.loc[0, "news_count"] = 0
    base.loc[0, "sentiment_mean"] = 0.5  # should be NULL
    checks = validate_sentiment_integrity(base)
    assert any(
        c.severity == "FAIL" and c.name == "no_news_semantics" for c in checks
    )


def test_sentiment_missing_columns_do_not_crash():
    base = pd.DataFrame({"ticker": ["BBCA"], "trading_date": [pd.Timestamp("2025-01-06")]})
    checks = validate_sentiment_integrity(base)
    assert any(c.severity == "FAIL" and c.name == "news_count_numeric" for c in checks)


def test_sentiment_matched_null_mean_fails():
    base = _valid_base()
    # Row with news_count=2 but sentiment_mean=None
    base.loc[2, "sentiment_mean"] = None
    checks = validate_sentiment_integrity(base)
    assert any(
        c.severity == "FAIL" and c.name == "matched_required_non_null"
        for c in checks
    )


def test_sentiment_ratio_out_of_range_fails():
    base = _valid_base()
    base.loc[2, "positive_ratio"] = 1.5
    checks = validate_sentiment_integrity(base)
    assert any(c.severity == "FAIL" and c.name == "ratio_in_range" for c in checks)


def test_sentiment_negative_std_fails():
    base = _valid_base()
    base.loc[2, "sentiment_std"] = -0.5  # n=2, must be >= 0
    checks = validate_sentiment_integrity(base)
    assert any(
        c.severity == "FAIL" and c.name == "sentiment_std_rules" for c in checks
    )


def test_sentiment_std_nan_with_multi_article_fails():
    base = _valid_base()
    base.loc[2, "sentiment_std"] = None  # n=2, must be non-null
    checks = validate_sentiment_integrity(base)
    assert any(
        c.severity == "FAIL" and c.name == "sentiment_std_rules" for c in checks
    )


def test_sentiment_std_nan_with_single_article_ok():
    """n=1: sentiment_std may legitimately be NaN."""
    base = _valid_base()
    base.loc[2, "news_count"] = 1
    base.loc[2, "sentiment_std"] = None
    base.loc[2, "sentiment_mean"] = 1.0
    checks = validate_sentiment_integrity(base)
    assert not any(
        c.severity == "FAIL" and c.name == "sentiment_std_rules" for c in checks
    )


# ---------------------------------------------------------------------------
# Checkpoint D — Coverage (WARN not FAIL)
# ---------------------------------------------------------------------------

def test_coverage_low_produces_warn_not_fail():
    metrics = {
        "matched_rows": 1,
        "total_output_rows": 1000,
        "matched_pct": 0.1,
    }
    check = build_coverage_check(metrics)
    assert check.severity == "WARN"


def test_coverage_zero_produces_warn_not_fail():
    metrics = {
        "matched_rows": 0,
        "total_output_rows": 100,
        "matched_pct": 0.0,
    }
    check = build_coverage_check(metrics)
    assert check.severity == "WARN"


def test_coverage_full_passes():
    metrics = {
        "matched_rows": 10,
        "total_output_rows": 10,
        "matched_pct": 100.0,
    }
    check = build_coverage_check(metrics)
    assert check.severity == "PASS"


def test_consistency_metrics_computed():
    base = _valid_base()
    metrics = compute_consistency_metrics(base, prices=None, sentiment=None)
    assert metrics["total_output_rows"] == 3
    assert metrics["matched_rows"] == 1
    assert metrics["unmatched_rows"] == 2


# ---------------------------------------------------------------------------
# Checkpoint E — Freshness (deterministic as_of_date)
# ---------------------------------------------------------------------------

def test_freshness_fresh_data_passes():
    base = _valid_base()
    # latest = 2025-01-07; as_of = 2025-01-08 => 1 day old
    checks, metrics = validate_freshness(
        base, as_of_date=date(2025, 1, 8), threshold_days=7,
    )
    assert checks[0].severity == "PASS"
    assert metrics["age_days"] == 1


def test_freshness_stale_data_warns():
    base = _valid_base()
    # latest = 2025-01-07; as_of = 2025-01-30 => 23 days old
    checks, metrics = validate_freshness(
        base, as_of_date=date(2025, 1, 30), threshold_days=7,
    )
    assert checks[0].severity == "WARN"
    assert metrics["age_days"] == 23


def test_freshness_boundary_exactly_threshold_passes():
    base = _valid_base()  # latest 2025-01-07
    checks, _ = validate_freshness(
        base, as_of_date=date(2025, 1, 14), threshold_days=7,  # 7 days
    )
    assert checks[0].severity == "PASS"


# ---------------------------------------------------------------------------
# Integration — run_all_checks
# ---------------------------------------------------------------------------

def test_run_all_checks_valid(tmp_path: Path):
    base_path = tmp_path / "base.parquet"
    _write_parquet(_valid_base(), base_path)

    report = run_all_checks(
        base_path=base_path,
        prices_path=None,
        sentiment_path=None,
        as_of_date=date(2025, 1, 8),
    )
    assert report.overall_status == "PASS"
    assert not report.has_failures


def test_run_all_checks_with_failures(tmp_path: Path):
    base = _valid_base()
    base.loc[0, "close"] = -1.0  # invalid
    base_path = tmp_path / "base.parquet"
    _write_parquet(base, base_path)

    report = run_all_checks(
        base_path=base_path,
        prices_path=None,
        sentiment_path=None,
        as_of_date=date(2025, 1, 8),
    )
    assert report.overall_status == "FAIL"
    assert report.has_failures


def test_run_all_checks_missing_file_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        run_all_checks(
            base_path=tmp_path / "nonexistent.parquet",
            prices_path=None,
            sentiment_path=None,
        )


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

def test_render_markdown_contains_key_sections(tmp_path: Path):
    base_path = tmp_path / "base.parquet"
    _write_parquet(_valid_base(), base_path)

    report = run_all_checks(
        base_path=base_path,
        prices_path=None,
        sentiment_path=None,
        as_of_date=date(2025, 1, 8),
    )
    md = render_markdown(report)

    assert "# FinSentiMind — Dataset Validation Report" in md
    assert "## Overall Status" in md
    assert "## Schema & Structure" in md
    assert "## Price Integrity" in md
    assert "## Sentiment Integrity" in md
    assert "## Verdict" in md
    assert "Structural validity" in md


def test_render_markdown_contains_failures(tmp_path: Path):
    base = _valid_base()
    base.loc[0, "close"] = -1.0
    base_path = tmp_path / "base.parquet"
    _write_parquet(base, base_path)

    report = run_all_checks(
        base_path=base_path,
        prices_path=None,
        sentiment_path=None,
        as_of_date=date(2025, 1, 8),
    )
    md = render_markdown(report)
    assert "## Blocking Failures" in md
    assert "❌" in md


# ---------------------------------------------------------------------------
# CLI exit codes
# ---------------------------------------------------------------------------

def test_cli_exit_zero_on_pass(tmp_path: Path):
    base_path = tmp_path / "base.parquet"
    report_path = tmp_path / "report.md"
    _write_parquet(_valid_base(), base_path)

    code = cli_main([
        "--input", str(base_path),
        "--output", str(report_path),
        "--prices", str(tmp_path / "nonexistent_prices.parquet"),
        "--sentiment", str(tmp_path / "nonexistent_sentiment.parquet"),
        "--as-of-date", "2025-01-08",
    ])
    assert code == 0
    assert report_path.exists()


def test_cli_exit_one_on_fail(tmp_path: Path):
    base = _valid_base()
    base.loc[0, "close"] = -1.0
    base_path = tmp_path / "base.parquet"
    report_path = tmp_path / "report.md"
    _write_parquet(base, base_path)

    code = cli_main([
        "--input", str(base_path),
        "--output", str(report_path),
        "--as-of-date", "2025-01-08",
    ])
    assert code == 1
    # Report should still be written even on FAIL
    assert report_path.exists()


def test_cli_exit_two_on_missing_input(tmp_path: Path):
    code = cli_main([
        "--input", str(tmp_path / "nonexistent.parquet"),
        "--no-save",
    ])
    assert code == 2


def test_save_report_atomic(tmp_path: Path):
    content = "# Test report\nContent"
    target = tmp_path / "subdir" / "report.md"
    save_report_atomic(content, target)
    assert target.exists()
    assert target.read_text() == content