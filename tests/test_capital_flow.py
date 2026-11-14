"""Tests for capital-flow intelligence and unavailable states (TASK 115)."""
from pathlib import Path

import pandas as pd
import pytest

from ml.capital_flow.analysis import build_market_report, summarize_flows
from ml.capital_flow.ingestion import ingest_flow_observations, write_parquet_atomic
from ml.capital_flow.repository import CapitalFlowRepository
from ml.capital_flow.schemas import (
    CapitalFlowSchemaError,
    validate_flow_observations,
)


def _flow(**overrides):
    row = {
        "date": "2026-01-05",
        "scope": "stock",
        "ticker": "BBCA",
        "investor_class": "foreign",
        "broker_code": "AK",
        "buy_value": 100.0,
        "sell_value": 40.0,
        "currency": "IDR",
        "published_at": "2026-01-05T18:00:00+07:00",
        "available_at": "2026-01-05T18:00:00+07:00",
        "source_id": "fixture",
        "source_document": "Fixture",
        "revision": 0,
    }
    row.update(overrides)
    return row


def _valid(rows):
    return validate_flow_observations(pd.DataFrame(rows))


def _repo(tmp_path, rows):
    frame = _valid(rows)
    path = tmp_path / "flow_observations.parquet"
    write_parquet_atomic(frame, path)
    return CapitalFlowRepository(path)


def test_net_and_gross_are_derived_from_buy_sell():
    out = _valid([_flow()])
    row = out.iloc[0]
    assert row["net_value"] == 60
    assert row["gross_value"] == 140


def test_duplicate_flow_key_is_rejected():
    with pytest.raises(CapitalFlowSchemaError, match="duplicate"):
        _valid([_flow(), _flow()])


def test_stock_scope_requires_ticker():
    with pytest.raises(CapitalFlowSchemaError, match="ticker"):
        _valid([_flow(ticker=None)])


def test_broker_class_requires_broker_code():
    with pytest.raises(CapitalFlowSchemaError, match="broker_code"):
        _valid([_flow(investor_class="broker", broker_code=None)])


def test_summary_net_to_gross_and_interpretation_boundary():
    rows = [_flow()]
    summary = summarize_flows(_valid(rows))
    assert summary["status"] == "OK"
    assert summary["net_value"] == 60
    assert summary["gross_value"] == 140
    assert summary["net_to_gross"] == pytest.approx(60 / 140)
    assert "not proof" in summary["interpretation_boundary"]


def test_broker_concentration_is_gross_share():
    rows = [
        _flow(broker_code="AK", buy_value=60, sell_value=0),
        _flow(broker_code="BB", buy_value=40, sell_value=0),
    ]
    summary = summarize_flows(_valid(rows))
    concentration = summary["broker_concentration"]
    assert concentration["average_top1_gross_share"] == pytest.approx(0.6)
    assert concentration["average_top3_gross_share"] == pytest.approx(1.0)


def test_flow_persistence_tracks_consecutive_positive_sessions():
    rows = [
        _flow(date="2026-01-05", buy_value=100, sell_value=10),
        _flow(date="2026-01-06", buy_value=100, sell_value=20),
        _flow(date="2026-01-07", buy_value=10, sell_value=100),
        _flow(date="2026-01-08", buy_value=100, sell_value=30),
    ]
    summary = summarize_flows(_valid(rows))
    persistence = summary["persistence"]
    assert persistence["longest_positive_streak"] == 2
    assert persistence["current_positive_streak"] == 1
    assert persistence["longest_negative_streak"] == 1


def test_price_flow_divergence_is_observational():
    rows = [
        _flow(date="2026-01-05", buy_value=100, sell_value=10),
        _flow(date="2026-01-06", buy_value=100, sell_value=10),
    ]
    prices = pd.DataFrame([
        {"date": "2026-01-05", "close": 100},
        {"date": "2026-01-06", "close": 90},
    ])
    summary = summarize_flows(_valid(rows), prices=prices)
    divergence = summary["price_flow_divergence"]
    assert divergence["classification"] == "accumulation_divergence"
    assert "not proof of intent" in divergence["note"]


def test_point_in_time_repository_hides_future_observations(tmp_path):
    repo = _repo(tmp_path, [_flow()])
    assert repo.observations(scope="stock", ticker="BBCA", as_of="2026-01-04").empty
    assert len(repo.observations(scope="stock", ticker="BBCA", as_of="2026-01-05")) == 1


def test_unavailable_repository_is_explicit(tmp_path):
    repo = CapitalFlowRepository(tmp_path / "missing.parquet")
    report = build_market_report(repo)
    assert report["provider"]["status"] == "UNAVAILABLE"
    assert report["summary"]["status"] == "UNAVAILABLE"
    assert "No verified" in report["summary"]["reason"]


def test_manual_import_round_trip(tmp_path):
    source = tmp_path / "flow.csv"
    pd.DataFrame([_flow()]).to_csv(source, index=False)
    output = tmp_path / "flow.parquet"
    result = ingest_flow_observations(source, output_path=output, append=False)
    assert output.exists()
    assert len(result) == 1
    assert result.iloc[0]["net_value"] == 60
