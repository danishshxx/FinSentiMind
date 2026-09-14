from typing import Any, Dict, List

from app.database.supabase_client import get_supabase_client
from app.services.sentiment_service import analyze_text


def run_sentiment_analysis() -> None:
    """
    Orchestrate sentiment analysis for unprocessed news articles in Supabase.

    Fetches rows from 'berita_saham' where 'sentiment_label' is NULL, runs each
    article's title through the IndoBERT sentiment classifier, and updates the
    corresponding row with 'sentiment_label', 'sentiment_score', and 'confidence'.

    Returns:
        None
    """
    supabase = get_supabase_client()

    try:
        response = (
            supabase.table("berita_saham")
            .select("id, title")
            .is_("sentiment_label", "null")
            .execute()
        )
    except Exception as e:
        print(f"[SentimentPipeline] Failed to fetch pending articles: {e}")
        return

    articles: List[Dict[str, Any]] = response.data if response and response.data else []

    if not articles:
        print("[SentimentPipeline] Tidak ada berita baru untuk dianalisis")
        return

    print(f"[SentimentPipeline] Found {len(articles)} article(s) to analyze.")

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
            supabase.table("berita_saham").update(update_payload).eq("id", article_id).execute()
            success_count += 1
        except Exception as e:
            failure_count += 1
            print(f"[SentimentPipeline] Failed to process article id={article_id}: {e}")
            continue

    print(f"[SentimentPipeline] Done. Success: {success_count}, Failed: {failure_count}")


if __name__ == "__main__":
    run_sentiment_analysis()
