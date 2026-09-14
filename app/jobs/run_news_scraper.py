import asyncio
from typing import Any, Dict, List

from app.core.aliases import TICKER_ALIASES, tag_tickers_in_text
from app.database.supabase_client import get_supabase_client
from app.models.schemas import NewsArticle
from app.scraper.sources.rss import RSSNewsScraper
from app.scraper.sources.stockbit import StockbitScraper
from app.scraper.sources.telegram import TelegramScraper


async def run_telegram_pipeline() -> List[NewsArticle]:
    """Optional Telegram ingestion. Returns [] if not configured."""
    scraper = TelegramScraper()
    if not scraper.is_configured():
        print("[NewsPipeline] Telegram not configured. Skipping.")
        return []
    try:
        return await scraper.fetch_articles(ticker=None, limit_per_channel=50)
    except Exception as e:
        print(f"[NewsPipeline] Telegram failed: {e}")
        return []


def run_stockbit_pipeline(watch_tickers: List[str]) -> List[NewsArticle]:
    """Best-effort Stockbit ingestion. Returns [] if not configured."""
    scraper = StockbitScraper()
    if not scraper.is_configured():
        print("[NewsPipeline] Stockbit not configured. Skipping.")
        return []
    articles: List[NewsArticle] = []
    for ticker in watch_tickers:
        try:
            articles.extend(scraper.fetch_articles(ticker=ticker, limit=30))
        except Exception as e:
            print(f"[NewsPipeline] Stockbit failed for {ticker}: {e}")
            continue
    return articles


def run_rss_pipeline() -> List[NewsArticle]:
    """RSS-first ingestion. Returns all articles; ticker tagging happens later."""
    try:
        return RSSNewsScraper().fetch_articles()
    except Exception as e:
        print(f"[NewsPipeline] RSS failed: {e}")
        return []


def _dedup_and_insert(tagged: List[tuple[NewsArticle, str]]) -> None:
    """
    Insert (article, ticker) pairs into 'berita_saham' after dedup by content_hash.

    Args:
        tagged: List of (NewsArticle, ticker) tuples.
    """
    if not tagged:
        print("[NewsPipeline] No ticker-matched articles to insert.")
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
        except Exception as e:
            print(f"[NewsPipeline] Dedup query failed: {e}. Proceeding without dedup.")

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
        except Exception as e:
            print(f"[NewsPipeline] Insert failed for '{article.title[:60]}...': {e}")

    print(f"[NewsPipeline] Tagged pairs : {len(tagged)}")
    print(f"[NewsPipeline] Duplicates   : {skipped_dup}")
    print(f"[NewsPipeline] Inserted     : {inserted}")


async def run_news_pipeline_async() -> None:
    """
    Top-level async orchestrator.

    Steps:
        1. Fetch from RSS (primary), Telegram (optional), Stockbit (optional).
        2. Tag each article with matching tickers via alias matcher.
        3. Skip articles with no ticker match.
        4. Dedup by content_hash and insert into Supabase.
    """
    watch_tickers = list(TICKER_ALIASES.keys())

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

    print(f"[NewsPipeline] Fetched {len(all_articles)} raw articles across all sources.")

    tagged: List[tuple[NewsArticle, str]] = []
    unmatched = 0
    for article in all_articles:
        tickers = tag_tickers_in_text(article.title, watch_tickers=watch_tickers)
        if not tickers:
            unmatched += 1
            continue
        tagged.append((article, tickers[0]))

    print(f"[NewsPipeline] Matched: {len(tagged)} | Unmatched: {unmatched}")
    _dedup_and_insert(tagged)


def run_news_pipeline() -> None:
    """Sync wrapper for schedulers (cron, Task Scheduler)."""
    asyncio.run(run_news_pipeline_async())


if __name__ == "__main__":
    run_news_pipeline()