"""Strict-mode tests for ingestion jobs used by the TASK 107 refresh."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.jobs.run_news_scraper import run_news_pipeline
from app.jobs.run_sentiment_pipeline import run_sentiment_analysis


def test_news_pipeline_strict_propagates_provider_error():
    with patch(
        "app.jobs.run_news_scraper.run_rss_pipeline",
        side_effect=RuntimeError("RSS provider down"),
    ), patch(
        "app.jobs.run_news_scraper.run_telegram_pipeline",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "app.jobs.run_news_scraper.run_stockbit_pipeline",
        return_value=[],
    ):
        with pytest.raises(RuntimeError, match="RSS provider down"):
            run_news_pipeline(strict=True)


def test_sentiment_pipeline_strict_propagates_fetch_error():
    client = MagicMock()
    client.table.side_effect = RuntimeError("Supabase unavailable")
    with patch(
        "app.jobs.run_sentiment_pipeline.get_supabase_client",
        return_value=client,
    ):
        with pytest.raises(RuntimeError, match="Supabase unavailable"):
            run_sentiment_analysis(strict=True)


def test_sentiment_pipeline_strict_propagates_analysis_failures():
    client = MagicMock()
    response = MagicMock()
    response.data = [{"id": 1, "title": "BBCA naik"}]
    client.table.return_value.select.return_value.is_.return_value.execute.return_value = response

    with patch(
        "app.jobs.run_sentiment_pipeline.get_supabase_client",
        return_value=client,
    ), patch(
        "app.jobs.run_sentiment_pipeline.analyze_text",
        side_effect=RuntimeError("analysis error"),
    ):
        with pytest.raises(RuntimeError, match="failed for 1 article"):
            run_sentiment_analysis(strict=True)
