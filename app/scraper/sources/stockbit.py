"""Stockbit stream scraper (best-effort, optional)."""
import hashlib
from datetime import datetime, timezone
from typing import List, Optional

import requests

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.schemas import NewsArticle


logger = get_logger(__name__)


class StockbitScraper:
    """Best-effort Stockbit scraper. Requires STOCKBIT_BEARER_TOKEN."""

    STREAM_URL = "https://exodus.stockbit.com/stream/v1/streams"
    BASE_URL = "https://stockbit.com"

    def __init__(
        self,
        bearer_token: Optional[str] = None,
        timeout: int = 10,
        headers: Optional[dict] = None,
    ):
        settings = get_settings()
        self.bearer_token = (
            bearer_token if bearer_token is not None
            else settings.stockbit_bearer_token
        )
        self.timeout = timeout
        self.headers = {
            "User-Agent": "Stockbit/1.0 (Android)",
            "Accept": "application/json",
            "Authorization": (
                f"Bearer {self.bearer_token}" if self.bearer_token else ""
            ),
        }
        if headers:
            self.headers.update(headers)

    def is_configured(self) -> bool:
        return bool(self.bearer_token)

    def fetch_articles(self, ticker: str, limit: int = 30) -> List[NewsArticle]:
        if not self.is_configured():
            logger.info("Not configured (missing STOCKBIT_BEARER_TOKEN). Skipping.")
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
        except requests.exceptions.RequestException:
            logger.error("Request failed for ticker %s", ticker, exc_info=True)
            return []
        except ValueError:
            logger.error("Invalid JSON response for ticker %s", ticker, exc_info=True)
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
            except Exception:
                logger.warning("Skip malformed post", exc_info=True)
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