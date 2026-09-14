import hashlib
import os
from datetime import datetime, timezone
from typing import List, Optional

import requests

from app.models.schemas import NewsArticle


class StockbitScraper:
    """
    Best-effort Stockbit stream scraper.

    NOTE: Stockbit does not expose a public API. This scraper relies on a
    manually-provisioned Bearer token (env: STOCKBIT_BEARER_TOKEN) and the
    internal `/stream/v1/streams` endpoint. It is treated as best-effort:
        - If the token is missing/expired, the scraper returns an empty list.
        - Endpoint changes may break this without warning.

    Do NOT treat Stockbit as a critical source. It is a bonus signal.
    """

    STREAM_URL = "https://exodus.stockbit.com/stream/v1/streams"
    BASE_URL = "https://stockbit.com"

    def __init__(
        self,
        bearer_token: Optional[str] = None,
        timeout: int = 10,
        headers: Optional[dict] = None,
    ):
        self.bearer_token = bearer_token or os.getenv("STOCKBIT_BEARER_TOKEN")
        self.timeout = timeout
        self.headers = {
            "User-Agent": "Stockbit/1.0 (Android)",
            "Accept": "application/json",
            "Authorization": f"Bearer {self.bearer_token}" if self.bearer_token else "",
        }
        if headers:
            self.headers.update(headers)

    def is_configured(self) -> bool:
        return bool(self.bearer_token)

    def fetch_articles(self, ticker: str, limit: int = 30) -> List[NewsArticle]:
        """
        Fetch recent stream posts mentioning the ticker.

        Args:
            ticker: Stock code keyword (case-insensitive).
            limit: Max posts to request.

        Returns:
            List[NewsArticle]. Empty if not configured or request fails.
        """
        if not self.is_configured():
            print("[StockbitScraper] Not configured (missing STOCKBIT_BEARER_TOKEN). Skipping.")
            return []

        params = {"symbol": ticker.upper(), "limit": limit, "type": "news"}
        try:
            response = requests.get(
                self.STREAM_URL,
                headers=self.headers,
                params=params,
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.exceptions.RequestException as e:
            print(f"[StockbitScraper] Request failed: {e}")
            return []
        except ValueError as e:
            print(f"[StockbitScraper] Invalid JSON response: {e}")
            return []

        posts = payload.get("data") or payload.get("streams") or []
        scraped_at = datetime.now(timezone.utc)
        articles: List[NewsArticle] = []

        for post in posts:
            try:
                title = (post.get("title") or post.get("content") or "").strip()
                if not title:
                    continue
                post_id = post.get("id") or post.get("stream_id")
                url = f"{self.BASE_URL}/stream/{post_id}" if post_id else self.BASE_URL

                published_at = self._parse_date(post.get("created_at")) or scraped_at
                articles.append(
                    NewsArticle(
                        title=title[:280],
                        url=url,
                        source="Stockbit",
                        published_at=published_at,
                        scraped_at=scraped_at,
                        content_hash=self._hash(title, url),
                    )
                )
            except Exception as e:
                print(f"[StockbitScraper] Skip malformed post: {e}")
                continue

        return articles

    @staticmethod
    def _parse_date(raw: object) -> Optional[datetime]:
        if not raw:
            return None
        if isinstance(raw, (int, float)):
            try:
                return datetime.fromtimestamp(float(raw), tz=timezone.utc)
            except (OSError, ValueError):
                return None
        if isinstance(raw, str):
            try:
                return datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                return None
        return None

    @staticmethod
    def _hash(title: str, url: str) -> str:
        combined = f"{title.strip().lower()}|{url.strip().lower()}"
        return hashlib.sha256(combined.encode("utf-8")).hexdigest()