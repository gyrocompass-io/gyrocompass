"""Code analysis API endpoints.

Routes
------
POST /api/analysis/index              — full-repository indexing
GET  /api/analysis/state/{project_id} — retrieve cached architecture state
POST /api/analysis/impact             — impact analysis for a set of changed files
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from loguru import logger
from pydantic import BaseModel, Field

from gyrocompass.indexer import CodeIndexer
from gyrocompass.models import ArchitectureState

# In-memory state cache: project_id → ArchitectureState
# Replace with async DB persistence (api.database) for production deployments.
_state_cache: dict[str, ArchitectureState] = {}

router = APIRouter(prefix="/analysis", tags=["analysis"])


# ── Request / response models ──────────────────────────────────────────────────


class IndexRequest(BaseModel):
    """Request body for POST /api/analysis/index."""

    repo_path: str = Field(..., description="Absolute path to the repository on the server.")
    project_id: str | None = Field(
        default=None,
        description="Stable identifier used to cache / retrieve the state. "
        "Defaults to the repository directory name.",
    )
    save_baseline: bool = Field(
        default=False,
        description="If True, write the fresh state to .gyro/.gyrostate.yaml.",
    )
    use_llm: bool = Field(
        default=False,
        description="If True, enhance component descriptions with the configured LLM.",
    )


class ImpactRequest(BaseModel):
    """Request body for POST /api/analysis/impact."""

    repo_path: str = Field(..., description="Absolute path to the repository on the server.")
    changed_files: list[str] = Field(
        ...,
        description="List of file paths (relative or absolute) that have changed.",
    )
    project_id: str | None = Field(
        default=None,
        description="Project identifier used to retrieve the cached baseline for comparison.",
    )


class ImpactReport(BaseModel):
    """Response from POST /api/analysis/impact."""

    project: str
    changed_files: list[str]
    affected_components: list[str]
    affected_capabilities: list[str]
    affected_endpoints: list[str]
    affected_data_entities: list[str]
    risk_level: str  # "none" | "low" | "medium" | "high"
    summary: str
    partial_state: ArchitectureState


# ── Helpers ────────────────────────────────────────────────────────────────────


def _resolve_repo(repo_path_str: str) -> Path:
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


def _project_id(repo_path: Path, override: str | None) -> str:
    return override or repo_path.name


def _save_state_to_disk(state: ArchitectureState, repo_path: Path) -> None:
    """Write ArchitectureState to .gyro/.gyrostate.yaml."""
    from gyrocompass.config import get_state_path

    state_path = get_state_path(repo_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with state_path.open("w", encoding="utf-8") as fh:
        yaml.dump(
            json.loads(state.model_dump_json()),
            fh,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )
    logger.info("Saved architecture state to {}", state_path)


def _enhance_descriptions_with_llm(state: ArchitectureState) -> None:
    """Optionally enhance component descriptions using the configured LLM."""
    try:
        from gyrocompass.config import settings
        from gyrocompass.llm.providers import get_provider

        provider = get_provider(settings)
        logger.info("Enhancing {} components with LLM…", len(state.architecture))
        for comp_id, elem in state.architecture.items():
            prompt = (
                f"In one sentence, describe the purpose of a software component called "
                f"'{comp_id}' with facts: {', '.join(elem.facts[:5])}."
            )
            try:
                elem.description = provider.complete(
                    prompt,
                    system="Be concise. Respond with exactly one sentence.",
                )
            except Exception as exc:
                logger.debug("LLM description failed for '{}': {}", comp_id, exc)
    except Exception as exc:
        logger.warning("LLM enhancement skipped: {}", exc)


# ── Endpoints ──────────────────────────────────────────────────────────────────


@router.post(
    "/index",
    response_model=ArchitectureState,
    status_code=status.HTTP_200_OK,
    summary="Index a repository and return its architecture state",
)
async def index_repository(request: IndexRequest) -> ArchitectureState:
    """
    Perform a full codebase index using Tree-sitter and return the resulting
    `ArchitectureState`.

    The state is cached in-memory under `project_id` for subsequent `GET` calls.
    Set `save_baseline=true` to also persist the result to `.gyro/.gyrostate.yaml`.
    """
    logger.info("Index requested: repo={} project={}", request.repo_path, request.project_id)

    repo_path = _resolve_repo(request.repo_path)
    project_id = _project_id(repo_path, request.project_id)

    indexer = CodeIndexer(repo_path)
    try:
        state = indexer.index()
    except Exception as exc:
        logger.exception("Full indexing failed for {}: {}", repo_path, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Indexing failed: {exc}",
        ) from exc

    if request.use_llm:
        _enhance_descriptions_with_llm(state)

    # Cache the state
    _state_cache[project_id] = state

    if request.save_baseline:
        try:
            _save_state_to_disk(state, repo_path)
        except Exception as exc:
            logger.error("Failed to save baseline to disk: {}", exc)
            # Don't abort — return the state anyway

    logger.info(
        "Index complete: project={} components={} capabilities={} endpoints={}",
        project_id,
        len(state.architecture),
        len(state.capabilities),
        len(state.surface_area),
    )
    return state


@router.get(
    "/state/{project_id}",
    response_model=ArchitectureState,
    status_code=status.HTTP_200_OK,
    summary="Retrieve cached architecture state for a project",
)
async def get_architecture_state(project_id: str) -> ArchitectureState:
    """
    Retrieve the most recently indexed `ArchitectureState` for a given project.

    If the in-memory cache is empty (e.g., after a server restart), attempts to
    load the state from `.gyro/.gyrostate.yaml` using the `project_id` as a
    directory name hint (resolved relative to `GYRO_REPO_BASE_DIR` if set).
    """
    # 1. Check in-memory cache
    state = _state_cache.get(project_id)
    if state is not None:
        return state

    # 2. Attempt to load from disk
    import os

    base_dir = os.environ.get("GYRO_REPO_BASE_DIR")
    candidates: list[Path] = []
    if base_dir:
        base = Path(base_dir)
        candidates += [base / project_id, base / project_id.replace("__", "/")]
    candidates.append(Path.cwd())

    for candidate in candidates:
        if not candidate.is_dir():
            continue
        try:
            from gyrocompass.config import get_state_path

            state_path = get_state_path(candidate)
            if state_path.exists():
                with state_path.open(encoding="utf-8") as fh:
                    raw = yaml.safe_load(fh)
                state = ArchitectureState.model_validate(raw)
                _state_cache[project_id] = state  # warm the cache
                logger.info("Loaded state from disk for project '{}'", project_id)
                return state
        except Exception as exc:
            logger.debug("Could not load state from {}: {}", candidate, exc)

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=(
            f"No architecture state found for project '{project_id}'. "
            "Run POST /api/analysis/index first."
        ),
    )


@router.post(
    "/impact",
    response_model=ImpactReport,
    status_code=status.HTTP_200_OK,
    summary="Analyse the architectural impact of a set of changed files",
)
async def analyze_impact(request: ImpactRequest) -> ImpactReport:
    """
    Given a list of changed files, return an impact assessment describing:

    - Which architecture **components** are affected.
    - Which **capabilities** may regress.
    - Which **API endpoints** are touched.
    - Which **data entities** could change.
    - An overall **risk level** (none / low / medium / high).

    This endpoint is intended for use in CI pipelines and by AI coding agents
    to understand the blast radius of a proposed change before executing it.
    """
    logger.info(
        "Impact analysis: repo={} files={}", request.repo_path, len(request.changed_files)
    )

    repo_path = _resolve_repo(request.repo_path)
    project_id = _project_id(repo_path, request.project_id)

    # Index only the changed files
    indexer = CodeIndexer(repo_path)
    try:
        partial_state = indexer.index_files(request.changed_files)
    except Exception as exc:
        logger.exception("Partial indexing failed: {}", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Partial indexing failed: {exc}",
        ) from exc

    # Determine affected components from the partial index
    affected_components = sorted(partial_state.architecture.keys())
    affected_capabilities = sorted(partial_state.capabilities.keys())
    affected_endpoints = sorted(partial_state.surface_area.keys())
    affected_data_entities = sorted(partial_state.data_model.entities.keys())

    # Load cached baseline for cross-reference (optional)
    baseline_state: ArchitectureState | None = _state_cache.get(project_id)
    if baseline_state is None:
        try:
            from gyrocompass.config import get_state_path

            sp = get_state_path(repo_path)
            if sp.exists():
                with sp.open(encoding="utf-8") as fh:
                    raw = yaml.safe_load(fh)
                baseline_state = ArchitectureState.model_validate(raw)
        except Exception:
            pass

    # Cross-reference: expand affected components to include dependents
    if baseline_state:
        baseline_comps = set(baseline_state.architecture.keys())
        directly_touched = set(affected_components)
        for comp_id in list(baseline_comps):
            elem = baseline_state.architecture[comp_id]
            for rel_key, rel in elem.relationships.items():
                if rel.target in directly_touched:
                    if comp_id not in affected_components:
                        affected_components.append(comp_id)

    # Compute risk level
    risk_level = _compute_risk_level(
        affected_components=affected_components,
        affected_endpoints=affected_endpoints,
        affected_capabilities=affected_capabilities,
        affected_data_entities=affected_data_entities,
        changed_files=request.changed_files,
    )

    # Build summary
    summary = _build_impact_summary(
        changed_files=request.changed_files,
        affected_components=affected_components,
        affected_capabilities=affected_capabilities,
        affected_endpoints=affected_endpoints,
        affected_data_entities=affected_data_entities,
        risk_level=risk_level,
    )

    logger.info(
        "Impact analysis complete: components={} endpoints={} risk={}",
        len(affected_components),
        len(affected_endpoints),
        risk_level,
    )

    return ImpactReport(
        project=partial_state.metadata.project,
        changed_files=request.changed_files,
        affected_components=affected_components,
        affected_capabilities=affected_capabilities,
        affected_endpoints=affected_endpoints,
        affected_data_entities=affected_data_entities,
        risk_level=risk_level,
        summary=summary,
        partial_state=partial_state,
    )


# ── Pure helpers ───────────────────────────────────────────────────────────────


def _compute_risk_level(
    affected_components: list[str],
    affected_endpoints: list[str],
    affected_capabilities: list[str],
    affected_data_entities: list[str],
    changed_files: list[str],
) -> str:
    """Heuristic risk level based on the breadth of the change."""
    # Critical path heuristics
    sensitive_keywords = {"auth", "payment", "billing", "security", "secret", "token", "credential"}

    all_names = " ".join(
        affected_components + affected_endpoints + affected_capabilities + affected_data_entities + changed_files
    ).lower()

    has_sensitive = any(kw in all_names for kw in sensitive_keywords)
    endpoint_count = len(affected_endpoints)
    component_count = len(affected_components)
    data_count = len(affected_data_entities)

    if has_sensitive or endpoint_count >= 5 or component_count >= 8 or data_count >= 5:
        return "high"
    if endpoint_count >= 2 or component_count >= 4 or data_count >= 2:
        return "medium"
    if endpoint_count >= 1 or component_count >= 1 or data_count >= 1:
        return "low"
    return "none"


def _build_impact_summary(
    changed_files: list[str],
    affected_components: list[str],
    affected_capabilities: list[str],
    affected_endpoints: list[str],
    affected_data_entities: list[str],
    risk_level: str,
) -> str:
    """Build a one-paragraph human-readable impact summary."""
    parts: list[str] = [
        f"Changing {len(changed_files)} file(s) affects "
        f"{len(affected_components)} component(s), "
        f"{len(affected_endpoints)} endpoint(s), "
        f"{len(affected_capabilities)} capability(s), and "
        f"{len(affected_data_entities)} data entity(s). "
        f"Risk level: {risk_level.upper()}."
    ]

    if affected_components:
        comp_preview = ", ".join(affected_components[:5])
        more = f" and {len(affected_components) - 5} more" if len(affected_components) > 5 else ""
        parts.append(f"Affected components: {comp_preview}{more}.")

    if affected_endpoints:
        ep_preview = ", ".join(affected_endpoints[:3])
        parts.append(f"API surface touched: {ep_preview}.")

    if affected_data_entities:
        de_preview = ", ".join(affected_data_entities[:3])
        parts.append(f"Data entities involved: {de_preview}.")

    return " ".join(parts)
