from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Optional, Set
from zoneinfo import ZoneInfo

from app.core.logging import get_logger


logger = get_logger(__name__)


# Safety cap: longest IDX holiday stretch is ~7 days (Idul Fitri).
# 30 days is a generous guardrail against infinite loops.
_MAX_LOOKAHEAD_DAYS = 30


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SignalContext:
    """
    Context describing when a model prediction is made and what it targets.

    Attributes:
        trading_date: The session whose data is used as features.
        signal_cutoff: End of information window for trading_date (WIB).
        decision_time: When the model makes the prediction (WIB).
            In MVP: equal to signal_cutoff.
        target_session: The session being predicted (next trading day
            after trading_date).
    """

    trading_date: date
    signal_cutoff: datetime
    decision_time: datetime
    target_session: date


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize_to_market_tz(timestamp: datetime, tz: ZoneInfo) -> datetime:
    """
    Convert timestamp to market timezone.

    - Naive datetime -> ValueError (assumption is dangerous in finance).
    - Aware datetime in other tz -> astimezone(tz).
    - Aware datetime already in tz -> returned as-is.
    """
    if timestamp.tzinfo is None:
        raise ValueError(
            "Naive datetime is not allowed. Attach tzinfo explicitly "
            "(e.g., tzinfo=ZoneInfo('Asia/Jakarta') or ZoneInfo('UTC'))."
        )
    return timestamp.astimezone(tz)


def signal_cutoff_for(
    trading_date: date,
    cutoff_time: time,
    tz: ZoneInfo,
) -> datetime:
    """Build a tz-aware datetime at the given cutoff on trading_date."""
    return datetime(
        trading_date.year,
        trading_date.month,
        trading_date.day,
        cutoff_time.hour,
        cutoff_time.minute,
        cutoff_time.second,
        tzinfo=tz,
    )


# ---------------------------------------------------------------------------
# Core alignment
# ---------------------------------------------------------------------------

def map_to_trading_date(
    timestamp: datetime,
    trading_dates: Set[date],
    cutoff: time,
    tz: ZoneInfo,
) -> date:
    """
    Map a timestamp to the next valid trading session.

    Cases:
        A. Trading day, time <= cutoff -> that day
        B. Trading day, time > cutoff  -> next trading day
        C. Weekend (any time)          -> next trading day (Mon)
        D. Holiday (any time)          -> next trading day
        E. Aware in other tz           -> convert to tz, then A-D

    Args:
        timestamp: When the information became available. Must be tz-aware.
        trading_dates: Set of valid trading session dates.
        cutoff: Daily information cutoff (e.g., time(16, 0) for 16:00 WIB).
        tz: Market timezone (e.g., Asia/Jakarta).

    Returns:
        A trading_date from `trading_dates`.

    Raises:
        ValueError: If timestamp is naive, trading_dates is empty, or no
            trading session found within _MAX_LOOKAHEAD_DAYS.
    """
    if not trading_dates:
        raise ValueError("trading_dates is empty — cannot map timestamp.")

    local_dt = normalize_to_market_tz(timestamp, tz)
    local_date = local_dt.date()
    local_time = local_dt.time()

    # Determine candidate date before rolling
    if local_time <= cutoff:
        candidate = local_date
    else:
        candidate = local_date + timedelta(days=1)

    # Roll forward until we hit a trading date
    for offset in range(_MAX_LOOKAHEAD_DAYS):
        check = candidate + timedelta(days=offset)
        if check in trading_dates:
            return check

    raise ValueError(
        f"No trading date found within {_MAX_LOOKAHEAD_DAYS} days "
        f"after {timestamp} (candidate start: {candidate})."
    )


# ---------------------------------------------------------------------------
# Signal context builder
# ---------------------------------------------------------------------------

def _next_trading_date(
    after: date,
    trading_dates: Set[date],
) -> date:
    """Return the first trading date strictly greater than `after`."""
    for offset in range(1, _MAX_LOOKAHEAD_DAYS + 1):
        check = after + timedelta(days=offset)
        if check in trading_dates:
            return check
    raise ValueError(
        f"No trading date within {_MAX_LOOKAHEAD_DAYS} days after {after}."
    )


def build_signal_context(
    trading_date: date,
    trading_dates: Set[date],
    cutoff_time: time,
    tz: ZoneInfo,
    decision_time: Optional[datetime] = None,
) -> SignalContext:
    """
    Build a SignalContext for a given trading session.

    Args:
        trading_date: The session whose features we snapshot.
        trading_dates: Set of valid trading dates.
        cutoff_time: Daily information cutoff (WIB).
        tz: Market timezone.
        decision_time: Optional override (must be tz-aware). Defaults to
            signal_cutoff (MVP: prediction happens at close).

    Returns:
        SignalContext.

    Raises:
        ValueError: If trading_date not in trading_dates, or decision_time
            is naive, or no future trading session exists.
    """
    if trading_date not in trading_dates:
        raise ValueError(
            f"trading_date {trading_date} is not in the trading calendar."
        )

    cutoff_dt = signal_cutoff_for(trading_date, cutoff_time, tz)

    if decision_time is None:
        decision_dt = cutoff_dt
    else:
        if decision_time.tzinfo is None:
            raise ValueError("decision_time must be tz-aware.")
        decision_dt = decision_time.astimezone(tz)
        if decision_dt < cutoff_dt:
            raise ValueError(
                f"decision_time {decision_dt} precedes signal_cutoff "
                f"{cutoff_dt} — would allow look-ahead bias."
            )

    target = _next_trading_date(trading_date, trading_dates)

    return SignalContext(
        trading_date=trading_date,
        signal_cutoff=cutoff_dt,
        decision_time=decision_dt,
        target_session=target,
    )