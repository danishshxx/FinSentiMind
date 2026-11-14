"""Security headers and optional local audit logging."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


PROJECT_ROOT = Path(__file__).resolve().parents[2]
AUDIT_LOG = PROJECT_ROOT / "ml" / "logs" / "api_audit.jsonl"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
        )
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        return response


class AuditLogMiddleware(BaseHTTPMiddleware):
    """Append a minimal request audit line when explicitly enabled.

    Enable with ``FINSENTIMIND_AUDIT_LOG=1``. No query strings, bodies,
    cookies, or credentials are recorded.
    """

    async def dispatch(self, request: Request, call_next: Callable):
        if os.getenv("FINSENTIMIND_AUDIT_LOG", "").strip() != "1":
            return await call_next(request)
        started = time.monotonic()
        response = await call_next(request)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "client": request.client.host if request.client else None,
        }
        try:
            AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
            with AUDIT_LOG.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass
        return response


async def generic_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    import logging

    logging.getLogger(__name__).exception("Unhandled API error: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error."},
    )
