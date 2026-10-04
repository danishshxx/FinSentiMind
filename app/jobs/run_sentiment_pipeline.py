"""Orchestrator untuk sentiment analysis pada berita yang belum dianalisis."""
from typing import Any, Dict, List

from app.core.logging import get_logger, setup_logging
from app.database.supabase_client import get_supabase_client
from app.services.sentiment_service import analyze_text


logger = get_logger(__name__)


def run_sentiment_analysis() -> None:
    """Analyze pending articles and update sentiment columns."""
    supabase = get_supabase_client()

    try:
        response = (
            supabase.table("berita_saham")
            .select("id, title")
            .is_("sentiment_label", "null")
            .execute()
        )
    except Exception:
        logger.error("Failed to fetch pending articles", exc_info=True)
        return

    articles: List[Dict[str, Any]] = (
        response.data if response and response.data else []
    )

    if not articles:
        logger.info("Tidak ada berita baru untuk dianalisis.")
        return

    logger.info("Found %d article(s) to analyze.", len(articles))

    success_count = 0
    failure_count = 0

    for article in articles:
        article_id = article.get("id")
        title = article.get("title", "")

        if article_id is None:
            failure_count += 1
            continue

        try:
            result = analyze_text(title)
            update_payload = {
                "sentiment_label": result["label"],
                "sentiment_score": result["score"],
                "confidence": result["confidence"],
            }
            (
                supabase.table("berita_saham")
                .update(update_payload)
                .eq("id", article_id)
                .execute()
            )
            success_count += 1
        except Exception:
            failure_count += 1
            logger.error(
                "Failed to process article id=%s", article_id, exc_info=True
            )
            continue

    logger.info(
        "Sentiment pipeline done — success=%d failed=%d",
        success_count,
        failure_count,
    )


if __name__ == "__main__":
    setup_logging()
    run_sentiment_analysis()