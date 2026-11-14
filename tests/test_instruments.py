"""Tests for the auditable instrument master-data layer (TASK 108)."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from ml.metadata.master import (
    DEFAULT_INSTRUMENT_PATH,
    InstrumentMaster,
    InstrumentMasterError,
)


def _payload() -> dict:
    return json.loads(DEFAULT_INSTRUMENT_PATH.read_text(encoding="utf-8"))


def test_loads_valid_master_and_preserves_missing_values():
    master = InstrumentMaster.load()

    assert len(master.instruments) == 30
    assert master.metadata_version == "idx30-2026-08-03-v1"
    bbca = master.get("bbca")
    assert bbca is not None
    assert bbca.company_name == "Bank Central Asia"
    assert bbca.sector == "Banking"
    assert bbca.metadata_status == "partial"
    assert "subsector" in bbca.missing_fields

    aadi = master.get("AADI")
    assert aadi is not None
    assert aadi.company_name is None
    assert aadi.sector == "Energy"
    assert aadi.completeness_pct < bbca.completeness_pct


def test_source_provenance_references_known_sources():
    master = InstrumentMaster.load()
    source_ids = {source["id"] for source in master.sources}
    assert "unavailable" in source_ids
    for instrument in master.instruments:
        assert set(instrument.provenance.values()) <= source_ids
        if instrument.sector:
            assert instrument.provenance["sector"] != "unavailable"


def test_duplicate_ticker_is_rejected():
    payload = _payload()
    payload["instruments"].append(deepcopy(payload["instruments"][0]))
    with pytest.raises(InstrumentMasterError, match="Duplicate instrument ticker"):
        InstrumentMaster.from_dict(payload).validate()


@pytest.mark.parametrize("ticker", ["BBCA.JK", "bbca", "TOOLONG", ""])
def test_invalid_ticker_is_rejected(ticker: str):
    payload = _payload()
    payload["instruments"][0]["ticker"] = ticker
    with pytest.raises(InstrumentMasterError, match="Invalid IDX ticker"):
        InstrumentMaster.from_dict(payload).validate()


def test_unknown_provenance_source_is_rejected():
    payload = _payload()
    payload["instruments"][0]["provenance"]["sector"] = "made_up_source"
    with pytest.raises(InstrumentMasterError, match="unknown source"):
        InstrumentMaster.from_dict(payload).validate()


def test_populated_field_cannot_be_marked_unavailable():
    payload = _payload()
    payload["instruments"][0]["sector"] = "Energy"
    payload["instruments"][0]["provenance"]["sector"] = "unavailable"
    with pytest.raises(InstrumentMasterError, match="marked unavailable"):
        InstrumentMaster.from_dict(payload).validate()


def test_metadata_update_and_revision_history_are_auditable(tmp_path: Path):
    payload = _payload()
    payload["metadata_version"] = "idx30-2026-08-03-v2"
    payload["updated_at"] = "2026-11-15T00:00:00+00:00"
    payload["revisions"].append(
        {
            "version": "idx30-2026-08-03-v2",
            "updated_at": "2026-11-15T00:00:00+00:00",
            "note": "Added a verified revision for testing.",
        }
    )
    path = tmp_path / "instruments.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    master = InstrumentMaster.load(path)
    assert master.metadata_version == "idx30-2026-08-03-v2"
    assert master.revisions[-1]["version"] == "idx30-2026-08-03-v2"
    assert master.updated_at.startswith("2026-11-15")


def test_historical_ticker_mapping_is_validated():
    payload = _payload()
    payload["instruments"][0]["previous_tickers"] = ["OLDX"]
    master = InstrumentMaster.from_dict(payload)
    master.validate()
    assert master.get("AADI").previous_tickers == ["OLDX"]

    payload["instruments"][1]["previous_tickers"] = ["OLDX"]
    with pytest.raises(InstrumentMasterError, match="Duplicate previous ticker"):
        InstrumentMaster.from_dict(payload).validate()


def test_previous_ticker_cannot_overlap_active_tickers():
    payload = _payload()
    payload["instruments"][0]["previous_tickers"] = ["BBCA"]
    with pytest.raises(InstrumentMasterError, match="cannot also be an active"):
        InstrumentMaster.from_dict(payload).validate()
