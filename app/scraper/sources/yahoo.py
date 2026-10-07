"""Yahoo Finance OHLCV scraper via yfinance."""
from datetime import date, datetime
from typing import List

import pandas as pd
import yfinance as yf

from app.core.logging import get_logger
from app.models.schemas import StockPrice


logger = get_logger(__name__)


class YahooFinanceScraper:
    """Scraper for historical OHLCV data from Yahoo Finance."""

    def __init__(self, auto_adjust: bool = False, raise_on_error: bool = False):
        """
        Args:
            auto_adjust: Pass-through to yfinance (default False).
            raise_on_error: If True, re-raise exceptions from yfinance instead
                of catching and returning empty list. Useful for backfill
                orchestration where retry logic is applied.
        """
        self.auto_adjust = auto_adjust
        self.raise_on_error = raise_on_error

    def fetch_historical_data(
        self, ticker: str, period: str = "1mo"
    ) -> List[StockPrice]:
        """Fetch historical OHLCV data for a given ticker."""
        if not ticker:
            logger.warning("Empty ticker provided.")
            return []

        try:
            df: pd.DataFrame = yf.Ticker(ticker).history(
                period=period, auto_adjust=self.auto_adjust
            )
        except Exception:
            if self.raise_on_error:
                raise
            logger.error("Failed to fetch data for %s", ticker, exc_info=True)
            return []

        if df is None or df.empty:
            logger.warning("No data returned for ticker %s", ticker)
            return []

        df = df.reset_index()
        df.columns = [str(col).lower().replace(" ", "_") for col in df.columns]

        date_col = None
        for candidate in ("date", "datetime"):
            if candidate in df.columns:
                date_col = candidate
                break
        if date_col is None:
            logger.warning("Date column not found for %s", ticker)
            return []

        kode_saham = ticker.split(".")[0].upper()

        required_cols = {"open", "high", "low", "close", "volume"}
        if not required_cols.issubset(set(df.columns)):
            logger.warning(
                "Missing OHLCV columns for %s. Found: %s",
                ticker,
                list(df.columns),
            )
            return []

        prices: List[StockPrice] = []
        for _, row in df.iterrows():
            try:
                raw_date = row[date_col]
                if isinstance(raw_date, pd.Timestamp):
                    trading_date: date = raw_date.date()
                elif isinstance(raw_date, datetime):
                    trading_date = raw_date.date()
                elif isinstance(raw_date, date):
                    trading_date = raw_date
                else:
                    trading_date = pd.to_datetime(raw_date).date()

                volume_value = row["volume"]
                if pd.isna(volume_value):
                    volume_value = 0

                price = StockPrice(
                    kode_saham=kode_saham,
                    tanggal=trading_date,
                    harga_buka=float(row["open"]),
                    harga_tertinggi=float(row["high"]),
                    harga_terendah=float(row["low"]),
                    harga_tutup=float(row["close"]),
                    volume=int(volume_value),
                )
                prices.append(price)
            except Exception:
                logger.warning(
                    "Skip malformed row for %s", ticker, exc_info=True
                )
                continue

        return prices