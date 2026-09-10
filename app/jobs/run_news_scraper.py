from typing import List

from app.database.supabase_client import get_supabase_client
from app.models.schemas import NewsArticle
from app.scraper.sources.cnbc import CNBCScraper
from app.scraper.sources.kontan import KontanScraper


def run_news_pipeline(ticker: str) -> None:
    """
    Orchestrates the news scraping pipeline for a given ticker.

    Steps:
        1. Instantiate scrapers (CNBC, Kontan).
        2. Fetch and extract articles from each source.
        3. Deduplicate against existing records in Supabase 'berita_saham'.
        4. Insert new articles with ticker information.

    Args:
        ticker (str): Stock ticker symbol (e.g., 'BBCA').

    Returns:
        None
    """
    supabase = get_supabase_client()
    scrapers = [CNBCScraper(), KontanScraper()]

    all_articles: List[NewsArticle] = []
    for scraper in scrapers:
        try:
            html = scraper.fetch_html(ticker)
            articles = scraper.extract_articles(html)
            all_articles.extend(articles)
        except Exception as e:
            print(f"[NewsPipeline] Scraper {scraper.__class__.__name__} failed: {e}")

    if not all_articles:
        print(f"[NewsPipeline] No articles fetched for ticker {ticker}.")
        return

    # Deduplication based on content_hash
    content_hashes = [article.content_hash for article in all_articles if article.content_hash]
    existing_hashes = set()
    if content_hashes:
        # Query Supabase for existing content_hash values
        response = supabase.table("berita_saham") \
            .select("content_hash") \
            .in_("content_hash", content_hashes) \
            .execute()
        if response.data:
            existing_hashes = {row["content_hash"] for row in response.data}

    new_articles = [article for article in all_articles if article.content_hash not in existing_hashes]
    duplicates_count = len(all_articles) - len(new_articles)

    # Insert new articles
    inserted_count = 0
    for article in new_articles:
        record = {
            "title": article.title,
            "url": article.url,
            "source": article.source,
            "published_at": article.published_at.isoformat(),
            "scraped_at": article.scraped_at.isoformat(),
            "content_hash": article.content_hash,
            "ticker": ticker,
        }
        try:
            supabase.table("berita_saham").insert(record).execute()
            inserted_count += 1
        except Exception as e:
            print(f"[NewsPipeline] Failed to insert article {article.title}: {e}")

    print(f"[NewsPipeline] Ticker: {ticker}")
    print(f"  - Total articles fetched: {len(all_articles)}")
    print(f"  - Duplicates ignored: {duplicates_count}")
    print(f"  - New articles inserted: {inserted_count}")


if __name__ == "__main__":
    run_news_pipeline("BBCA")