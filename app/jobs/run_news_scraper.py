"""Orchestrator untuk ingestion berita: RSS + Telegram + Stockbit."""
import asyncio
from typing import Any, Dict, List

from app.core.aliases import WATCH_TICKERS, tag_tickers_in_text
from app.core.logging import get_logger, setup_logging
from app.database.supabase_client import get_supabase_client
from app.models.schemas import NewsArticle
from app.scraper.sources.rss import RSSNewsScraper
from app.scraper.sources.stockbit import StockbitScraper
from app.scraper.sources.telegram import TelegramScraper


logger = get_logger(__name__)


async def run_telegram_pipeline() -> List[NewsArticle]:
    """Optional Telegram ingestion. Returns [] if not configured."""
    scraper = TelegramScraper()
    if not scraper.is_configured():
        logger.info("Telegram not configured. Skipping.")
        return []
    try:
        return await scraper.fetch_articles(ticker=None, limit_per_channel=50)
    except Exception:
        logger.error("Telegram pipeline failed", exc_info=True)
        return []


def run_stockbit_pipeline(watch_tickers: List[str]) -> List[NewsArticle]:
    """Best-effort Stockbit ingestion. Returns [] if not configured."""
    scraper = StockbitScraper()
    if not scraper.is_configured():
        logger.info("Stockbit not configured. Skipping.")
        return []
    articles: List[NewsArticle] = []
    for ticker in watch_tickers:
        try:
            articles.extend(scraper.fetch_articles(ticker=ticker, limit=30))
        except Exception:
            logger.error("Stockbit failed for %s", ticker, exc_info=True)
            continue
    return articles


def run_rss_pipeline() -> List[NewsArticle]:
    """RSS-first ingestion. Ticker tagging dilakukan di orchestrator."""
    try:
        return RSSNewsScraper().fetch_articles()
    except Exception:
        logger.error("RSS pipeline failed", exc_info=True)
        return []


def _dedup_and_insert(tagged: List[tuple[NewsArticle, str]]) -> None:
    """Insert (article, ticker) pairs after dedup by content_hash."""
    if not tagged:
        logger.info("No ticker-matched articles to insert.")
        return

    supabase = get_supabase_client()
    hashes = [a.content_hash for a, _ in tagged if a.content_hash]
    existing: set[str] = set()

    if hashes:
        try:
            resp = (
                supabase.table("berita_saham")
                .select("content_hash")
                .in_("content_hash", hashes)
                .execute()
            )
            if resp and resp.data:
                existing = {r["content_hash"] for r in resp.data}
        except Exception:
            logger.error("Dedup query failed; proceeding without dedup", exc_info=True)

    seen_in_batch: set[str] = set()
    inserted = 0
    skipped_dup = 0

    for article, ticker in tagged:
        ch = article.content_hash
        if not ch or ch in existing or ch in seen_in_batch:
            skipped_dup += 1
            continue
        seen_in_batch.add(ch)

        record: Dict[str, Any] = {
            "title": article.title,
            "url": article.url,
            "source": article.source,
            "published_at": article.published_at.isoformat(),
            "scraped_at": article.scraped_at.isoformat(),
            "content_hash": ch,
            "ticker": ticker,
        }
        try:
            supabase.table("berita_saham").insert(record).execute()
            inserted += 1
        except Exception:
            logger.error(
                "Insert failed for '%s...'", article.title[:60], exc_info=True
            )

    logger.info(
        "Pipeline summary — tagged=%d duplicates=%d inserted=%d",
        len(tagged),
        skipped_dup,
        inserted,
    )


async def run_news_pipeline_async() -> None:
    """Async orchestrator: fetch all sources → tag → dedup → insert."""
    watch_tickers = list(WATCH_TICKERS)

    rss_task = asyncio.to_thread(run_rss_pipeline)
    telegram_task = run_telegram_pipeline()
    stockbit_task = asyncio.to_thread(run_stockbit_pipeline, watch_tickers)

    rss_articles, telegram_articles, stockbit_articles = await asyncio.gather(
        rss_task, telegram_task, stockbit_task, return_exceptions=False
    )

    all_articles: List[NewsArticle] = []
    all_articles.extend(rss_articles or [])
    all_articles.extend(telegram_articles or [])
    all_articles.extend(stockbit_articles or [])

    logger.info("Fetched %d raw articles across all sources.", len(all_articles))

    tagged: List[tuple[NewsArticle, str]] = []
    unmatched = 0
    for article in all_articles:
        tickers = tag_tickers_in_text(article.title, watch_tickers=watch_tickers)
        if not tickers:
            unmatched += 1
            continue
        tagged.append((article, tickers[0]))

    logger.info("Matched: %d | Unmatched: %d", len(tagged), unmatched)
    _dedup_and_insert(tagged)


def run_news_pipeline() -> None:
    """Sync wrapper for schedulers (cron, Task Scheduler)."""
    asyncio.run(run_news_pipeline_async())


if __name__ == "__main__":
    setup_logging()
    run_news_pipeline()