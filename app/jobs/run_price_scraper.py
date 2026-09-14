from typing import List

from app.database.supabase_client import get_supabase_client
from app.models.schemas import StockPrice
from app.scraper.sources.yahoo import YahooFinanceScraper


def run_price_pipeline(ticker: str, period: str = "1mo") -> None:
    """
    Orchestrates the OHLCV price ingestion pipeline for a given ticker.

    Steps:
        1. Fetch historical data via YahooFinanceScraper.
        2. Convert StockPrice models into dictionaries.
        3. Upsert records into Supabase 'harga_saham' table, relying on the
           UNIQUE(kode_saham, tanggal) constraint to prevent duplicates.

    Args:
        ticker (str): Yahoo Finance ticker symbol (e.g., 'BBCA.JK').
        period (str): Historical window to fetch. Defaults to '1mo'.

    Returns:
        None
    """
    scraper = YahooFinanceScraper()
    prices: List[StockPrice] = scraper.fetch_historical_data(ticker=ticker, period=period)

    if not prices:
        print(f"[PricePipeline] No price data fetched for {ticker}.")
        return

    records = [
        {
            "kode_saham": price.kode_saham,
            "tanggal": price.tanggal.isoformat(),
            "harga_buka": price.harga_buka,
            "harga_tertinggi": price.harga_tertinggi,
            "harga_terendah": price.harga_terendah,
            "harga_tutup": price.harga_tutup,
            "volume": price.volume,
        }
        for price in prices
    ]

    supabase = get_supabase_client()
    try:
        response = (
            supabase.table("harga_saham")
            .upsert(records, on_conflict="kode_saham,tanggal")
            .execute() 
        )
        inserted = len(response.data) if response and response.data else 0
        print(f"[PricePipeline] Ticker: {ticker}")
        print(f"  - Records fetched: {len(records)}")
        print(f"  - Records upserted: {inserted}")
    except Exception as e:
        print(f"[PricePipeline] Failed to upsert data for {ticker}: {e}")


if __name__ == "__main__":
    run_price_pipeline("BBCA.JK", period="1mo")