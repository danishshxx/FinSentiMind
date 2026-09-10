from pydantic import BaseModel, HttpUrl, Field
from datetime import datetime
from typing import Optional

class newArticle(BaseModel):
    title: str
    url: str
    source: str
    published_at: datetime
    scraped_at: datetime
    content_hash: Optional[str] = None

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat(),}
    
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