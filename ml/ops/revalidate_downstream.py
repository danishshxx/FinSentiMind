"""Revalidate downstream artifacts against the active dataset generation.

Quant features are recomputed and compared with the published artifact. Market
intelligence is recomputed from the active base dataset. Fundamentals, capital
flow, macro, portfolio and ML research are reported from their local artifacts
and repositories without fabricating missing data.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from app.core.logging import get_logger, setup_logging
from ml.capital_flow.repository import CapitalFlowRepository
from ml.features.quant import FEATURE_COLUMNS, FEATURE_VERSION, compute_features
from ml.fundamentals.repository import FundamentalRepository
from ml.macro.repository import MacroRepository
from ml.market.intelligence import compute_market_intelligence
from ml.metadata.master import DEFAULT_INSTRUMENT_PATH, InstrumentMaster, InstrumentMasterError
from ml.portfolio.repository import PortfolioRepository
from ml.preprocessing.dataset_store import (
    resolve_active_dataset,
    write_json_atomic,
)
from ml.research.ml_pipeline import load_latest_research_report


logger = get_logger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_ROOT = _PROJECT_ROOT / "ml" / "data"
DEFAULT_REPORTS = _PROJECT_ROOT / "ml" / "reports"


def _quant_consistency(active_dir: Path, prices: pd.DataFrame) -> Dict[str, Any]:
    stored_path = active_dir / "quant_features.parquet"
    if not stored_path.is_file():
        return {
            "status": "UNAVAILABLE",
            "reason": "quant_features.parquet is not part of the active generation.",
            "feature_version": FEATURE_VERSION,
        }
    stored = pd.read_parquet(stored_path).sort_values(
        ["ticker", "trading_date"]
    ).reset_index(drop=True)
    recomputed = compute_features(prices).sort_values(
        ["ticker", "trading_date"]
    ).reset_index(drop=True)

    if len(stored) != len(recomputed):
        return {
            "status": "MISMATCH",
            "reason": f"Row count differs: stored={len(stored)} recomputed={len(recomputed)}",
        }

    mismatched: Dict[str, float] = {}
    for column in FEATURE_COLUMNS:
        if column not in stored.columns or column not in recomputed.columns:
            mismatched[column] = float("nan")
            continue
        left = stored[column].to_numpy(dtype="float64")
        right = recomputed[column].to_numpy(dtype="float64")
        if not np.allclose(left, right, rtol=1e-9, atol=1e-12, equal_nan=True):
            diff = np.nanmax(np.abs(left - right)) if len(left) else 0.0
            mismatched[column] = float(diff)

    warmup = {
        column: int(stored[column].isna().sum())
        for column in FEATURE_COLUMNS if column in stored.columns
    }
    return {
        "status": "MATCH" if not mismatched else "MISMATCH",
        "feature_version": FEATURE_VERSION,
        "rows": int(len(stored)),
        "mismatched_columns": mismatched,
        "warmup_null_counts": warmup,
    }


def _market_snapshot(base: pd.DataFrame) -> Dict[str, Any]:
    try:
        master = InstrumentMaster.load(DEFAULT_INSTRUMENT_PATH)
    except InstrumentMasterError:
        master = InstrumentMaster.empty()
    intelligence = compute_market_intelligence(base, master)
    macro = intelligence.get("macro") or {}
    indicators = macro.get("indicators", []) if isinstance(macro, dict) else []
    return {
        "status": "OK",
        "as_of": intelligence.get("as_of"),
        "universe_size": intelligence.get("universe_size"),
        "index_label": (intelligence.get("index") or {}).get("label"),
        "breadth": intelligence.get("breadth"),
        "regime": (intelligence.get("regime") or {}).get("label"),
        "macro_status": macro.get("status") if isinstance(macro, dict) else None,
        "macro_indicators": [
            {"indicator": item.get("indicator"), "status": item.get("status")}
            for item in indicators
        ],
        "not_ihsg": True,
    }


def _fundamentals_status() -> Dict[str, Any]:
    repo = FundamentalRepository()
    statements = repo.statements()
    actions = repo.corporate_actions()
    dividends = repo.dividends() if hasattr(repo, "dividends") else pd.DataFrame()
    if statements.empty:
        return {
            "status": "UNAVAILABLE",
            "reason": "No verified fundamental statements are installed (manual import only).",
            "statements_rows": 0,
            "corporate_action_rows": int(len(actions)),
            "dividend_rows": int(len(dividends)),
        }
    return {
        "status": "AVAILABLE",
        "statements_rows": int(len(statements)),
        "tickers": int(statements["ticker"].nunique()) if "ticker" in statements else 0,
        "corporate_action_rows": int(len(actions)),
        "dividend_rows": int(len(dividends)),
        "provenance": "MANUAL_IMPORT_ONLY",
    }


def _portfolio_status() -> Dict[str, Any]:
    repo = PortfolioRepository()
    transactions = repo.transactions()
    portfolios = repo.portfolios()
    if transactions.empty:
        return {
            "status": "UNAVAILABLE",
            "reason": "No local portfolio ledger is installed. Owner ledger is never auto-created.",
            "transactions_rows": 0,
            "portfolios": 0,
            "risk_lab": {"status": "UNAVAILABLE", "reason": "No portfolio ledger."},
        }
    return {
        "status": "AVAILABLE",
        "transactions_rows": int(len(transactions)),
        "portfolios": int(len(portfolios)),
        "risk_lab": {
            "status": "REQUIRES_PORTFOLIO_SELECTION",
            "reason": "Risk is computed per portfolio from the preserved ledger.",
        },
    }


def _ml_status() -> Dict[str, Any]:
    report = load_latest_research_report()
    acceptance = report.get("acceptance") or {}
    return {
        "status": acceptance.get("status") or report.get("status") or "NOT_RUN",
        "validated": bool(acceptance.get("validated", False)),
        "roc_auc": (report.get("aggregate") or {}).get("model", {}).get("roc_auc"),
        "strategy_sharpe": (report.get("aggregate") or {}).get("strategy", {}).get(
            "sharpe_annualized"
        ),
        "feature_version": (report.get("dataset") or {}).get("feature_version"),
        "report_available": bool(report.get("available")),
    }


def run_revalidation(
    data_root: Path | str = DEFAULT_DATA_ROOT,
    reports_dir: Path | str = DEFAULT_REPORTS,
) -> Dict[str, Any]:
    data_root = Path(data_root)
    reports_dir = Path(reports_dir)
    active = resolve_active_dataset(data_root, verify_hashes=True)
    active_dir = active.active_dir
    prices = pd.read_parquet(active_dir / "prices_clean.parquet")
    base = pd.read_parquet(active_dir / "base_dataset.parquet")
    calendar_path = active_dir / "trading_calendar.parquet"
    calendar_version = None
    if calendar_path.is_file():
        calendar = pd.read_parquet(calendar_path)
        versions = calendar["calendar_version"].dropna().unique()
        calendar_version = str(versions[0]) if len(versions) == 1 else None

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "active_generation": active.run_id,
        "active_source": active.source,
        "calendar_version": calendar_version,
        "data_as_of": str(pd.to_datetime(prices["trading_date"]).max().date()),
        "universe_scope": "IDX30",
        "quant_features": _quant_consistency(active_dir, prices),
        "market_intelligence": _market_snapshot(base),
        "fundamentals": _fundamentals_status(),
        "capital_flow": CapitalFlowRepository().status(),
        "macro": MacroRepository().status(),
        "portfolio": _portfolio_status(),
        "ml_research": _ml_status(),
    }

    mandatory_ok = report["quant_features"]["status"] == "MATCH"
    report["status"] = "PASS" if mandatory_ok else "PARTIAL"
    report["notes"] = [
        "Fundamental, capital-flow and macro data are manual-import only in this build.",
        "Missing data remains UNAVAILABLE; it is never synthesized.",
        "The equal-weight market index is a transparent proxy, not IHSG.",
    ]

    reports_dir.mkdir(parents=True, exist_ok=True)
    run_id = active.run_id or "legacy"
    json_path = reports_dir / f"downstream_revalidation_{run_id}.json"
    write_json_atomic(json_path, report)
    report["report_path"] = str(json_path)
    return report


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Revalidate downstream artifacts.")
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--reports-dir", type=Path, default=DEFAULT_REPORTS)
    args = parser.parse_args(argv)

    setup_logging()
    report = run_revalidation(args.data_root, args.reports_dir)
    print(json.dumps({
        "status": report["status"],
        "active_generation": report["active_generation"],
        "calendar_version": report["calendar_version"],
        "quant": report["quant_features"]["status"],
        "market": report["market_intelligence"]["status"],
        "fundamentals": report["fundamentals"]["status"],
        "capital_flow": report["capital_flow"]["status"],
        "macro": report["macro"]["status"],
        "portfolio": report["portfolio"]["status"],
        "ml": report["ml_research"]["status"],
        "report_path": report["report_path"],
    }, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
