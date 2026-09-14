import hashlib
from datetime import datetime, timezone
from typing import Iterable, List

import feedparser

from app.models.schemas import NewsArticle


class RSSNewsScraper:
    """
    RSS-first scraper for Indonesian financial news.

    Fetches all configured feeds concurrently-agnostic (sequential feedparser
    calls; each feed is fast) and returns a flat list of NewsArticle. Ticker
    filtering/tagging is delegated to the orchestrator (aliases.py) so that
    this class stays a pure ingestion layer.

    Only VERIFIED feeds (returns >0 entries on test) are included by default.
    """

    DEFAULT_FEEDS: List[tuple[str, str]] = [
        ("https://www.cnbcindonesia.com/market/rss", "CNBC Indonesia"),
        ("https://www.cnbcindonesia.com/news/rss", "CNBC Indonesia"),
        ("https://finance.detik.com/rss", "Detik Finance"),
        ("https://ekbis.sindonews.com/rss", "Sindonews Ekbis"),
        ("https://katadata.co.id/rss", "Katadata"),
        ("https://www.antaranews.com/rss/ekonomi.xml", "Antara Ekonomi"),
        ("https://www.antaranews.com/rss/ekonomi-bisnis.xml", "Antara Ekbis"),
        ("https://www.republika.co.id/rss/ekonomi", "Republika Ekonomi"),
    ]

    def __init__(
        self,
        feeds: Iterable[tuple[str, str]] | None = None,
        timeout: int = 10,
    ):
        self.feeds = list(feeds) if feeds is not None else list(self.DEFAULT_FEEDS)
        self.timeout = timeout

    def fetch_articles(self) -> List[NewsArticle]:
        """
        Fetch and parse all configured RSS feeds.

        Returns:
            Flat list of NewsArticle across all feeds. Skips malformed entries.
        """
        scraped_at = datetime.now(timezone.utc)
        articles: List[NewsArticle] = []

        for feed_url, source_name in self.feeds:
            try:
                parsed = feedparser.parse(feed_url)
            except Exception as e:
                print(f"[RSSNewsScraper] Failed to parse {feed_url}: {e}")
                continue

            if not parsed.entries:
                print(f"[RSSNewsScraper] Empty feed: {feed_url}")
                continue

            for entry in parsed.entries:
                try:
                    title = str(entry.get("title", "")).strip()
                    link = str(entry.get("link", "")).strip()
                    if not title or not link:
                        continue

                    published_at = self._parse_published(entry) or scraped_at
                    articles.append(
                        NewsArticle(
                            title=title,
                            url=link,
                            source=source_name,
                            published_at=published_at,
                            scraped_at=scraped_at,
                            content_hash=self._hash(title, link),
                        )
                    )
                except Exception as e:
                    print(f"[RSSNewsScraper] Skip malformed entry from {source_name}: {e}")
                    continue

        return articles

    @staticmethod
    def _parse_published(entry: dict) -> datetime | None:
        for key in ("published_parsed", "updated_parsed"):
            value = entry.get(key)
            if value:
                try:
                    return datetime(*value[:6], tzinfo=timezone.utc)
                except (TypeError, ValueError):
                    continue
        return None

    @staticmethod
    def _hash(title: str, url: str) -> str:
        combined = f"{title.strip().lower()}|{url.strip().lower()}"
        return hashlib.sha256(combined.encode("utf-8")).hexdigest()