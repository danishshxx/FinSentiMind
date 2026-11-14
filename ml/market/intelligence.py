"""Market-level context derived from the local validated dataset.

The equal-weight index in this module is *not* IHSG. It is a transparent proxy
built from the available tickers. Macro adapters are disabled unless explicitly
enabled; unavailable data is returned as unavailable, never fabricated.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

from app.core.logging import get_logger


logger = get_logger(__name__)

MACRO_INDICATORS = [
    ("USDIDR", "USD/IDR"),
    ("SP500", "S&P 500"),
    ("HSI", "Hang Seng"),
    ("GOLD", "Gold"),
    ("COAL", "Coal"),
    ("NICKEL", "Nickel"),
    ("CPO", "CPO"),
]

_MACRO_YFINANCE_SYMBOLS = {
    "USDIDR": "IDR=X",
    "SP500": "^GSPC",
    "HSI": "^HSI",
    "GOLD": "GC=F",
}


def _date_string(value: Any) -> Optional[str]:
    if value is None or pd.isna(value):
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(parsed) else parsed.date().isoformat()


def _pct(value: Any) -> Optional[float]:
    if value is None or pd.isna(value):
        return None
    return float(value)


def _percentile_rank(series: pd.Series, value: float) -> Optional[float]:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if clean.empty or value is None or pd.isna(value):
        return None
    return float((clean <= value).mean())


@dataclass(frozen=True)
class MacroQuote:
    indicator: str
    label: str
    value: Optional[float]
    as_of: Optional[str]
    source: str
    status: str
    reason: Optional[str] = None


class MacroProvider:
    indicator = ""
    label = ""
    source = "unavailable"

    def fetch(self) -> MacroQuote:
        raise NotImplementedError


class UnavailableMacroProvider(MacroProvider):
    def __init__(self, indicator: str, label: str, reason: str) -> None:
        self.indicator = indicator
        self.label = label
        self.reason = reason

    def fetch(self) -> MacroQuote:
        return MacroQuote(
            indicator=self.indicator,
            label=self.label,
            value=None,
            as_of=None,
            source=self.source,
            status="unavailable",
            reason=self.reason,
        )


class YFinanceMacroProvider(MacroProvider):
    """Optional adapter. Disabled unless explicitly enabled by the owner.

    Coal, nickel, and CPO are intentionally not assigned speculative symbols.
    They remain unavailable until an authorized source is configured.
    """

    source = "yfinance"

    def __init__(self, indicator: str, label: str, symbol: Optional[str]) -> None:
        self.indicator = indicator
        self.label = label
        self.symbol = symbol

    def fetch(self) -> MacroQuote:
        if not self.symbol:
            return MacroQuote(
                self.indicator,
                self.label,
                None,
                None,
                self.source,
                "unavailable",
                "No authorized symbol/provider configured for this indicator.",
            )
        try:
            import yfinance as yf  # local import: only when explicitly enabled

            history = yf.Ticker(self.symbol).history(period="10d", interval="1d", auto_adjust=True)
            if history is None or history.empty or "Close" not in history:
                raise ValueError("empty provider response")
            history = history.dropna(subset=["Close"])
            if history.empty:
                raise ValueError("no valid close price")
            last = history.iloc[-1]
            return MacroQuote(
                self.indicator,
                self.label,
                float(last["Close"]),
                _date_string(history.index[-1]),
                self.source,
                "available",
            )
        except Exception as exc:
            logger.warning("Macro provider failed for %s: %s", self.indicator, exc)
            return MacroQuote(
                self.indicator,
                self.label,
                None,
                None,
                self.source,
                "unavailable",
                f"Provider error: {exc}",
            )


def build_macro_context(
    providers: Optional[Sequence[MacroProvider]] = None,
    yfinance_enabled: Optional[bool] = None,
) -> Dict[str, Any]:
    if providers is None:
        if yfinance_enabled is None:
            yfinance_enabled = os.getenv("FINSENTIMIND_ENABLE_MACRO_YFINANCE", "").strip() == "1"
        if yfinance_enabled:
            providers = [
                YFinanceMacroProvider(indicator, label, _MACRO_YFINANCE_SYMBOLS.get(indicator))
                for indicator, label in MACRO_INDICATORS
            ]
        else:
            providers = [
                UnavailableMacroProvider(
                    indicator,
                    label,
                    "Macro adapter disabled. Set FINSENTIMIND_ENABLE_MACRO_YFINANCE=1 "
                    "only after confirming provider licensing.",
                )
                for indicator, label in MACRO_INDICATORS
            ]

    quotes: List[Dict[str, Any]] = []
    for provider in providers:
        try:
            quote = provider.fetch()
        except Exception as exc:  # defensive: adapters must never fabricate
            quote = MacroQuote(
                provider.indicator,
                provider.label,
                None,
                None,
                provider.source,
                "unavailable",
                f"Provider error: {exc}",
            )
        quotes.append(
            {
                "indicator": quote.indicator,
                "label": quote.label,
                "value": quote.value,
                "as_of": quote.as_of,
                "source": quote.source,
                "status": quote.status,
                "reason": quote.reason,
            }
        )
    available = sum(1 for quote in quotes if quote["status"] == "available")
    return {
        "status": "available" if available else "unavailable",
        "available_count": available,
        "total_count": len(quotes),
        "indicators": quotes,
        "note": (
            "Macro data is optional and disabled by default. No dummy values are "
            "produced when an authorized provider is unavailable."
        ),
    }


def _ticker_period_returns(base: pd.DataFrame, latest_date: Any) -> pd.DataFrame:
    rows = []
    for ticker, group in base.groupby("ticker", sort=True):
        history = group.sort_values("trading_date")
        subset = history[history["trading_date"] <= latest_date]
        if subset.empty:
            continue
        close = pd.to_numeric(subset["close"], errors="coerce")
        last_close = close.iloc[-1]
        return_20 = last_close / close.iloc[-21] - 1 if len(close) > 20 else None
        rows.append(
            {
                "ticker": ticker,
                "return_20d": return_20,
                "close": last_close,
                "sessions": len(close),
            }
        )
    return pd.DataFrame(rows)


def compute_market_intelligence(
    base: pd.DataFrame,
    instrument_master=None,
    as_of: Optional[date] = None,
    macro_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if base.empty:
        raise ValueError("Cannot compute market intelligence from an empty dataset.")
    required = {"ticker", "trading_date", "close", "volume", "return_1d"}
    missing = required - set(base.columns)
    if missing:
        raise ValueError(f"Market intelligence missing columns: {sorted(missing)}")

    frame = base.copy()
    frame["trading_date"] = pd.to_datetime(frame["trading_date"]).dt.normalize()
    frame["ticker"] = frame["ticker"].astype("string").str.upper()
    latest_date = pd.Timestamp(as_of).normalize() if as_of else frame["trading_date"].max()
    frame = frame[frame["trading_date"] <= latest_date]
    if frame.empty:
        raise ValueError("No data available up to the requested as_of date.")

    daily = (
        frame.groupby("trading_date")
        .agg(
            equal_weight_return=("return_1d", "mean"),
            total_volume=("volume", "sum"),
            tickers=("ticker", "nunique"),
        )
        .reset_index()
        .sort_values("trading_date")
    )
    daily["equal_weight_return"] = pd.to_numeric(
        daily["equal_weight_return"], errors="coerce"
    )
    daily["equal_weight_index"] = (
        (1 + daily["equal_weight_return"].fillna(0)).cumprod() * 100.0
    )
    daily["market_return_20d"] = daily["equal_weight_index"] / daily["equal_weight_index"].shift(20) - 1
    daily["market_volatility_20_annualized"] = (
        daily["equal_weight_return"].rolling(20, min_periods=20).std(ddof=1) * math.sqrt(252)
    )
    daily["volume_sma_20"] = daily["total_volume"].rolling(20, min_periods=20).mean()

    latest_row = daily.iloc[-1]
    latest_session = frame[frame["trading_date"] == latest_date]
    session_returns = pd.to_numeric(latest_session["return_1d"], errors="coerce").dropna()
    advancers = int((session_returns > 0).sum())
    decliners = int((session_returns < 0).sum())
    unchanged = int((session_returns == 0).sum())
    median_return = float(session_returns.median()) if len(session_returns) else None
    p10 = float(session_returns.quantile(0.10)) if len(session_returns) else None
    p90 = float(session_returns.quantile(0.90)) if len(session_returns) else None

    current_ret = _pct(latest_row["market_return_20d"])
    current_vol = _pct(latest_row["market_volatility_20_annualized"])
    history_ret = daily["market_return_20d"].dropna()
    history_vol = daily["market_volatility_20_annualized"].dropna()
    ret_rank = _percentile_rank(history_ret, current_ret)
    vol_rank = _percentile_rank(history_vol, current_vol)

    if len(history_ret) < 60 or ret_rank is None or vol_rank is None:
        regime_label = "unknown"
        regime_reason = "Insufficient history for a stable regime classification."
    elif vol_rank >= 0.80:
        regime_label = "high_volatility"
        regime_reason = "20-session realized volatility is in the top quintile of available history."
    elif ret_rank >= 2.0 / 3.0:
        regime_label = "bullish"
        regime_reason = "20-session equal-weight return is in the upper tercile of available history."
    elif ret_rank <= 1.0 / 3.0:
        regime_label = "bearish"
        regime_reason = "20-session equal-weight return is in the lower tercile of available history."
    else:
        regime_label = "sideways"
        regime_reason = "20-session return is inside the middle tercile of available history."

    ticker_returns = _ticker_period_returns(frame, latest_date)
    sectors = []
    if instrument_master is not None and getattr(instrument_master, "instruments", None):
        for sector in sorted({item.sector for item in instrument_master.instruments if item.sector}):
            sector_tickers = [
                item.ticker for item in instrument_master.instruments if item.sector == sector
            ]
            values = ticker_returns[ticker_returns["ticker"].isin(sector_tickers)]
            valid = values["return_20d"].dropna()
            if valid.empty:
                continue
            sectors.append(
                {
                    "sector": sector,
                    "tickers": len(sector_tickers),
                    "covered": int(len(valid)),
                    "coverage_pct": round(len(valid) / len(sector_tickers) * 100, 2)
                    if sector_tickers
                    else 0.0,
                    "return_20d_mean": float(valid.mean()) if len(valid) else None,
                    "return_20d_median": float(valid.median()) if len(valid) else None,
                }
            )
        sectors.sort(
            key=lambda item: item["return_20d_mean"]
            if item["return_20d_mean"] is not None
            else float("-inf"),
            reverse=True,
        )

    latest_index = float(latest_row["equal_weight_index"])
    idx = daily.set_index("trading_date")["equal_weight_index"]
    ret_1d = _pct(latest_row["equal_weight_return"])
    ret_5d = (
        latest_index / float(idx.iloc[-6]) - 1 if len(idx) >= 6 else None
    )
    ret_20d = _pct(latest_row["market_return_20d"])
    vol_20d = current_vol
    volume_latest = float(latest_row["total_volume"])
    volume_avg = _pct(latest_row["volume_sma_20"])
    volume_ratio = volume_latest / volume_avg if volume_avg else None
    universe = int(frame["ticker"].nunique())
    latest_coverage = int(latest_row["tickers"])

    return {
        "as_of": latest_date.date().isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "label": "Equal-weight local universe proxy (bukan IHSG)",
        "universe_size": universe,
        "latest_session_tickers": latest_coverage,
        "sessions": int(len(daily)),
        "index": {
            "level": latest_index,
            "return_1d": ret_1d,
            "return_5d": ret_5d,
            "return_20d": ret_20d,
        },
        "breadth": {
            "advancers": advancers,
            "decliners": decliners,
            "unchanged": unchanged,
            "valid_returns": int(len(session_returns)),
        },
        "cross_section": {
            "median_return_1d": median_return,
            "p10_return_1d": p10,
            "p90_return_1d": p90,
        },
        "volatility": {
            "volatility_20d_annualized": vol_20d,
            "method": "sample std of daily equal-weight returns, 20 sessions, annualized sqrt(252)",
        },
        "volume_activity": {
            "latest_total_volume": volume_latest,
            "average_total_volume_20d": volume_avg,
            "relative_to_20d_average": volume_ratio,
            "description": "above average" if volume_ratio and volume_ratio > 1 else "at or below average",
        },
        "regime": {
            "label": regime_label,
            "reason": regime_reason,
            "return_20d": current_ret,
            "volatility_20d_annualized": current_vol,
            "return_percentile": ret_rank,
            "volatility_percentile": vol_rank,
            "sample_size": int(len(history_ret)),
            "methodology": (
                "High volatility if 20-session realized volatility percentile >= 0.80; "
                "otherwise bullish/bearish if 20-session return percentile is in the "
                "upper/lower tercile; otherwise sideways. Percentiles use only history "
                "available at the as-of date."
            ),
        },
        "sector_performance": {
            "period": "20 sessions",
            "coverage_note": "Hanya ticker dengan metadata sector terverifikasi.",
            "sectors": sectors,
        },
        "macro": macro_context if macro_context is not None else build_macro_context(),
        "notes": [
            "Equal-weight proxy is derived from available local tickers, not IHSG.",
            "No future data is used; all computations are truncated at as_of.",
            "Macro indicators are unavailable unless an authorized provider is enabled.",
        ],
    }
