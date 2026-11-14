"""Tests for TASK 118 macro and cross-asset intelligence."""
from pathlib import Path

import pandas as pd
import pytest

from ml.macro.analysis import cross_asset_summary, sector_macro_correlations
from ml.macro.ingestion import ingest_macro_observations
from ml.macro.providers import provider_matrix
from ml.macro.repository import MacroRepository
from ml.macro.schemas import MacroSchemaError, validate_macro_observations
from ml.fundamentals.ingestion import write_parquet_atomic


def _obs(**overrides):
    row = {
        "indicator": "SP500",
        "date": "2026-01-05",
        "value": 5000.0,
        "unit": "index points",
        "currency": "USD",
        "published_at": "2026-01-05T23:00:00+00:00",
        "available_at": "2026-01-05T23:00:00+00:00",
        "source_id": "fixture",
        "source_document": "Fixture",
        "revision": 0,
    }
    row.update(overrides)
    return row


def _repo(tmp_path, rows):
    frame = validate_macro_observations(pd.DataFrame(rows))
    path = tmp_path / "macro_observations.parquet"
    write_parquet_atomic(frame, path)
    return MacroRepository(path)


def _rows():
    rows = []
    days = [
        "2026-01-05", "2026-01-06", "2026-01-07",
        "2026-01-08", "2026-01-09", "2026-01-12",
    ]
    for index, day in enumerate(days):
        stamp = f"{day}T17:00:00+07:00"
        rows.append(_obs(
            date=day, value=5000 + index * 50,
            published_at=stamp, available_at=stamp,
        ))
        rows.append(_obs(
            indicator="USDIDR", date=day, value=16000 + index * 10,
            unit="IDR per USD", currency="IDR",
            published_at=stamp, available_at=stamp,
        ))
    return rows


def test_validate_rejects_unknown_indicator():
    with pytest.raises(MacroSchemaError, match="unknown"):
        validate_macro_observations(pd.DataFrame([_obs(indicator="FAKE")]))


def test_cross_asset_summary_and_returns(tmp_path):
    summary = cross_asset_summary(_repo(tmp_path, _rows()), as_of="2026-01-12")
    assert summary["status"] == "available"
    assert summary["available_count"] == 2
    sp500 = [item for item in summary["indicators"] if item["code"] == "SP500"][0]
    assert sp500["status"] == "available"
    assert sp500["returns"]["5d"] == pytest.approx(5250 / 5000 - 1)
    assert summary["causation"] is False
    assert "not causation" in summary["warning"]


def test_missing_indicators_are_explicitly_unavailable(tmp_path):
    summary = cross_asset_summary(_repo(tmp_path, _rows()), as_of="2026-01-12")
    coal = [item for item in summary["indicators"] if item["code"] == "COAL"][0]
    assert coal["status"] == "unavailable"


def test_point_in_time_hides_future_macro_observation(tmp_path):
    repo = _repo(tmp_path, _rows())
    assert repo.observations(indicator="SP500", as_of="2026-01-04").empty
    assert len(repo.observations(indicator="SP500", as_of="2026-01-05")) == 1


def test_no_repository_returns_unavailable_not_dummy(tmp_path):
    summary = cross_asset_summary(MacroRepository(tmp_path / "missing.parquet"))
    assert summary["status"] == "UNAVAILABLE"
    assert summary["indicators"] == []
    assert summary["provider"]["status"] == "UNAVAILABLE"


def test_sector_macro_correlation_uses_overlap():
    dates = pd.date_range("2026-01-01", periods=20, freq="B").date
    sector = pd.DataFrame([
        {"date": day, "sector": "Banking", "return": 0.01 if i % 2 == 0 else -0.005}
        for i, day in enumerate(dates)
    ])
    macro = pd.DataFrame([
        {"date": day, "USDIDR": 0.002 if i % 2 == 0 else -0.001}
        for i, day in enumerate(dates)
    ])
    result = sector_macro_correlations(sector, macro)
    assert result["status"] == "OK"
    assert result["causation"] is False
    assert result["correlations"][0]["observations"] == 20


def test_manual_import_round_trip(tmp_path):
    source = tmp_path / "macro.csv"
    pd.DataFrame(_rows()).to_csv(source, index=False)
    output = tmp_path / "macro.parquet"
    result = ingest_macro_observations(source, output_path=output, append=False)
    assert output.exists()
    assert len(result) == 12


def test_provider_matrix_does_not_claim_speculative_symbols():
    matrix = provider_matrix()
    text = " ".join(str(item) for item in matrix).lower()
    assert "disabled" in text
    assert "speculative" in text
