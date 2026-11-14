"""Tests for TASK 116 portfolio accounting and performance."""
from datetime import date

import pandas as pd
import pytest

from ml.portfolio.accounting import build_value_history, replay
from ml.portfolio.performance import compute_twr, performance_report, xirr
from ml.portfolio.repository import PortfolioRepository, ingest_transactions
from ml.portfolio.schemas import (
    PortfolioSchemaError,
    validate_transactions,
)


def _tx(**overrides):
    row = {
        "transaction_id": "T1",
        "portfolio_id": "P1",
        "portfolio_type": "personal",
        "trade_date": "2026-01-02",
        "side": "DEPOSIT",
        "ticker": None,
        "quantity": None,
        "price": None,
        "fees": 0,
        "taxes": 0,
        "cash_amount": 10000,
        "ratio_from": None,
        "ratio_to": None,
        "currency": "IDR",
        "notes": "",
        "created_at": "2026-01-02T09:00:00+07:00",
    }
    row.update(overrides)
    return row


def _valid(rows):
    return validate_transactions(pd.DataFrame(rows))


def _buy(tx_id, date, qty, price, fees=0, taxes=0):
    return _tx(
        transaction_id=tx_id, trade_date=date, side="BUY", ticker="BBCA",
        quantity=qty, price=price, fees=fees, taxes=taxes, cash_amount=None,
    )


def _sell(tx_id, date, qty, price, fees=0, taxes=0):
    return _tx(
        transaction_id=tx_id, trade_date=date, side="SELL", ticker="BBCA",
        quantity=qty, price=price, fees=fees, taxes=taxes, cash_amount=None,
    )


def test_buy_fees_are_capitalized_and_unrealized_pnl_is_marked_to_market():
    ledger = replay(_valid([
        _tx(),
        _buy("T2", "2026-01-03", 10, 100, fees=10),
    ]), prices={"BBCA": 120})
    data = ledger.to_dict(prices={"BBCA": 120})
    assert data["cash"] == pytest.approx(8990)
    holding = data["holdings"][0]
    assert holding["quantity"] == 10
    assert holding["average_cost"] == pytest.approx(101)
    assert holding["unrealized_pnl"] == pytest.approx(190)
    assert data["total_value"] == pytest.approx(10190)


def test_partial_sale_uses_fifo_and_realizes_pnl():
    ledger = replay(_valid([
        _tx(),
        _buy("T2", "2026-01-03", 10, 100, fees=10),
        _sell("T3", "2026-01-04", 4, 130, fees=5),
    ]), prices={"BBCA": 130})
    data = ledger.to_dict(prices={"BBCA": 130})
    holding = data["holdings"][0]
    assert holding["quantity"] == 6
    assert holding["cost_basis"] == pytest.approx(606)
    assert holding["realized_pnl"] == pytest.approx(111)
    assert data["cash"] == pytest.approx(9505)


def test_short_selling_is_rejected():
    with pytest.raises(PortfolioSchemaError, match="short selling"):
        replay(_valid([
            _tx(),
            _sell("T2", "2026-01-03", 1, 100),
        ]))


def test_dividend_and_tax_are_separate_from_realized_pnl():
    ledger = replay(_valid([
        _tx(),
        _buy("T2", "2026-01-03", 10, 100),
        _tx(
            transaction_id="T3", trade_date="2026-01-04", side="DIVIDEND",
            ticker="BBCA", cash_amount=100, taxes=10, quantity=None, price=None,
        ),
    ]), prices={"BBCA": 100})
    data = ledger.to_dict(prices={"BBCA": 100})
    assert data["dividend_income"] == 100
    assert data["taxes"] == 10
    assert data["cash"] == pytest.approx(9090)


