"""Read-only HTTP API for the FinSentiMind MVP terminal."""

from __future__ import annotations

import re
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, Request

from app.services.market_data import (
    DataUnavailableError,
    MarketDataRepository,
    TickerNotFoundError,
)


router = APIRouter(prefix="/api", tags=["market-data"])

_TICKER_RE = re.compile(r"^[A-Z0-9]{1,12}$")


def get_repository(request: Request) -> MarketDataRepository:
    return request.app.state.repository


def _validated_ticker(repo: MarketDataRepository, ticker: str) -> str:
    symbol = ticker.strip().upper()
    if not _TICKER_RE.fullmatch(symbol) or not repo.has_ticker(symbol):
        raise HTTPException(status_code=404, detail=f"Ticker '{ticker}' tidak ada di dataset lokal.")
    return symbol


@router.get("/health", summary="API liveness and dataset availability")
def api_health(request: Request) -> dict:
    repo = get_repository(request)
    try:
        meta = repo.meta()
        return {
            "status": "ok",
            "read_only": True,
            "dataset_available": True,
            "latest_trading_date": meta["latest_trading_date"],
            "universe_size": meta["universe_size"],
        }
    except DataUnavailableError as exc:
        return {
            "status": "degraded",
            "read_only": True,
            "dataset_available": False,
            "detail": str(exc),
        }


@router.get("/meta", summary="Dataset metadata and provenance")
def get_meta(request: Request) -> dict:
    return get_repository(request).meta()


@router.get("/market/overview", summary="Latest local-session market snapshot")
def market_overview(request: Request) -> dict:
    return get_repository(request).overview()


@router.get("/stocks", summary="Stock explorer rows from the latest session")
def list_stocks(
    request: Request,
    search: Optional[str] = Query(default=None, max_length=32),
    tickers: Optional[str] = Query(
        default=None,
        description="Comma-separated ticker filter, e.g. BBCA,BBRI",
    ),
    sort: str = Query(default="ticker"),
    order: str = Query(default="asc", pattern="^(?i)(asc|desc)$"),
) -> dict:
    parsed_tickers: Optional[List[str]] = None
    if tickers:
        parsed_tickers = [part for part in tickers.split(",") if part.strip()]
    return get_repository(request).list_stocks(
        search=search,
        tickers=parsed_tickers,
        sort=sort,
        order=order,
    )


@router.get("/stocks/{ticker}", summary="Single stock snapshot and data quality")
def stock_detail(ticker: str, request: Request) -> dict:
    repo = get_repository(request)
    symbol = _validated_ticker(repo, ticker)
    try:
        return repo.stock_detail(symbol)
    except TickerNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Ticker '{ticker}' tidak ditemukan.") from exc


@router.get("/stocks/{ticker}/ohlcv", summary="Historical OHLCV series")
def stock_ohlcv(
    ticker: str,
    request: Request,
    start: Optional[date] = Query(default=None, description="Inclusive start date (YYYY-MM-DD)"),
    end: Optional[date] = Query(default=None, description="Inclusive end date (YYYY-MM-DD)"),
    limit: int = Query(default=500, ge=1, le=2000),
) -> dict:
    repo = get_repository(request)
    symbol = _validated_ticker(repo, ticker)
    if start and end and start > end:
        raise HTTPException(status_code=422, detail="start tidak boleh setelah end.")
    try:
        return repo.ohlcv(symbol, start=start, end=end, limit=limit)
    except TickerNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Ticker '{ticker}' tidak ditemukan.") from exc


@router.get("/data/health", summary="Dataset health report from the TASK 106 validator")
def data_health(
    request: Request,
    freshness_days: int = Query(default=7, ge=0, le=365),
) -> dict:
    repo = get_repository(request)
    try:
        return repo.data_health(freshness_threshold_days=freshness_days)
    except DataUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/instruments", summary="Auditable instrument master data")
def list_instruments(request: Request) -> dict:
    master = get_repository(request).instrument_master
    return {
        "count": len(master.instruments),
        "metadata_version": master.metadata_version,
        "effective_from": master.effective_from,
        "updated_at": master.updated_at,
        "sources": master.sources,
        "revisions": master.revisions,
        "instruments": [instrument.to_dict() for instrument in master.instruments],
    }


@router.get("/instruments/{ticker}", summary="Instrument metadata for one ticker")
def instrument_detail(ticker: str, request: Request) -> dict:
    master = get_repository(request).instrument_master
    symbol = ticker.strip().upper()
    instrument = master.get(symbol)
    if instrument is None:
        raise HTTPException(
            status_code=404,
            detail=f"Instrument metadata for '{ticker}' tidak tersedia.",
        )
    return instrument.to_dict()
