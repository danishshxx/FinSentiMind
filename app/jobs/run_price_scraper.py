"""Orchestrator untuk ingestion OHLCV dari Yahoo Finance."""
from typing import List

from app.core.logging import get_logger, setup_logging
from app.database.supabase_client import get_supabase_client
from app.models.schemas import StockPrice
from app.scraper.sources.yahoo import YahooFinanceScraper


logger = get_logger(__name__)


def run_price_pipeline(ticker: str, period: str = "1mo") -> None:
    """Fetch OHLCV and upsert into 'harga_saham'."""
    scraper = YahooFinanceScraper()
    prices: List[StockPrice] = scraper.fetch_historical_data(
        ticker=ticker, period=period
    )

    if not prices:
        logger.warning("No price data fetched for %s.", ticker)
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
        upserted = len(response.data) if response and response.data else 0
        logger.info(
            "Ticker %s — fetched=%d upserted=%d",
            ticker,
            len(records),
            upserted,
        )
    except Exception:
        logger.error("Upsert failed for %s", ticker, exc_info=True)


if __name__ == "__main__":
    setup_logging()
    run_price_pipeline("BBCA.JK", period="1mo")