import asyncio
import hashlib
from datetime import datetime, timezone
from typing import List, Optional

from telethon import TelegramClient
from telethon.errors import FloodWaitError

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.schemas import NewsArticle


logger = get_logger(__name__)


class TelegramScraper:
    """Optional Telegram scraper. Skips gracefully when not configured."""

    def __init__(
        self,
        api_id: Optional[int] = None,
        api_hash: Optional[str] = None,
        channels: Optional[List[str]] = None,
        session_name: str = "finsentimind_session",
    ):
        settings = get_settings()
        self.api_id = api_id if api_id is not None else settings.telegram_api_id
        self.api_hash = api_hash if api_hash is not None else settings.telegram_api_hash
        self.channels = channels if channels is not None else settings.telegram_channels
        self.session_name = session_name

    def is_configured(self) -> bool:
        return bool(self.api_id and self.api_hash and self.channels)

    async def fetch_articles(
        self, ticker: Optional[str] = None, limit_per_channel: int = 50
    ) -> List[NewsArticle]:
        if not self.is_configured():
            logger.info("Not configured. Skipping (optional source).")
            return []

        scraped_at = datetime.now(timezone.utc)
        articles: List[NewsArticle] = []

        try:
            async with TelegramClient(
                self.session_name, self.api_id, self.api_hash
            ) as client:
                for channel in self.channels:
                    try:
                        async for message in client.iter_messages(
                            channel, limit=limit_per_channel
                        ):
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
                                    published_at = published_at.replace(
                                        tzinfo=timezone.utc
                                    )

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
                            except Exception:
                                logger.warning(
                                    "Skip malformed message in channel %s",
                                    channel,
                                    exc_info=True,
                                )
                                continue
                    except FloodWaitError as e:
                        logger.warning(
                            "FloodWait on channel %s — sleeping %ss",
                            channel,
                            e.seconds,
                        )
                        await asyncio.sleep(e.seconds)
                    except Exception:
                        logger.error(
                            "Failed to read channel %s",
                            channel,
                            exc_info=True,
                        )
                        continue
        except Exception:
            logger.error("Telegram client error", exc_info=True)
            return []

        return articles

    @staticmethod
    def _build_url(channel: str, message_id: int) -> str:
        clean = channel.lstrip("@").replace("https://t.me/", "")
        return f"https://t.me/{clean}/{message_id}"

    @staticmethod
    def _hash(title: str, url: str) -> str:
        combined = f"{title.strip().lower()}|{url.strip().lower()}"
        return hashlib.sha256(combined.encode("utf-8")).hexdigest()