"""GyroCompass FastAPI application entry point.

Start with:
    uvicorn api.main:app --reload --port 7700

Or via the CLI:
    python -m uvicorn api.main:app --host 0.0.0.0 --port 7700
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger

from api.routers import analysis, drift, memory, projects, specs, webhooks


# ── Application factory ────────────────────────────────────────────────────────


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Application lifespan: startup → yield → shutdown."""
    # ── Startup ───────────────────────────────────────────────────────────────
    _configure_logging()
    logger.info("GyroCompass API starting up")

    # Ensure the database tables exist (SQLite or Postgres via SQLAlchemy)
    try:
        from api.database import create_tables  # type: ignore[import]

        await create_tables()
        logger.info("Database tables ready")
    except (ImportError, Exception) as exc:
        logger.warning("Database init skipped: {}", exc)

    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    logger.info("GyroCompass API shutting down")


def _configure_logging() -> None:
    """Set up loguru with a structured format appropriate for production."""
    import sys

    logger.remove()
    logger.add(
        sys.stdout,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> — "
            "<level>{message}</level>"
        ),
        level="INFO",
        colorize=True,
        enqueue=True,
    )


# ── App instance ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="GyroCompass API",
    description=(
        "Architecture guardrails for AI-native development teams.\n\n"
        "GyroCompass continuously monitors your codebase for architectural drift, "
        "enforces invariants via GitHub webhooks, and exposes MCP tools so AI coding "
        "agents like Claude Code and Cursor stay within your design boundaries."
    ),
    version="0.1.0",
    lifespan=_lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)


# ── Middleware ─────────────────────────────────────────────────────────────────

def _get_cors_origins() -> list[str]:
    try:
        from gyrocompass.config import settings

        return settings.CORS_ORIGINS
    except Exception:
        return ["http://localhost:3000", "http://localhost:5173"]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_get_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Routers ────────────────────────────────────────────────────────────────────

app.include_router(projects.router, prefix="/api")
app.include_router(analysis.router, prefix="/api")
app.include_router(drift.router, prefix="/api")
app.include_router(webhooks.router, prefix="/api")
app.include_router(specs.router, prefix="/api")
app.include_router(memory.router, prefix="/api")


# ── Health check ───────────────────────────────────────────────────────────────


@app.get("/health", tags=["health"], summary="Health check")
async def health_check() -> JSONResponse:
    """Return ``{"status": "ok"}`` when the API is healthy."""
    return JSONResponse(content={"status": "ok", "version": "0.1.0"})


@app.get("/", tags=["health"], summary="Root redirect")
async def root() -> JSONResponse:
    """API root — returns service info."""
    return JSONResponse(
        content={
            "service": "GyroCompass API",
            "version": "0.1.0",
            "docs": "/docs",
            "health": "/health",
        }
    )
