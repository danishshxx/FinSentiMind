# app/scraper/sources/kontan.py
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from typing import List
from urllib.parse import urljoin

from app.models.schemas import NewsArticle
from app.scraper.base import BaseNewsScraper


class KontanScraper(BaseNewsScraper):
    """
    Scraper for Kontan search results.

    Fetches news articles related to a given ticker symbol from
    https://search.kontan.co.id/search/?search={ticker}
    """

    BASE_URL = "https://www.kontan.co.id"
    SEARCH_URL = "https://search.kontan.co.id/search/?search={ticker}"

    def __init__(self, headers: dict | None = None, timeout: int = 10):
        super().__init__(headers=headers, timeout=timeout)

    def fetch_html(self, ticker: str) -> str:
        """
        Fetch the HTML of Kontan search results for a given ticker.

        Args:
            ticker (str): The stock ticker or company name to search for.

        Returns:
            str: Raw HTML content. Returns empty string if request fails.
        """
        url = self.SEARCH_URL.format(ticker=ticker)
        try:
            response = requests.get(url, headers=self.headers, timeout=self.timeout)
            response.raise_for_status()
            return response.text
        except requests.exceptions.RequestException as e:
            print(f"[KontanScraper] Request failed for {url}: {e}")
            return ""

    def extract_articles(self, html: str) -> List[NewsArticle]:
        """
        Parse HTML and extract news articles from search results.

        Args:
            html (str): Raw HTML content.

        Returns:
            List[NewsArticle]: List of extracted articles (may be empty).
        """
        if not html:
            return []

        soup = BeautifulSoup(html, "html.parser")
        articles: List[NewsArticle] = []
        scraped_at = datetime.now()

        # Kontan search results often have <div class="list-item"> or <article> or <h2><a>
        candidates = soup.find_all("article")
        if not candidates:
            # Fallback: look for anchors inside headings or with substantial text
            candidates = [
                a for a in soup.find_all("a", href=True)
                if a.find_parent(["h1", "h2", "h3"]) or len(a.get_text(strip=True)) > 20
            ]

        for element in candidates:
            try:
                if element.name == "article":
                    anchor = element.find("a", href=True)
                    if not anchor:
                        continue
                else:
                    anchor = element

                title = anchor.get_text(strip=True)
                href = anchor.get("href", "")
                if not title or not href:
                    continue

                url = urljoin(self.BASE_URL, href)

                published_at = self._extract_date(element)
                if published_at is None:
                    published_at = scraped_at

                content_hash = self.generate_content_hash(title, url)

                article = NewsArticle(
                    title=title,
                    url=url,
                    source="Kontan",
                    published_at=published_at,
                    scraped_at=scraped_at,
                    content_hash=content_hash,
                )
                articles.append(article)

            except Exception as e:
                print(f"[KontanScraper] Error parsing an article element: {e}")
                continue

        return articles

    def _extract_date(self, element) -> datetime | None:
        """
        Attempt to extract a publication date from an HTML element.
        """
        date_tag = element.find("time") if element.name != "time" else element
        if date_tag:
            datetime_attr = date_tag.get("datetime")
            if datetime_attr:
                try:
                    return datetime.fromisoformat(datetime_attr.replace("Z", "+00:00"))
                except ValueError:
                    pass

        # Look for elements with class containing 'date'
        date_candidates = element.find_all(["span", "small", "div"], class_=lambda c: c and "date" in c.lower())
        for tag in date_candidates:
            text = tag.get_text(strip=True)
            if text:
                parsed = self._parse_date_text(text)
                if parsed:
                    return parsed

        # Try parsing the whole text
        text = element.get_text(" ", strip=True)
        parsed = self._parse_date_text(text)
        return parsed

    def _parse_date_text(self, text: str) -> datetime | None:
        """
        Try to parse a datetime from a string using several common formats.
        """
        from dateutil import parser as date_parser
        try:
            return date_parser.parse(text, fuzzy=True)
        except (ValueError, OverflowError):
            pass

        formats = [
            "%d %b %Y %H:%M",
            "%d %B %Y %H:%M",
            "%Y-%m-%d %H:%M:%S",
            "%d/%m/%Y %H:%M",
        ]
        for fmt in formats:
            try:
                return datetime.strptime(text.strip(), fmt)
            except ValueError:
                continue
        return None