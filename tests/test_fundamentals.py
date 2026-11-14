"""Tests for the auditable fundamental-data foundation (TASK 113)."""
from pathlib import Path

import pandas as pd
import pytest

from ml.fundamentals.ingestion import (
    ingest_financial_statements,
    write_parquet_atomic,
)
from ml.fundamentals.normalization import (
    STATUS_IDR,
    STATUS_NON_IDR,
    normalize_financial_statements,
)
from ml.fundamentals.quality import run_quality_checks
from ml.fundamentals.repository import FundamentalRepository
from ml.fundamentals.schemas import (
    FundamentalSchemaError,
    validate_financial_statements,
)


def _row(**overrides):
    row = {
        "ticker": "TEST",
        "statement_type": "income_statement",
        "period_type": "annual",
        "fiscal_year": 2024,
        "fiscal_quarter": None,
        "period_start": "2024-01-01",
        "period_end": "2024-12-31",
        "consolidation": "consolidated",
        "currency": "IDR",
        "unit_scale": "billions",
        "line_item": "revenue",
        "value": 1.5,
        "published_at": "2025-03-01T09:00:00+07:00",
        "available_at": "2025-03-01T09:00:00+07:00",
        "source_id": "annual-report-2024",
        "source_document": "Annual Report 2024",
        "source_page": 12,
        "restatement_of": None,
        "revision": 0,
    }
    row.update(overrides)
    return row


def _validated(rows):
    return validate_financial_statements(pd.DataFrame(rows))


def _repository(tmp_path: Path, rows):
    frame = normalize_financial_statements(_validated(rows))
    path = tmp_path / "financial_statements.parquet"
    write_parquet_atomic(frame, path)
    return FundamentalRepository(data_dir=tmp_path, statements_path=path)


def test_unit_scale_is_normalized_and_original_preserved():
    out = normalize_financial_statements(_validated([_row(value=1.5)]))
    row = out.iloc[0]
    assert row["value"] == 1_500_000_000.0
    assert row["original_value"] == 1.5
    assert row["original_unit_scale"] == "billions"
    assert row["normalization_status"] == STATUS_IDR


def test_non_idr_is_not_silently_currency_converted():
    out = normalize_financial_statements(_validated([_row(currency="USD")]))
    row = out.iloc[0]
    assert row["value"] == 1_500_000_000.0
    assert row["currency"] == "USD"
    assert row["normalization_status"] == STATUS_NON_IDR


def test_duplicate_key_and_revision_is_rejected():
    with pytest.raises(FundamentalSchemaError, match="duplicate"):
        _validated([_row(), _row()])


def test_restatement_history_is_preserved_and_latest_revision_wins(tmp_path):
    rows = [
        _row(value=1.0, revision=0),
        _row(
            value=1.2,
            revision=1,
            available_at="2025-04-01T09:00:00+07:00",
            published_at="2025-04-01T09:00:00+07:00",
            restatement_of="annual-report-2024",
        ),
    ]
    repo = _repository(tmp_path, rows)
    latest = repo.statements(ticker="TEST")
    assert len(latest) == 1
    assert latest.iloc[0]["revision"] == 1
    assert latest.iloc[0]["value"] == 1_200_000_000.0

    history = repo.statements(ticker="TEST", include_history=True)
    assert len(history) == 2


def test_point_in_time_repository_excludes_future_availability(tmp_path):
    rows = [
        _row(
            published_at="2025-03-01T09:00:00+07:00",
            available_at="2025-03-01T09:00:00+07:00",
        )
    ]
    repo = _repository(tmp_path, rows)
    assert repo.statements(as_of="2025-02-28").empty
    assert len(repo.statements(as_of="2025-03-01")) == 1


def test_missing_value_remains_null_not_zero():
    out = normalize_financial_statements(_validated([_row(value=None)]))
    assert pd.isna(out.iloc[0]["value"])
    report = run_quality_checks(out)
    assert any(
        finding.check == "missing_values_preserved" for finding in report.infos
    )


def test_invalid_quarter_is_rejected():
    with pytest.raises(FundamentalSchemaError, match="fiscal_quarter"):
        _validated([_row(period_type="quarterly", fiscal_quarter=5)])


def test_mixed_units_within_period_are_flagged():
    rows = [
        _row(line_item="revenue", unit_scale="billions"),
        _row(line_item="net_income", unit_scale="millions"),
    ]
    report = run_quality_checks(normalize_financial_statements(_validated(rows)))
    assert any(finding.check == "mixed_units" for finding in report.errors)


def test_repository_periods_are_observed_periods_only(tmp_path):
    rows = [
        _row(period_start="2023-01-01", period_end="2023-12-31", fiscal_year=2023),
        _row(period_start="2024-01-01", period_end="2024-12-31", fiscal_year=2024),
    ]
    repo = _repository(tmp_path, rows)
    periods = repo.periods("TEST")
    assert len(periods) == 2
    assert list(periods["fiscal_year"]) == [2023, 2024]


def test_manual_csv_import_writes_normalized_dataset(tmp_path):
    source = tmp_path / "statements.csv"
    pd.DataFrame([_row()]).to_csv(source, index=False)
    output = tmp_path / "out.parquet"
    result = ingest_financial_statements(source, output_path=output, append=False)
    assert output.exists()
    assert len(result) == 1
    assert result.iloc[0]["value"] == 1_500_000_000.0
