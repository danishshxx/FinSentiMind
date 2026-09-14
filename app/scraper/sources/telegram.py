import hashlib
import os
from datetime import datetime, timezone
from typing import List, Optional

from telethon import TelegramClient
from telethon.errors import FloodWaitError

from app.models.schemas import NewsArticle


class TelegramScraper:
    """
    Optional Telegram channel scraper built on Telethon.

    This scraper is disabled by default. It only runs if the following
    environment variables are set:
        - TELEGRAM_API_ID
        - TELEGRAM_API_HASH
        - TELEGRAM_CHANNELS (comma-separated, e.g. "@idx_channel1,@saham_update")

    Telegram is treated as best-effort: any failure (auth, flood, parsing)
    results in an empty list rather than crashing the orchestrator.
    """

    def __init__(
        self,
        api_id: Optional[int] = None,
        api_hash: Optional[str] = None,
        channels: Optional[List[str]] = None,
        session_name: str = "finsentimind_session",
    ):
        self.api_id = api_id if api_id is not None else self._env_int("TELEGRAM_API_ID")
        self.api_hash = api_hash if api_hash is not None else os.getenv("TELEGRAM_API_HASH")
        self.channels = channels if channels is not None else self._env_channels()
        self.session_name = session_name

    def is_configured(self) -> bool:
        """Return True if Telegram credentials and at least one channel are set."""
        return bool(self.api_id and self.api_hash and self.channels)

    async def fetch_articles(
        self, ticker: str | None = None, limit_per_channel: int = 50
    ) -> List[NewsArticle]:
        """
        Fetch recent messages from configured Telegram channels.

        Args:
            ticker: Optional keyword filter (case-insensitive).
            limit_per_channel: Max messages fetched per channel.

        Returns:
            List[NewsArticle]. Empty if not configured or all fetches fail.
        """
        if not self.is_configured():
            print("[TelegramScraper] Not configured. Skipping (optional source).")
            return []

        scraped_at = datetime.now(timezone.utc)
        articles: List[NewsArticle] = []

        try:
            async with TelegramClient(self.session_name, self.api_id, self.api_hash) as client:
                for channel in self.channels:
                    try:
                        async for message in client.iter_messages(channel, limit=limit_per_channel):
                            try:
                                text = (message.message or "").strip()
                                if not text:
                                    continue
                                if ticker and ticker.lower() not in text.lower():
                                    continue

                                title = text.split("\n", 1)[0].strip()[:280]
                                url = self._build_url(channel, message.id)
                                published_at = message.date or scraped_at
                                if published_at.tzinfo is None:
                                    published_at = published_at.replace(tzinfo=timezone.utc)

                                articles.append(
                                    NewsArticle(
                                        title=title,
                                        url=url,
                                        source=f"Telegram: {channel}",
                                        published_at=published_at,
                                        scraped_at=scraped_at,
                                        content_hash=self._hash(title, url),
                                    )
                                )
                            except Exception as e:
                                print(f"[TelegramScraper] Skip message in {channel}: {e}")
                                continue
                    except FloodWaitError as e:
                        print(f"[TelegramScraper] FloodWait on {channel}, waiting {e.seconds}s")
                        await asyncio.sleep(e.seconds)
                    except Exception as e:
                        print(f"[TelegramScraper] Failed to read channel {channel}: {e}")
                        continue
        except Exception as e:
            print(f"[TelegramScraper] Client error: {e}. Returning empty list.")
            return []

        return articles

    @staticmethod
    def _build_url(channel: str, message_id: int) -> str:
        clean = channel.lstrip("@")
        if clean.startswith("https://t.me/"):
            clean = clean.replace("https://t.me/", "")
        return f"https://t.me/{clean}/{message_id}"

    @staticmethod
    def _hash(title: str, url: str) -> str:
        combined = f"{title.strip().lower()}|{url.strip().lower()}"
        return hashlib.sha256(combined.encode("utf-8")).hexdigest()

    @staticmethod
    def _env_int(name: str) -> Optional[int]:
        value = os.getenv(name)
        if not value:
            return None
        try:
            return int(value)
        except ValueError:
            return None

    @staticmethod
    def _env_channels() -> List[str]:
        raw = os.getenv("TELEGRAM_CHANNELS", "")
        return [c.strip() for c in raw.split(",") if c.strip()]


import asyncio  # placed at bottom to avoid unused import warning in non-async paths