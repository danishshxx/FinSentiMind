from datetime import date, datetime
from typing import List

import pandas as pd
import yfinance as yf

from app.models.schemas import StockPrice


class YahooFinanceScraper:
    """
    Scraper for historical OHLCV data from Yahoo Finance via the yfinance library.

    Unlike news scrapers, this class does not inherit from BaseNewsScraper because
    it deals with tabular time-series data rather than HTML parsing.
    """

    def __init__(self, auto_adjust: bool = False):
        """
        Initialize the scraper.

        Args:
            auto_adjust (bool): Whether to auto-adjust prices for splits/dividends.
                Defaults to False to preserve raw OHLCV values.
        """
        self.auto_adjust = auto_adjust

    def fetch_historical_data(self, ticker: str, period: str = "1mo") -> List[StockPrice]:
        """
        Fetch historical OHLCV data for a given ticker.

        Args:
            ticker (str): Yahoo Finance ticker symbol (e.g., 'BBCA.JK' for IDX).
            period (str): Valid yfinance period string ('1d', '5d', '1mo', '3mo',
                '6mo', '1y', '2y', '5y', '10y', 'ytd', 'max'). Defaults to '1mo'.

        Returns:
            List[StockPrice]: A list of StockPrice models. Returns an empty list
            if the ticker is invalid or no data is available.
        """
        if not ticker:
            print("[YahooFinanceScraper] Empty ticker provided.")
            return []

        try:
            df: pd.DataFrame = yf.Ticker(ticker).history(
                period=period, auto_adjust=self.auto_adjust
            )
        except Exception as e:
            print(f"[YahooFinanceScraper] Failed to fetch data for {ticker}: {e}")
            return []

        if df is None or df.empty:
            print(f"[YahooFinanceScraper] No data returned for ticker {ticker}.")
            return []

        # Normalize dataframe: reset index, lowercase columns, drop NA rows
        df = df.reset_index()
        df.columns = [str(col).lower().replace(" ", "_") for col in df.columns]

        # Identify the date column (yfinance names it 'date' or 'datetime')
        date_col = None
        for candidate in ("date", "datetime"):
            if candidate in df.columns:
                date_col = candidate
                break
        if date_col is None:
            print(f"[YahooFinanceScraper] Date column not found for {ticker}.")
            return []

        # Normalize ticker code (strip exchange suffix like '.JK')
        kode_saham = ticker.split(".")[0].upper()

        required_cols = {"open", "high", "low", "close", "volume"}
        if not required_cols.issubset(set(df.columns)):
            print(
                f"[YahooFinanceScraper] Missing OHLCV columns for {ticker}. "
                f"Found: {list(df.columns)}"
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
            except Exception as e:
                print(f"[YahooFinanceScraper] Skipping malformed row for {ticker}: {e}")
                continue

        return prices