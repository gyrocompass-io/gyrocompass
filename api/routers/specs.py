"""Architecture spec (component) management API endpoints.

Routes
------
GET    /api/specs/{project_id}                    — list all components for a project
GET    /api/specs/{project_id}/{component_id}     — get a single component
POST   /api/specs/{project_id}                    — add / update a component
DELETE /api/specs/{project_id}/{component_id}     — remove a component
GET    /api/specs/{project_id}/rules              — list rules for a project
POST   /api/specs/{project_id}/rules              — add a rule to .gyrorules.yaml
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import yaml
from fastapi import APIRouter, HTTPException, status
from loguru import logger
from pydantic import BaseModel, Field

from gyrocompass.models import ArchitectureElement, ElementStatus, ElementType, Rules

router = APIRouter(prefix="/specs", tags=["specs"])


# ── Request / response models ──────────────────────────────────────────────────


class UpsertComponentRequest(BaseModel):
    """Body for POST /api/specs/{project_id}."""

    component_id: str = Field(..., description="Unique component key, e.g. 'api/auth'.")
    type: str = Field(default="component", description="ElementType value.")
    description: str = Field(..., description="Human-readable description.")
    tags: list[str] = Field(default_factory=list)
    status: str = Field(default="implemented", description="ElementStatus value.")
    facts: list[str] = Field(default_factory=list)
    parent: str | None = None


class AddRuleRequest(BaseModel):
    """Body for POST /api/specs/{project_id}/rules."""

    rule_id: str = Field(..., description="Unique rule identifier, e.g. 'no-eval'.")
    rule_type: str = Field(default="invariant", description="'principle', 'invariant', or 'adr'.")
    description: str = Field(..., description="Human-readable rule description.")
    enforcement: str = Field(default="warn", description="'block', 'warn', or 'suggest'.")
    pattern: str = Field(..., description="Regex or import name to check (prefix '!' to negate).")
    pattern_type: str = Field(default="code_pattern", description="'import_check', 'code_pattern', 'file_exists'.")
    scope: list[str] = Field(default_factory=list, description="File glob patterns to restrict scope.")
    suggestion: str | None = Field(default=None, description="Remediation suggestion.")


# ── Helpers ────────────────────────────────────────────────────────────────────


def _project_repo_path(project_id: str) -> Path:
    """Resolve a project_id to a local repository path."""
    import os

    base_dir = os.environ.get("GYRO_REPO_BASE_DIR")
    if base_dir:
        base = Path(base_dir)
        for candidate in [base / project_id, base / project_id.replace("__", "/")]:
            if candidate.is_dir():
                return candidate.resolve()

    # Fall back to CWD — useful in single-repo deployments
    return Path.cwd()


def _load_state_raw(repo_path: Path) -> dict[str, Any]:
    """Load .gyrostate.yaml as a raw dict.  Returns empty structure if absent."""
    from gyrocompass.config import get_state_path

    sp = get_state_path(repo_path)
    if not sp.exists():
        return {"metadata": {"project": repo_path.name, "version": "1.0", "serial": 1}, "architecture": {}}
    with sp.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _save_state_raw(raw: dict[str, Any], repo_path: Path) -> None:
    from gyrocompass.config import get_state_path

    sp = get_state_path(repo_path)
    sp.parent.mkdir(parents=True, exist_ok=True)
    with sp.open("w", encoding="utf-8") as fh:
        yaml.dump(raw, fh, default_flow_style=False, allow_unicode=True, sort_keys=False)


def _load_rules_raw(repo_path: Path) -> dict[str, Any]:
    from gyrocompass.config import get_rules_path

    rp = get_rules_path(repo_path)
    if not rp.exists():
        return {"principles": {}, "adrs": {}, "invariants": {}}
    with rp.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _save_rules_raw(raw: dict[str, Any], repo_path: Path) -> None:
    from gyrocompass.config import get_rules_path

    rp = get_rules_path(repo_path)
    rp.parent.mkdir(parents=True, exist_ok=True)
    with rp.open("w", encoding="utf-8") as fh:
        yaml.dump(raw, fh, default_flow_style=False, allow_unicode=True, sort_keys=False)


# ── Endpoints ──────────────────────────────────────────────────────────────────


@router.get(
    "/{project_id}",
    response_model=dict[str, Any],
    status_code=status.HTTP_200_OK,
    summary="List all components for a project",
)
async def list_components(project_id: str) -> dict[str, Any]:
    """Return all architecture components from `.gyro/.gyrostate.yaml`."""
    repo_path = _project_repo_path(project_id)
    raw = _load_state_raw(repo_path)
    architecture = raw.get("architecture", {})
    return {
        "project_id": project_id,
        "project": raw.get("metadata", {}).get("project", project_id),
        "component_count": len(architecture),
        "components": architecture,
    }


@router.get(
    "/{project_id}/component/{component_id:path}",
    response_model=dict[str, Any],
    status_code=status.HTTP_200_OK,
    summary="Get a single component",
)
async def get_component(project_id: str, component_id: str) -> dict[str, Any]:
    """Return details for a single architecture component."""
    component_id = unquote(component_id)
    repo_path = _project_repo_path(project_id)
    raw = _load_state_raw(repo_path)
    architecture = raw.get("architecture", {})
    elem = architecture.get(component_id)
    if elem is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Component '{component_id}' not found in project '{project_id}'.",
        )
    return {"component_id": component_id, **elem}


@router.post(
    "/{project_id}",
    response_model=dict[str, Any],
    status_code=status.HTTP_200_OK,
    summary="Add or update a component",
)
async def upsert_component(project_id: str, request: UpsertComponentRequest) -> dict[str, Any]:
    """Add a new component or update an existing one in `.gyro/.gyrostate.yaml`."""
    repo_path = _project_repo_path(project_id)

    try:
        elem_type = ElementType(request.type)
    except ValueError:
        elem_type = ElementType.component

    try:
        elem_status = ElementStatus(request.status)
    except ValueError:
        elem_status = ElementStatus.implemented

    element = ArchitectureElement(
        type=elem_type,
        description=request.description,
        tags=request.tags,
        status=elem_status,
        facts=request.facts,
        parent=request.parent,
    )

    raw = _load_state_raw(repo_path)
    raw.setdefault("architecture", {})[request.component_id] = json.loads(
        element.model_dump_json()
    )

    try:
        _save_state_raw(raw, repo_path)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save component: {exc}",
        ) from exc

    logger.info("Component upserted: project={} component={}", project_id, request.component_id)
    return {"component_id": request.component_id, **json.loads(element.model_dump_json())}


@router.delete(
    "/{project_id}/component/{component_id:path}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a component from the spec",
)
async def delete_component(project_id: str, component_id: str) -> None:
    """Remove a component from `.gyro/.gyrostate.yaml`."""
    component_id = unquote(component_id)
    repo_path = _project_repo_path(project_id)
    raw = _load_state_raw(repo_path)
    architecture = raw.get("architecture", {})

    if component_id not in architecture:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Component '{component_id}' not found in project '{project_id}'.",
        )

    del architecture[component_id]
    try:
        _save_state_raw(raw, repo_path)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save changes: {exc}",
        ) from exc

    logger.info("Component deleted: project={} component={}", project_id, component_id)


@router.get(
    "/{project_id}/rules",
    response_model=dict[str, Any],
    status_code=status.HTTP_200_OK,
    summary="List rules for a project",
)
async def list_rules(project_id: str) -> dict[str, Any]:
    """Return all rules from `.gyro/.gyrorules.yaml`."""
    repo_path = _project_repo_path(project_id)
    raw = _load_rules_raw(repo_path)
    return {
        "project_id": project_id,
        "principle_count": len(raw.get("principles", {})),
        "invariant_count": len(raw.get("invariants", {})),
        "adr_count": len(raw.get("adrs", {})),
        "rules": raw,
    }


@router.post(
    "/{project_id}/rules",
    response_model=dict[str, Any],
    status_code=status.HTTP_201_CREATED,
    summary="Add a rule to .gyrorules.yaml",
)
async def add_rule(project_id: str, request: AddRuleRequest) -> dict[str, Any]:
    """Add a new rule (principle, invariant, or ADR) to `.gyro/.gyrorules.yaml`."""
    repo_path = _project_repo_path(project_id)
    raw = _load_rules_raw(repo_path)

    section = f"{request.rule_type}s"
    if section not in ("principles", "invariants", "adrs"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid rule_type '{request.rule_type}'. Use 'principle', 'invariant', or 'adr'.",
        )

    if request.rule_id in raw.get(section, {}):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Rule '{request.rule_id}' already exists under '{section}'.",
        )

    rule_entry: dict[str, Any] = {
        "description": request.description,
        "status": "active",
        "enforcement": request.enforcement,
        "scope": request.scope,
        "evidence": [
            {
                "pattern": request.pattern,
                "type": request.pattern_type,
                "description": request.suggestion or request.description,
            }
        ],
    }

    raw.setdefault(section, {})[request.rule_id] = rule_entry

    try:
        _save_rules_raw(raw, repo_path)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save rule: {exc}",
        ) from exc

    logger.info("Rule added: project={} rule_id={} type={}", project_id, request.rule_id, request.rule_type)
    return {"rule_id": request.rule_id, "rule_type": request.rule_type, **rule_entry}