def test_split_preserves_cost_basis_and_adjusts_lots():
    ledger = replay(_valid([
        _tx(),
        _buy("T2", "2026-01-03", 10, 100),
        _tx(
            transaction_id="T3", trade_date="2026-01-04", side="SPLIT",
            ticker="BBCA", ratio_from=1, ratio_to=2, cash_amount=None,
        ),
    ]), prices={"BBCA": 50})
    data = ledger.to_dict(prices={"BBCA": 50})
    holding = data["holdings"][0]
    assert holding["quantity"] == 20
    assert holding["cost_basis"] == pytest.approx(1000)
    assert holding["average_cost"] == pytest.approx(50)
    assert data["market_value"] == pytest.approx(1000)


def test_bonus_shares_increase_quantity_without_new_cost():
    ledger = replay(_valid([
        _tx(),
        _buy("T2", "2026-01-03", 10, 100),
        _tx(
            transaction_id="T3", trade_date="2026-01-04", side="BONUS",
            ticker="BBCA", ratio_from=1, ratio_to=1, cash_amount=None,
        ),
    ]), prices={"BBCA": 50})
    data = ledger.to_dict(prices={"BBCA": 50})
    assert data["holdings"][0]["quantity"] == 20
    assert data["holdings"][0]["cost_basis"] == pytest.approx(1000)


def test_missing_price_is_unavailable_not_zero():
    ledger = replay(_valid([_tx(), _buy("T2", "2026-01-03", 10, 100)]))
    data = ledger.to_dict()
    assert data["total_value"] is None
    assert data["missing_prices"] == ["BBCA"]
    assert data["holdings"][0]["market_value"] is None


def test_twr_removes_external_flow_timing():
    history = pd.DataFrame([
        {"date": date(2026, 1, 2), "total_value": 100.0, "external_flow": 0.0},
        {"date": date(2026, 1, 3), "total_value": 110.0, "external_flow": 0.0},
        {"date": date(2026, 1, 4), "total_value": 220.0, "external_flow": 100.0},
    ])
    result = compute_twr(history)
    assert result["status"] == "OK"
    assert result["twr"] == pytest.approx(1.10 * (220 / 210) - 1)


def test_xirr_known_cash_flows():
    flows = [
        (date(2025, 1, 1), -100.0),
        (date(2026, 1, 1), 110.0),
    ]
    assert xirr(flows) == pytest.approx(0.10, abs=1e-4)


def test_xirr_without_both_signs_is_unavailable():
    assert xirr([(date(2025, 1, 1), -100.0)]) is None


def test_value_history_and_performance_report():
    transactions = _valid([
        _tx(),
        _buy("T2", "2026-01-03", 10, 100),
    ])
    prices = pd.DataFrame([
        {"ticker": "BBCA", "trading_date": "2026-01-03", "close": 100},
        {"ticker": "BBCA", "trading_date": "2026-01-04", "close": 110},
    ])
    history = build_value_history(
        transactions, prices, dates=[date(2026, 1, 3), date(2026, 1, 4)]
    )
    assert list(history["total_value"]) == [10000, 10100]
    report = performance_report(
        history, transactions, float(history.iloc[-1]["total_value"]),
        history.iloc[-1]["date"],
    )
    assert report["twr"]["status"] == "OK"
    assert report["mwr"]["status"] == "OK"


def test_portfolio_repository_report_and_import(tmp_path):
    source = tmp_path / "transactions.csv"
    pd.DataFrame([
        _tx(), _buy("T2", "2026-01-03", 10, 100),
    ]).to_csv(source, index=False)
    output = tmp_path / "transactions.parquet"
    ingest_transactions(source, output_path=output)
    prices = pd.DataFrame([
        {"ticker": "BBCA", "trading_date": "2026-01-03", "close": 100},
    ])
    repo = PortfolioRepository(
        data_dir=tmp_path,
        transactions_path=output,
        portfolios_path=tmp_path / "missing_portfolios.parquet",
    )
    report = repo.report("P1", prices=prices, prices_as_of={"BBCA": 100})
    assert report["status"] == "OK"
    assert report["snapshot"]["total_value"] == pytest.approx(10000)
    assert report["not_for_trading"] is True
