"""Read-only market data access for the FinSentiMind MVP terminal.

The MVP deliberately reads the local research artifacts produced by the ML
pipeline (Parquet files under ``ml/data``). It does not call live market APIs
and it does not invent sentiment, predictions, or technical indicators.

All returned values are derived from the committed pipeline outputs on disk.
The repository is read-only: no method mutates a dataset.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

from app.core.logging import get_logger
from ml.preprocessing.dataset_store import resolve_active_dataset


logger = get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = PROJECT_ROOT / "ml" / "data"

BASE_FILENAME = "base_dataset.parquet"
PRICES_FILENAME = "prices_clean.parquet"
SENTIMENT_FILENAME = "daily_sentiment.parquet"
CALENDAR_FILENAME = "trading_calendar.parquet"

PRICE_COLUMNS = ["open", "high", "low", "close", "volume", "return_1d"]
SENTIMENT_COLUMNS = [
    "news_count",
    "sentiment_mean",
    "sentiment_std",
    "positive_ratio",
    "negative_ratio",
    "availability_live_ratio",
]
STOCK_COLUMNS = ["ticker", "trading_date", *PRICE_COLUMNS, *SENTIMENT_COLUMNS]

SORT_COLUMNS = {
    "ticker",
    "close",
    "return_1d",
    "volume",
    "news_count",
    "sentiment_mean",
    "price_rows",
}


class DataUnavailableError(RuntimeError):
    """Raised when the local dataset required by the terminal is missing."""


class TickerNotFoundError(KeyError):
    """Raised when a requested ticker is not present in the dataset."""


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        result = pd.isna(value)
    except (TypeError, ValueError):
        return False
    return bool(result) if isinstance(result, bool) else False


def _opt_float(value: Any) -> Optional[float]:
    if _is_missing(value):
        return None
    number = float(value)
    return None if math.isnan(number) or math.isinf(number) else number


def _opt_int(value: Any) -> Optional[int]:
    if _is_missing(value):
        return None
    return int(value)


def _date_str(value: Any) -> Optional[str]:
    if _is_missing(value):
        return None
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.date().isoformat() if isinstance(value, datetime) else (
            value.isoformat() if isinstance(value, date) else value.date().isoformat()
        )
    return str(value)


def _jsonable(value: Any) -> Any:
    """Convert pandas/numpy scalars into strict JSON-compatible values."""
    if value is None or value is pd.NaT or value is pd.NA:
        return None
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, str)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
        return value
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "item"):
        return _jsonable(value.item())
    return str(value)


def _normalize_base(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    df = frame.copy()
    if "ticker" in df.columns:
        df["ticker"] = df["ticker"].astype("string").str.strip().str.upper()
    if "trading_date" in df.columns:
        parsed = pd.to_datetime(df["trading_date"], errors="coerce")
        if isinstance(parsed.dtype, pd.DatetimeTZDtype):
            parsed = parsed.dt.tz_convert(None)
        df["trading_date"] = parsed.dt.normalize()
    df = df.dropna(subset=[c for c in ("ticker", "trading_date") if c in df.columns])
    if {"ticker", "trading_date"}.issubset(df.columns):
        df = df.sort_values(["ticker", "trading_date"]).reset_index(drop=True)
    return df


@dataclass(frozen=True)
class DatasetSource:
    name: str
    path: Path
    exists: bool
    size_bytes: Optional[int]
    modified_utc: Optional[str]

    @classmethod
    def inspect(cls, name: str, path: Path) -> "DatasetSource":
        if not path.exists():
            return cls(name=name, path=path, exists=False, size_bytes=None, modified_utc=None)
        stat = path.stat()
        modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
        return cls(
            name=name,
            path=path,
            exists=True,
            size_bytes=int(stat.st_size),
            modified_utc=modified,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "file": self.path.name,
            "exists": self.exists,
            "size_bytes": self.size_bytes,
            "modified_utc": self.modified_utc,
        }


class MarketDataRepository:
    """Loads the local pipeline artifacts once and serves derived views."""

    def __init__(self, data_dir: Optional[Path | str] = None) -> None:
        self.data_root = Path(
            data_dir or os.getenv("FINSENTIMIND_DATA_DIR") or DEFAULT_DATA_DIR
        )
        self.active_dataset = resolve_active_dataset(self.data_root)
        self.data_dir = self.active_dataset.active_dir
        self.base_path = self.data_dir / BASE_FILENAME
        self.prices_path = self.data_dir / PRICES_FILENAME
        self.sentiment_path = self.data_dir / SENTIMENT_FILENAME
        self.calendar_path = self.data_dir / CALENDAR_FILENAME

        self._base = pd.DataFrame()
        self._calendar = pd.DataFrame()
        self._load_error: Optional[str] = None
        self._load()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------
    def _load(self) -> None:
        if not self.base_path.exists():
            self._load_error = f"Dataset tidak ditemukan: {self.base_path}"
            logger.warning(self._load_error)
            return
        try:
            self._base = _normalize_base(pd.read_parquet(self.base_path))
        except Exception as exc:  # pragma: no cover - surfaced through the API
            self._load_error = f"Gagal membaca base dataset: {exc}"
            logger.exception(self._load_error)
            return

        if self.calendar_path.exists():
            try:
                calendar = pd.read_parquet(self.calendar_path)
                if "date" in calendar.columns:
                    calendar["date"] = pd.to_datetime(calendar["date"], errors="coerce").dt.normalize()
                    self._calendar = calendar.dropna(subset=["date"]).sort_values("date")
            except Exception:  # pragma: no cover - calendar is optional
                logger.exception("Gagal membaca trading calendar; coverage sesi dilewati")
                self._calendar = pd.DataFrame()

        if self._base.empty:
            self._load_error = "Dataset tersedia tetapi kosong."
            logger.warning(self._load_error)
        else:
            logger.info(
                "Market data loaded: %d rows, %d tickers, latest %s (source=%s, run_id=%s)",
                len(self._base),
                self._base["ticker"].nunique(),
                self.latest_date(),
                self.active_dataset.source,
                self.active_dataset.run_id,
            )

    def reload(self) -> None:
        self.active_dataset = resolve_active_dataset(self.data_root)
        self.data_dir = self.active_dataset.active_dir
        self.base_path = self.data_dir / BASE_FILENAME
        self.prices_path = self.data_dir / PRICES_FILENAME
        self.sentiment_path = self.data_dir / SENTIMENT_FILENAME
        self.calendar_path = self.data_dir / CALENDAR_FILENAME
        self._base = pd.DataFrame()
        self._calendar = pd.DataFrame()
        self._load_error = None
        self._load()

    def _require_data(self) -> pd.DataFrame:
        if self._base.empty:
            raise DataUnavailableError(
                self._load_error
                or "Dataset lokal belum tersedia. Jalankan pipeline TASK 102-106 terlebih dahulu."
            )
        return self._base

    # ------------------------------------------------------------------
    # Basic metadata
    # ------------------------------------------------------------------
    def tickers(self) -> List[str]:
        base = self._require_data()
        return sorted(str(t) for t in base["ticker"].dropna().unique())

    def has_ticker(self, ticker: str) -> bool:
        return ticker.upper() in set(self.tickers())

    def latest_date(self) -> Optional[str]:
        if self._base.empty:
            return None
        return _date_str(self._base["trading_date"].max())

    def earliest_date(self) -> Optional[str]:
        if self._base.empty:
            return None
        return _date_str(self._base["trading_date"].min())

    def meta(self) -> Dict[str, Any]:
        base = self._require_data()
        latest = base["trading_date"].max()
        latest_rows = base[base["trading_date"] == latest]
        sources = [
            DatasetSource.inspect("base_dataset", self.base_path),
            DatasetSource.inspect("prices_clean", self.prices_path),
            DatasetSource.inspect("daily_sentiment", self.sentiment_path),
            DatasetSource.inspect("trading_calendar", self.calendar_path),
        ]
        return {
            "product": "FinSentiMind",
            "mode": "local-research-snapshot",
            "read_only": True,
            "universe_size": int(base["ticker"].nunique()),
            "latest_trading_date": self.latest_date(),
            "earliest_trading_date": self.earliest_date(),
            "latest_session_tickers": int(len(latest_rows)),
            "trading_sessions": int(base["trading_date"].nunique()),
            "total_rows": int(len(base)),
            "sentiment_rows_with_news": int((base.get("news_count", pd.Series(dtype="int64")) > 0).sum()),
            "sources": [s.to_dict() for s in sources],
            "note": (
                "Data berasal dari artefak riset lokal (Parquet), bukan feed pasar live. "
                "Tidak ada prediksi atau rekomendasi trading di terminal ini."
            ),
        }

    # ------------------------------------------------------------------
    # Derived frames
    # ------------------------------------------------------------------
    def _latest_frame(self) -> pd.DataFrame:
        base = self._require_data()
        latest = base.sort_values(["ticker", "trading_date"]).groupby(
            "ticker", as_index=False, sort=False
        ).tail(1)
        return latest.sort_values("ticker").reset_index(drop=True)

    def _ticker_meta_frame(self) -> pd.DataFrame:
        base = self._require_data()
        agg = base.groupby("ticker", as_index=False).agg(
            price_rows=("trading_date", "size"),
            price_start=("trading_date", "min"),
            price_end=("trading_date", "max"),
            news_days=("news_count", lambda s: int((s.fillna(0) > 0).sum())),
        )
        return agg

    def _stock_records(self) -> pd.DataFrame:
        latest = self._latest_frame()
        meta = self._ticker_meta_frame()
        return latest.merge(meta, on="ticker", how="left")

    def _sentiment_within(
        self, ticker: str, start: Optional[date] = None, end: Optional[date] = None
    ) -> List[Dict[str, Any]]:
        base = self._require_data()
        subset = base[base["ticker"] == ticker]
        if "news_count" in subset.columns:
            subset = subset[subset["news_count"].fillna(0) > 0]
        if start:
            subset = subset[subset["trading_date"] >= pd.Timestamp(start)]
        if end:
            subset = subset[subset["trading_date"] <= pd.Timestamp(end)]
        rows: List[Dict[str, Any]] = []
        for row in subset.sort_values("trading_date").itertuples(index=False):
            rows.append(_row_to_stock_dict(row, include_sentiment=True))
        return [_jsonable(r) for r in rows]

    def _session_coverage(self, ticker_start: Any, ticker_end: Any, observed: int) -> Dict[str, Any]:
        if self._calendar.empty:
            return {
                "calendar_available": False,
                "calendar_sessions": None,
                "observed_sessions": observed,
                "session_coverage_pct": None,
            }
        calendar = self._calendar
        window = calendar[
            (calendar["date"] >= ticker_start) & (calendar["date"] <= ticker_end)
        ]
        if "is_trading_day" in window.columns:
            window = window[window["is_trading_day"].astype(bool)]
        expected = int(len(window))
        coverage = (observed / expected * 100.0) if expected else None
        return {
            "calendar_available": True,
            "calendar_sessions": expected,
            "observed_sessions": observed,
            "session_coverage_pct": round(coverage, 4) if coverage is not None else None,
        }

    # ------------------------------------------------------------------
    # Market overview
    # ------------------------------------------------------------------
    def overview(self) -> Dict[str, Any]:
        base = self._require_data()
        latest = self._latest_frame()
        returns = latest["return_1d"].dropna() if "return_1d" in latest.columns else pd.Series(dtype="float64")
        advancers = int((returns > 0).sum())
        decliners = int((returns < 0).sum())
        unchanged = int((returns == 0).sum())
        avg_return = float(returns.mean()) if len(returns) else None

        matched = base[base["news_count"].fillna(0) > 0] if "news_count" in base.columns else base.iloc[0:0]
        latest_news_date = _date_str(matched["trading_date"].max()) if len(matched) else None
        avg_sentiment = (
            _opt_float(matched["sentiment_mean"].mean()) if "sentiment_mean" in matched.columns else None
        )

        def pick(frame: pd.DataFrame, ascending: bool, n: int = 5) -> List[Dict[str, Any]]:
            if "return_1d" not in frame.columns:
                return []
            usable = frame.dropna(subset=["return_1d"])
            usable = usable[usable["return_1d"] < 0] if ascending else usable[usable["return_1d"] > 0]
            usable = usable.sort_values("return_1d", ascending=ascending)
            return [_jsonable(_latest_row(row)) for row in usable.head(n).itertuples(index=False)]

        active = latest.sort_values("volume", ascending=False).head(5) if "volume" in latest.columns else latest.head(0)
        return {
            "as_of": self.latest_date(),
            "universe_size": int(base["ticker"].nunique()),
            "latest_session_tickers": int(len(latest)),
            "trading_sessions": int(base["trading_date"].nunique()),
            "date_min": self.earliest_date(),
            "date_max": self.latest_date(),
            "advancers": advancers,
            "decliners": decliners,
            "unchanged": unchanged,
            "avg_return_1d": avg_return,
            "total_volume": _opt_int(latest["volume"].sum()) if "volume" in latest.columns else None,
            "top_gainers": pick(latest, ascending=False),
            "top_losers": pick(latest, ascending=True),
            "most_active": [_jsonable(_latest_row(row)) for row in active.itertuples(index=False)],
            "sentiment": {
                "rows_with_news": int(len(matched)),
                "coverage_pct": round((len(matched) / len(base) * 100.0), 4) if len(base) else 0.0,
                "tickers_with_news": int(matched["ticker"].nunique()) if len(matched) else 0,
                "latest_news_date": latest_news_date,
                "avg_sentiment_mean": avg_sentiment,
            },
        }

    # ------------------------------------------------------------------
    # Stock explorer
    # ------------------------------------------------------------------
    def list_stocks(
        self,
        search: Optional[str] = None,
        tickers: Optional[Sequence[str]] = None,
        sort: str = "ticker",
        order: str = "asc",
    ) -> Dict[str, Any]:
        records = self._stock_records()
        if search:
            needle = search.strip().upper()
            records = records[records["ticker"].astype(str).str.contains(needle, regex=False)]
        if tickers:
            wanted = {t.strip().upper() for t in tickers if t and t.strip()}
            records = records[records["ticker"].isin(wanted)]

        sort = sort if sort in SORT_COLUMNS else "ticker"
        ascending = order.lower() != "desc"
        if sort in records.columns:
            records = records.sort_values(sort, ascending=ascending, na_position="last")

        items = [_jsonable(_stock_item(row)) for row in records.itertuples(index=False)]
        return {
            "as_of": self.latest_date(),
            "count": len(items),
            "sort": sort,
            "order": "asc" if ascending else "desc",
            "items": items,
        }

    def stock_detail(self, ticker: str) -> Dict[str, Any]:
        symbol = ticker.strip().upper()
        base = self._require_data()
        history = base[base["ticker"] == symbol]
        if history.empty:
            raise TickerNotFoundError(symbol)
        history = history.sort_values("trading_date").reset_index(drop=True)
        latest = history.iloc[-1]

        window = history.tail(252)
        high_window = _opt_float(window["high"].max()) if "high" in window.columns else None
        low_window = _opt_float(window["low"].min()) if "low" in window.columns else None
        avg_volume_20 = (
            _opt_float(history["volume"].tail(20).mean()) if "volume" in history.columns else None
        )

        def pct_change(lookback: int) -> Optional[float]:
            if "close" not in history.columns or len(history) <= lookback:
                return None
            previous = _opt_float(history["close"].iloc[-1 - lookback])
            current = _opt_float(history["close"].iloc[-1])
            if previous in (None, 0) or current is None:
                return None
            return (current / previous) - 1.0

        first_date = history["trading_date"].min()
        last_date = history["trading_date"].max()
        observed = int(history["trading_date"].nunique())
        coverage = self._session_coverage(first_date, last_date, observed)

        recent_cols = [c for c in STOCK_COLUMNS if c in history.columns]
        recent = history[recent_cols].tail(5).iloc[::-1]
        return {
            "as_of": self.latest_date(),
            "ticker": symbol,
            "latest": _jsonable(_stock_item(latest, symbol)),
            "stats": {
                "high_252_sessions": high_window,
                "low_252_sessions": low_window,
                "avg_volume_20_sessions": avg_volume_20,
                "change_5_sessions_pct": pct_change(5),
                "change_20_sessions_pct": pct_change(20),
                "first_trading_date": _date_str(first_date),
                "last_trading_date": _date_str(last_date),
                "price_rows": int(len(history)),
            },
            "data_quality": {
                **coverage,
                "sentiment_days": int((history["news_count"].fillna(0) > 0).sum())
                if "news_count" in history.columns
                else 0,
            },
            "sentiment_history": self._sentiment_within(symbol),
            "recent_sessions": [_jsonable(_stock_item(row, symbol)) for row in recent.itertuples(index=False)],
            "note": (
                "High/low 252 sesi dan perubahan harga dihitung dari data historis lokal. "
                "Tidak ada prediksi atau target harga."
            ),
        }

    # ------------------------------------------------------------------
    # OHLCV series
    # ------------------------------------------------------------------
    def ohlcv(
        self,
        ticker: str,
        start: Optional[date] = None,
        end: Optional[date] = None,
        limit: int = 500,
    ) -> Dict[str, Any]:
        symbol = ticker.strip().upper()
        base = self._require_data()
        history = base[base["ticker"] == symbol]
        if history.empty:
            raise TickerNotFoundError(symbol)
        history = history.sort_values("trading_date")
        if start:
            history = history[history["trading_date"] >= pd.Timestamp(start)]
        if end:
            history = history[history["trading_date"] <= pd.Timestamp(end)]
        if limit and limit > 0:
            history = history.tail(limit)

        cols = [c for c in STOCK_COLUMNS if c in history.columns]
        items = [_jsonable(_stock_item(row, symbol)) for row in history[cols].itertuples(index=False)]
        return {
            "ticker": symbol,
            "count": len(items),
            "limit": limit,
            "start": _date_str(history["trading_date"].min()) if len(history) else None,
            "end": _date_str(history["trading_date"].max()) if len(history) else None,
            "items": items,
        }

    # ------------------------------------------------------------------
    # Data health (reuses the TASK 106 validator)
    # ------------------------------------------------------------------
    def data_health(
        self,
        freshness_threshold_days: int = 7,
        as_of_date: Optional[date] = None,
    ) -> Dict[str, Any]:
        from ml.validation.validate_dataset import run_all_checks

        if not self.base_path.exists():
            raise DataUnavailableError(f"Dataset tidak ditemukan: {self.base_path}")
        report = run_all_checks(
            base_path=self.base_path,
            prices_path=self.prices_path if self.prices_path.exists() else None,
            sentiment_path=self.sentiment_path if self.sentiment_path.exists() else None,
            as_of_date=as_of_date or date.today(),
            freshness_threshold_days=freshness_threshold_days,
        )
        counts = report.counts
        metrics = _jsonable(report.metrics)
        base = self._base
        latest = base["trading_date"].max() if not base.empty else None
        sources = [
            DatasetSource.inspect("base_dataset", self.base_path),
            DatasetSource.inspect("prices_clean", self.prices_path),
            DatasetSource.inspect("daily_sentiment", self.sentiment_path),
            DatasetSource.inspect("trading_calendar", self.calendar_path),
        ]
        calendar_rows = None
        calendar_trading_days = None
        calendar_latest = None
        if not self._calendar.empty:
            calendar_rows = int(len(self._calendar))
            if "is_trading_day" in self._calendar.columns:
                calendar_trading_days = int(self._calendar["is_trading_day"].astype(bool).sum())
            calendar_latest = _date_str(self._calendar["date"].max())

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": report.overall_status,
            "structural_validity": report.overall_status,
            "summary": {
                "pass": counts.get("PASS", 0),
                "warn": counts.get("WARN", 0),
                "info": counts.get("INFO", 0),
                "fail": counts.get("FAIL", 0),
                "total": len(report.checks),
            },
            "as_of_date": report.as_of_date.isoformat(),
            "freshness_threshold_days": freshness_threshold_days,
            "checks": [
                {
                    "name": check.name,
                    "severity": check.severity,
                    "message": check.message,
                    "details": _jsonable(check.details),
                }
                for check in report.checks
            ],
            "metrics": metrics,
            "dataset": {
                "rows": int(len(base)),
                "tickers": int(base["ticker"].nunique()) if "ticker" in base.columns else 0,
                "date_min": _date_str(base["trading_date"].min()) if len(base) else None,
                "date_max": self.latest_date(),
                "latest_trading_date": _date_str(latest),
                "trading_sessions": int(base["trading_date"].nunique()) if len(base) else 0,
                "sentiment_rows_with_news": int((base["news_count"] > 0).sum())
                if "news_count" in base.columns
                else 0,
                "trading_calendar_rows": calendar_rows,
                "trading_calendar_days": calendar_trading_days,
                "trading_calendar_latest": calendar_latest,
            },
            "sources": [s.to_dict() for s in sources],
            "notes": [
                "Status struktural PASS berarti skema dan integritas data valid, bukan jaminan kualitas sinyal.",
                "Freshness dihitung terhadap tanggal hari ini; dataset riset boleh lebih lama dari feed live.",
                "Cakupan sentimen rendah ditampilkan sebagai WARN, bukan diisi dengan nol.",
            ],
        }


def _latest_row(row: Any) -> Dict[str, Any]:
    return {
        key: getattr(row, key)
        for key in ("ticker", "trading_date", "close", "return_1d", "volume", "news_count")
        if hasattr(row, key)
    }


def _row_to_stock_dict(row: Any, include_sentiment: bool = True) -> Dict[str, Any]:
    data = {
        "ticker": getattr(row, "ticker", None),
        "date": _date_str(getattr(row, "trading_date", None)),
    }
    for column in PRICE_COLUMNS:
        if hasattr(row, column):
            data[column] = getattr(row, column)
    if include_sentiment:
        for column in SENTIMENT_COLUMNS:
            if hasattr(row, column):
                data[column] = getattr(row, column)
    return data


def _stock_item(row: Any, ticker: Optional[str] = None) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "ticker": ticker or getattr(row, "ticker", None),
        "trading_date": _date_str(getattr(row, "trading_date", None)),
    }
    for column in (*PRICE_COLUMNS, *SENTIMENT_COLUMNS):
        if hasattr(row, column):
            data[column] = getattr(row, column)
    for column in ("price_rows", "price_start", "price_end", "news_days"):
        if hasattr(row, column):
            value = getattr(row, column)
            data[column] = _date_str(value) if column in {"price_start", "price_end"} else _opt_int(value)
    return data
