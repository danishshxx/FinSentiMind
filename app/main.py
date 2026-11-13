"""FinSentiMind MVP terminal application.

Run locally:
    uvicorn app.main:app --reload --port 8000

The backend is intentionally read-only. It serves the local research snapshots
under ``ml/data`` plus a dependency-free frontend from ``frontend/``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.core.logging import get_logger, setup_logging
from app.services.market_data import (
    DataUnavailableError,
    MarketDataRepository,
)


logger = get_logger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FRONTEND_DIR = PROJECT_ROOT / "frontend"


def create_app(
    data_dir: Optional[Path | str] = None,
    frontend_dir: Optional[Path | str] = None,
) -> FastAPI:
    setup_logging()
    app = FastAPI(
        title="FinSentiMind — Local Equity Research Terminal",
        version="mvp-001",
        description=(
            "Read-only API over local Parquet research artifacts. "
            "No live market data, predictions, or investment advice."
        ),
    )
    repository = MarketDataRepository(data_dir)
    app.state.repository = repository

    app.include_router(router)

    @app.exception_handler(DataUnavailableError)
    async def _data_unavailable(_request, exc: DataUnavailableError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    frontend = Path(frontend_dir or DEFAULT_FRONTEND_DIR)
    index = frontend / "index.html"
    if index.exists():
        app.mount("/", StaticFiles(directory=str(frontend), html=True), name="terminal")
    else:
        logger.warning("Frontend directory not found: %s", frontend)

        @app.get("/", include_in_schema=False)
        def root() -> dict:
            return {
                "product": "FinSentiMind",
                "version": "mvp-001",
                "message": "Frontend belum tersedia. API aktif di /api.",
                "docs": "/docs",
            }

    logger.info(
        "FinSentiMind MVP app ready (data_dir=%s, latest=%s)",
        repository.data_dir,
        repository.latest_date(),
    )
    return app


app = create_app()
