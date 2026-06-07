"""Projects management API endpoints.

Routes
------
GET    /api/projects                 — list all known projects
POST   /api/projects                 — register a new project
GET    /api/projects/{project_id}    — get project details
DELETE /api/projects/{project_id}    — remove a project
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException, status
from loguru import logger
from pydantic import BaseModel, Field

router = APIRouter(prefix="/projects", tags=["projects"])

# In-memory project registry (swap for DB persistence in production)
_projects: dict[str, "ProjectRecord"] = {}


# ── Models ─────────────────────────────────────────────────────────────────────


class ProjectRecord(BaseModel):
    """Stored project metadata."""

    project_id: str
    name: str
    repo_path: str
    description: str | None = None
    github_repo: str | None = None
    llm_provider: str = "openai"
    registered_at: datetime = Field(default_factory=datetime.utcnow)
    has_baseline: bool = False


class CreateProjectRequest(BaseModel):
    name: str = Field(..., description="Human-readable project name.")
    repo_path: str = Field(..., description="Absolute path to the repository on the server.")
    description: str | None = Field(default=None, description="Short project description.")
    github_repo: str | None = Field(default=None, description="GitHub repo full name (owner/repo).")
    llm_provider: str = Field(default="openai", description="LLM provider: openai, anthropic, ollama, custom.")
    project_id: str | None = Field(
        default=None,
        description="Stable identifier. Defaults to the repository directory name.",
    )


# ── Helpers ────────────────────────────────────────────────────────────────────


def _has_baseline(repo_path: str) -> bool:
    try:
        from gyrocompass.config import get_state_path

        return get_state_path(Path(repo_path)).exists()
    except Exception:
        return False


# ── Endpoints ──────────────────────────────────────────────────────────────────


@router.get(
    "",
    response_model=list[ProjectRecord],
    status_code=status.HTTP_200_OK,
    summary="List all registered projects",
)
async def list_projects() -> list[ProjectRecord]:
    """Return a list of all registered projects."""
    return sorted(_projects.values(), key=lambda p: p.registered_at, reverse=True)


@router.post(
    "",
    response_model=ProjectRecord,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new project",
)
async def create_project(request: CreateProjectRequest) -> ProjectRecord:
    """Register a new project with GyroCompass."""
    p = Path(request.repo_path).expanduser().resolve()
    if not p.is_dir():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Repository path does not exist or is not a directory: {p}",
        )

    project_id = request.project_id or p.name
    if project_id in _projects:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Project '{project_id}' is already registered.",
        )

    record = ProjectRecord(
        project_id=project_id,
        name=request.name,
        repo_path=str(p),
        description=request.description,
        github_repo=request.github_repo,
        llm_provider=request.llm_provider,
        has_baseline=_has_baseline(str(p)),
    )
    _projects[project_id] = record
    logger.info("Project registered: {} → {}", project_id, p)
    return record


@router.get(
    "/{project_id}",
    response_model=ProjectRecord,
    status_code=status.HTTP_200_OK,
    summary="Get project details",
)
async def get_project(project_id: str) -> ProjectRecord:
    """Return details for a specific project."""
    record = _projects.get(project_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project '{project_id}' not found.",
        )
    # Refresh baseline flag
    record.has_baseline = _has_baseline(record.repo_path)
    return record


@router.delete(
    "/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a registered project",
)
async def delete_project(project_id: str) -> None:
    """Unregister a project from GyroCompass (does not delete any files)."""
    if project_id not in _projects:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project '{project_id}' not found.",
        )
    del _projects[project_id]
    logger.info("Project unregistered: {}", project_id)
