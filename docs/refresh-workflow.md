## Exit Codes

| Code | Status | Meaning |
|---|---|---|
| 0 | SUCCESS | All requested steps + derived chain succeeded |
| 1 | PARTIAL | Source step failed; derived rebuilt from existing data (stale but consistent) |
| 2 | FAILED | Derived chain failed; artifacts inconsistent — do not use downstream |

## Partial Failure Semantics

The derived chain (`load_prices` → `trading_calendar` → `daily_sentiment`)
is **sequential, not transactional**. Each step writes its artifact
independently. There is no rollback.

**Consequences on failure:**

- If step 4 (`trading_calendar`) fails after step 3 (`load_prices`)
  succeeded, `prices_clean.parquet` may be **fresh** while
  `trading_calendar.parquet` and `daily_sentiment.parquet` remain **stale**.
- Exit code `2` (FAILED) signals this inconsistent state.
- The workflow **must be rerun successfully** before any downstream use.
- Do not consume derived artifacts after a failed run without
  re-executing the chain.

**Exit code `1` (PARTIAL)** means the derived chain completed
successfully, but at least one requested source step (news or
backfill) failed. Derived artifacts are consistent **with each other**
but may be stale relative to the failed source. Investigate and rerun
before relying on freshness.

**Exit code `0` (SUCCESS)** is the only status that guarantees the
requested work completed end-to-end.

## CI/CD Integration

```powershell
python -m ml.preprocessing.refresh_all
if ($LASTEXITCODE -ne 0) {
    Write-Error "Refresh not fully successful (exit=$LASTEXITCODE)"
    exit $LASTEXITCODE
}
text

---

## New Tests

### `tests/test_daily_sentiment.py` — 6 new tests (append)

```python
# ---------------------------------------------------------------------------
# Review patch — availability_quality strict rule
# ---------------------------------------------------------------------------

def test_quality_zero_delta_is_live():
    df = _df([_row(
        "BBCA",
        published="2025-01-06T10:00:00+07:00",
        scraped="2025-01-06T10:00:00+07:00",  # zero delta
    )])
    df = calculate_effective_available_at(df, JKT)
    df = classify_availability_quality(df, threshold_days=7)
    assert df["availability_quality"].iloc[0] == "live"


def test_quality_exactly_threshold_is_live():
    df = _df([_row(
        "BBCA",
        published="2025-01-01T10:00:00+07:00",
        scraped="2025-01-08T10:00:00+07:00",  # exactly 7 days
    )])
    df = calculate_effective_available_at(df, JKT)
    df = classify_availability_quality(df, threshold_days=7)
    assert df["availability_quality"].iloc[0] == "live"


def test_quality_above_threshold_is_reconstructed():
    df = _df([_row(
        "BBCA",
        published="2025-01-01T10:00:00+07:00",
        scraped="2025-01-09T10:00:00+07:00",  # 8 days
    )])
    df = calculate_effective_available_at(df, JKT)
    df = classify_availability_quality(df, threshold_days=7)
    assert df["availability_quality"].iloc[0] == "reconstructed"


def test_quality_negative_delta_is_reconstructed(caplog):
    """Anomaly: scraped before published -> reconstructed + warning."""
    df = _df([_row(
        "BBCA",
        published="2025-01-06T10:00:00+07:00",
        scraped="2025-01-05T10:00:00+07:00",  # 1 day BEFORE published
    )])
    df = calculate_effective_available_at(df, JKT)
    with caplog.at_level("WARNING"):
        df = classify_availability_quality(df, threshold_days=7)
    assert df["availability_quality"].iloc[0] == "reconstructed"
    assert any("scraped_at < published_at" in r.message for r in caplog.records)


def test_quality_custom_threshold_3_days():
    # delta = 4 days; with threshold=7 -> live, with threshold=3 -> reconstructed
    df = _df([_row(
        "BBCA",
        published="2025-01-01T10:00:00+07:00",
        scraped="2025-01-05T10:00:00+07:00",  # 4 days
    )])
    df = calculate_effective_available_at(df, JKT)
    assert classify_availability_quality(df.copy(), threshold_days=7)["availability_quality"].iloc[0] == "live"
    assert classify_availability_quality(df.copy(), threshold_days=3)["availability_quality"].iloc[0] == "reconstructed"


def test_audit_logs_when_all_valid(caplog):
    """When all rows valid, audit logs zero-exclusion info message."""
    with patch("ml.preprocessing.daily_sentiment.fetch_all") as mock_fetch:
        mock_fetch.return_value = [
            _row("BBCA", "2025-01-06T10:00:00+07:00", "2025-01-06T10:05:00+07:00"),
            _row("BBRI", "2025-01-06T11:00:00+07:00", "2025-01-06T11:05:00+07:00"),
        ]
        from ml.preprocessing.daily_sentiment import load_news
        with caplog.at_level("INFO"):
            df = load_news()
        assert len(df) == 2
        assert any("News audit" in r.message and "all valid" in r.message
                   for r in caplog.records)


def test_audit_logs_exclusion_counts(caplog):
    """Verify load_news logs exclusion counts when rows are dropped."""
    with patch("ml.preprocessing.daily_sentiment.fetch_all") as mock_fetch:
        mock_fetch.return_value = [
            _row("BBCA", "2025-01-06T10:00:00+07:00", "2025-01-06T10:05:00+07:00"),
            {"ticker": None, "sentiment_label": "POSITIVE", "sentiment_score": 1.0,
             "published_at": "2025-01-06T10:00:00+07:00", "scraped_at": "2025-01-06T10:05:00+07:00"},
            {"ticker": "BBRI", "sentiment_label": None, "sentiment_score": 1.0,
             "published_at": "2025-01-06T10:00:00+07:00", "scraped_at": "2025-01-06T10:05:00+07:00"},
        ]
        from ml.preprocessing.daily_sentiment import load_news
        with caplog.at_level("INFO"):
            df = load_news()
        assert len(df) == 1
        assert any(
            "excluded=2" in r.message and "missing_ticker=1" in r.message
            and "missing_label=1" in r.message
            for r in caplog.records
        )