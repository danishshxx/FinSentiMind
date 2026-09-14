from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field


class NewsArticle(BaseModel):
    """
    Represents a single news article collected from a source.

    Attributes:
        title (str): The headline or title of the article.
        url (str): The canonical URL of the article.
        source (str): The source or publisher of the article (e.g., 'CNBC Indonesia').
        published_at (datetime): The publication timestamp of the article.
        scraped_at (datetime): The timestamp when the article was scraped.
        content_hash (Optional[str]): A SHA-256 hash of the article's title and URL,
            used for deduplication. Optional, but recommended to be set by scrapers.
    """

    title: str
    url: str
    source: str
    published_at: datetime
    scraped_at: datetime
    content_hash: Optional[str] = None

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}


class StockPrice(BaseModel):
    """
    Represents a single day of OHLCV (Open, High, Low, Close, Volume) data
    for a given stock.

    Attributes:
        kode_saham (str): The stock code/ticker (e.g., 'BBCA').
        tanggal (date): The trading date.
        harga_buka (float): Opening price.
        harga_tertinggi (float): Highest price of the day.
        harga_terendah (float): Lowest price of the day.
        harga_tutup (float): Closing price.
        volume (int): Number of shares traded.
    """

    kode_saham: str
    tanggal: date
    harga_buka: float
    harga_tertinggi: float
    harga_terendah: float
    harga_tutup: float
    volume: int

    class Config:
        json_encoders = {date: lambda v: v.isoformat()}