# app/scraper/base.py
import hashlib
from abc import ABC, abstractmethod
from typing import List

from app.models.schemas import NewsArticle


class BaseNewsScraper(ABC):
    """
    Abstract base class for all news scrapers.

    This class enforces a common interface for fetching and parsing news articles,
    and provides shared utilities like default HTTP headers and content hashing.

    Subclasses must implement:
        - fetch_html(url: str) -> str
        - extract_articles(html: str) -> List[NewsArticle]

    Attributes:
        headers (dict): Default HTTP headers to be used in requests.
        timeout (int): Request timeout in seconds.
    """

    DEFAULT_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9,id;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Connection": "keep-alive",
    }

    def __init__(self, headers: dict | None = None, timeout: int = 10):
        """
        Initialize the scraper with optional custom headers and timeout.

        Args:
            headers (dict | None): Custom headers to override or extend defaults.
            timeout (int): Request timeout in seconds. Defaults to 10.
        """
        self.headers = self.DEFAULT_HEADERS.copy()
        if headers:
            self.headers.update(headers)
        self.timeout = timeout

    @abstractmethod
    def fetch_html(self, url: str) -> str:
        """
        Fetch the HTML content of a given URL.

        Args:
            url (str): The target URL.

        Returns:
            str: Raw HTML content as a string.
        """
        raise NotImplementedError

    @abstractmethod
    def extract_articles(self, html: str) -> List[NewsArticle]:
        """
        Parse the provided HTML and extract news articles.

        This method must be implemented by each concrete scraper, as the HTML
        structure varies between sources.

        Args:
            html (str): Raw HTML content.

        Returns:
            List[NewsArticle]: A list of NewsArticle objects extracted from the HTML.
        """
        raise NotImplementedError

    def generate_content_hash(self, title: str, url: str) -> str:
        """
        Generate a SHA-256 hash based on the article's title and URL.

        This hash is used for deduplication to prevent storing the same article
        multiple times, even if the title or URL has minor differences.

        Args:
            title (str): The article title.
            url (str): The article URL.

        Returns:
            str: Hexadecimal SHA-256 hash string.
        """
        # Normalize and combine title and URL for consistent hashing
        combined = f"{title.strip().lower()}|{url.strip().lower()}"
        return hashlib.sha256(combined.encode("utf-8")).hexdigest()