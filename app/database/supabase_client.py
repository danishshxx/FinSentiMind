from typing import Any, Dict, List, Optional

from supabase import Client, create_client

from app.core.config import get_settings
from app.core.logging import get_logger


logger = get_logger(__name__)


# PostgREST/Supabase hard cap per query.
_MAX_PAGE_SIZE = 1000

# Runaway-loop safety cap. 10_000 pages × 1000 rows = 10M rows.
# Far beyond any realistic FinSentiMind fetch in the foreseeable future.
_MAX_PAGES = 10_000


def get_supabase_client() -> Client:
    """
    Return an authenticated Supabase client.

    Settings are loaded & validated via app.core.config on first call.
    Raises ConfigError if SUPABASE_URL / SUPABASE_KEY are missing.
    """
    settings = get_settings()
    return create_client(settings.supabase_url, settings.supabase_key)


def fetch_all(
    table: str,
    select: str = "*",
    page_size: int = _MAX_PAGE_SIZE,
    filters: Optional[Dict[str, Any]] = None,
    order_by: str = "id",
    ascending: bool = True,
) -> List[Dict[str, Any]]:
    """
    Fetch ALL rows from a Supabase table using deterministic pagination.

    Supabase / PostgREST caps query results at 1000 rows by default. This
    helper loops in `page_size` increments until the table (or filtered
    subset) is exhausted.

    DETERMINISM REQUIREMENT:
        `order_by` MUST reference a UNIQUE column (e.g., primary key 'id').
        If it does not, rows may shift between pages, causing duplicates
        or missing records. See PostgREST pagination caveats.

    EXACT MULTIPLES:
        If the total row count is an exact multiple of `page_size`, one
        extra empty query is performed to confirm completion. This is a
        deliberate trade-off for guaranteed completeness.

    Args:
        table: Table name (e.g., 'harga_saham').
        select: Column projection string (default '*').
        page_size: Rows per page. Must be 1..1000 (Supabase hard limit).
        filters: Optional dict of column -> value. Values that are
            list/tuple become `.in_(col, values)`; others become `.eq(col, value)`.
        order_by: Column name for deterministic ordering. Default 'id'.
            MUST be unique for correct pagination.
        ascending: Sort direction. Default True.

    Returns:
        List of row dicts, flattened across all pages, in the order
        specified by `order_by`.

    Raises:
        ValueError: If page_size, table, or order_by are invalid.
        Exception: Any error from Supabase `.execute()` is propagated
            unchanged (no swallowing).
    """
    # --- Input validation ---
    if not table or not table.strip():
        raise ValueError("table name must be non-empty")
    if not order_by or not order_by.strip():
        raise ValueError(
            "order_by must be a stable, unique column name "
            "(e.g., 'id') for deterministic pagination"
        )
    if page_size <= 0:
        raise ValueError(f"page_size must be positive, got {page_size}")
    if page_size > _MAX_PAGE_SIZE:
        raise ValueError(
            f"page_size must be <= {_MAX_PAGE_SIZE} "
            f"(Supabase hard limit), got {page_size}"
        )

    supabase = get_supabase_client()
    all_rows: List[Dict[str, Any]] = []
    offset = 0
    page_num = 0

    while page_num < _MAX_PAGES:
        # Build query: select → filters → order → range
        query = supabase.table(table).select(select)

        if filters:
            for key, value in filters.items():
                if isinstance(value, (list, tuple)):
                    query = query.in_(key, list(value))
                else:
                    query = query.eq(key, value)

        query = query.order(order_by, desc=not ascending)
        query = query.range(offset, offset + page_size - 1)

        logger.debug(
            "Fetching page %d from '%s' (offset=%d, page_size=%d)",
            page_num + 1,
            table,
            offset,
            page_size,
        )

        response = query.execute()
        rows = response.data or []

        all_rows.extend(rows)
        page_num += 1

        # Partial page (or empty) → last page reached.
        if len(rows) < page_size:
            break

        offset += page_size

    else:
        # while-loop `else` runs only if loop never broke.
        # Reaching here means we hit _MAX_PAGES without exhausting data.
        logger.warning(
            "Hit max pages (%d) while fetching '%s'. "
            "Result may be incomplete.",
            _MAX_PAGES,
            table,
        )

    logger.info(
        "Fetched %d row(s) from '%s' across %d page(s).",
        len(all_rows),
        table,
        page_num,
    )
    return all_rows