"""Architectural-memory API endpoints (Phase 3).

Exposes the beads-style knowledge graph that GyroCompass accumulates over time —
drift events, decisions, remediation work items and the activity feed — so the
dashboard can render state evolution and "ready work" views.

Routes
------
GET /api/memory/stats      — node/edge/open-work counts
GET /api/memory/ready      — remediation items that are unblocked and open
GET /api/memory/activity   — recent event-log entries
GET /api/memory/nodes      — list nodes, optionally filtered by type/status
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, status
from loguru import logger

from gyrocompass.memory import MemoryStore, Node

router = APIRouter(prefix="/memory", tags=["memory"])


# ── Helpers ────────────────────────────────────────────────────────────────────


def _resolve_repo(repo_path_str: str) -> Path:
    """Resolve and validate a repository path."""
    p = Path(repo_path_str).expanduser().resolve()
    if not p.exists():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Repository path does not exist: {p}",
        )
    if not p.is_dir():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Repository path is not a directory: {p}",
        )
    return p


def _node_to_dict(node: Node) -> dict[str, Any]:
    return asdict(node)


# ── Endpoints ──────────────────────────────────────────────────────────────────


@router.get("/stats", summary="Architectural-memory statistics")
async def memory_stats(repo_path: str = ".") -> dict[str, Any]:
    """Return node/edge/open-work counts for the repository's memory graph."""
    repo = _resolve_repo(repo_path)
    store = MemoryStore(repo_path=repo)
    try:
        return store.stats()
    finally:
        store.close()


@router.get("/ready", summary="Ready remediation work")
async def memory_ready(repo_path: str = ".") -> list[dict[str, Any]]:
    """Return remediation items that are open and unblocked, highest priority first."""
    repo = _resolve_repo(repo_path)
    store = MemoryStore(repo_path=repo)
    try:
        return [_node_to_dict(n) for n in store.ready_remediation()]
    finally:
        store.close()


@router.get("/activity", summary="Recent memory activity feed")
async def memory_activity(repo_path: str = ".", limit: int = 50) -> list[dict[str, Any]]:
    """Return the most recent event-log entries for the activity feed."""
    repo = _resolve_repo(repo_path)
    store = MemoryStore(repo_path=repo)
    try:
        return store.activity(limit)
    finally:
        store.close()


@router.get("/nodes", summary="List memory nodes")
async def memory_nodes(
    repo_path: str = ".",
    type: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """List memory nodes, optionally filtered by type and/or status."""
    repo = _resolve_repo(repo_path)
    store = MemoryStore(repo_path=repo)
    try:
        nodes = store.list_nodes(type=type, status=status)
        return [_node_to_dict(n) for n in nodes]
    finally:
        store.close()
