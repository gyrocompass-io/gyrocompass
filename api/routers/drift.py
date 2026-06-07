"""Drift detection API endpoints.

Routes
------
POST /api/drift/analyze           — run full drift analysis
GET  /api/drift/report/{id}       — retrieve the latest report for a project
POST /api/drift/check-compliance  — lightweight compliance pre-flight check
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from loguru import logger
from pydantic import BaseModel, Field

from gyrocompass.drift import DriftDetector
from gyrocompass.indexer import CodeIndexer
from gyrocompass.models import (
    ArchitectureState,
    DriftEvent,
    DriftReport,
    DriftSeverity,
    DriftType,
    Rules,
)

# In-memory cache: project_id → DriftReport (replace with DB in production)
_report_cache: dict[str, DriftReport] = {}

router = APIRouter(prefix="/drift", tags=["drift"])


# ── Request / response models ──────────────────────────────────────────────────


class DriftAnalyzeRequest(BaseModel):
    """Request body for POST /api/drift/analyze."""

    repo_path: str = Field(..., description="Absolute path to the repository on the server.")
    changed_files: list[str] | None = Field(
        default=None,
        description="Subset of files to analyse (e.g. files changed in a PR). "
        "When None the entire repository is indexed.",
    )
    pr_number: int | None = Field(default=None, description="GitHub PR number (informational).")
    project_id: str | None = Field(
        default=None,
        description="Stable project identifier used for caching/retrieval. "
        "Defaults to the repository directory name.",
    )


class ComplianceCheckRequest(BaseModel):
    """Request body for POST /api/drift/check-compliance."""

    description: str = Field(..., description="Natural-language description of the change.")
    files: list[str] = Field(
        ...,
        description="List of file paths that will be modified by the change.",
    )
    repo_path: str | None = Field(
        default=None,
        description="Repository root for resolving relative file paths and loading the baseline.",
    )


class ComplianceCheckResult(BaseModel):
    """Response from POST /api/drift/check-compliance."""

    compliant: bool
    summary: str
    issues: list[dict[str, Any]] = Field(default_factory=list)
    blocking: bool = False
    drift_score: float = 0.0


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


def _load_baseline(repo_path: Path) -> ArchitectureState:
    """Load .gyrostate.yaml from the repository.  Raises HTTP 404 if missing."""
    import yaml
    from gyrocompass.config import get_state_path

    state_path = get_state_path(repo_path)
    if not state_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"No baseline found at {state_path}. "
                "Run 'gyro analyze --save' to create the baseline first."
            ),
        )
    with state_path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    try:
        return ArchitectureState.model_validate(raw)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Baseline file is malformed: {exc}",
        ) from exc


def _load_rules(repo_path: Path) -> Rules:
    """Load .gyrorules.yaml or return empty Rules if absent."""
    import yaml
    from gyrocompass.config import get_rules_path

    rules_path = get_rules_path(repo_path)
    if not rules_path.exists():
        return Rules()
    with rules_path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return Rules.model_validate(raw)


def _project_id_from_path(repo_path: Path, override: str | None) -> str:
    return override or repo_path.name


# ── Endpoints ──────────────────────────────────────────────────────────────────


@router.post(
    "/analyze",
    response_model=DriftReport,
    status_code=status.HTTP_200_OK,
    summary="Run architectural drift analysis",
)
async def analyze_drift(request: DriftAnalyzeRequest) -> DriftReport:
    """
    Compare the current state of a repository against its baseline.

    Steps:
    1. Resolve the repository path on the server filesystem.
    2. Load `.gyro/.gyrostate.yaml` as the baseline.
    3. Run a fresh `CodeIndexer` pass (full repo or changed files only).
    4. Run `DriftDetector` to produce a `DriftReport`.
    5. Cache the report under `project_id`.

    Returns the full `DriftReport` as JSON.
    """
    logger.info(
        "Drift analysis requested: repo={} files={} pr={}",
        request.repo_path,
        len(request.changed_files or []),
        request.pr_number,
    )

    repo_path = _resolve_repo(request.repo_path)
    baseline = _load_baseline(repo_path)
    rules = _load_rules(repo_path)
    project_id = _project_id_from_path(repo_path, request.project_id)

    # Index the repository or the changed files subset
    indexer = CodeIndexer(repo_path)
    try:
        if request.changed_files:
            logger.debug("Partial index: {} files", len(request.changed_files))
            current = indexer.index_files(request.changed_files)
        else:
            logger.debug("Full index of {}", repo_path)
            current = indexer.index()
    except Exception as exc:
        logger.exception("Indexing failed for {}: {}", repo_path, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Indexing failed: {exc}",
        ) from exc

    # Detect drift
    detector = DriftDetector(baseline=baseline, rules=rules)
    try:
        report = detector.detect(current, changed_files=request.changed_files)
    except Exception as exc:
        logger.exception("Drift detection failed: {}", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Drift detection failed: {exc}",
        ) from exc

    if request.pr_number:
        report.pr_number = request.pr_number

    # Cache the report
    _report_cache[project_id] = report
    logger.info(
        "Drift analysis complete: project={} score={:.2f} events={}",
        project_id,
        report.drift_score,
        len(report.events),
    )

    return report


@router.get(
    "/report/{project_id}",
    response_model=DriftReport,
    status_code=status.HTTP_200_OK,
    summary="Get the latest drift report for a project",
)
async def get_drift_report(project_id: str) -> DriftReport:
    """
    Retrieve the most recently generated drift report for a given project.

    The report is stored in-memory after `POST /api/drift/analyze`.
    In production, persist reports to a database via `api.database`.
    """
    report = _report_cache.get(project_id)
    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"No drift report found for project '{project_id}'. "
                "Run POST /api/drift/analyze first."
            ),
        )
    return report


@router.post(
    "/check-compliance",
    response_model=ComplianceCheckResult,
    status_code=status.HTTP_200_OK,
    summary="Lightweight compliance pre-flight check",
)
async def check_compliance(request: ComplianceCheckRequest) -> ComplianceCheckResult:
    """
    Run a fast compliance check for a proposed set of file changes.

    This endpoint does **not** require a full repository index.  Instead it:
    1. Loads the baseline (if `repo_path` is provided).
    2. Runs a partial `CodeIndexer` pass over just the supplied `files`.
    3. Evaluates `.gyrorules.yaml` invariants and principles against the partial state.
    4. Returns a `ComplianceCheckResult` indicating whether the change is compliant.

    Useful for pre-commit hooks, IDE integrations, and AI-agent guardrails.
    """
    logger.info(
        "Compliance check: files={} description={}",
        len(request.files),
        request.description[:80],
    )

    # Determine repo path
    if request.repo_path:
        repo_path = _resolve_repo(request.repo_path)
    else:
        repo_path = Path.cwd()

    # Try to load baseline and rules; degrade gracefully if not found
    try:
        baseline = _load_baseline(repo_path)
    except HTTPException:
        baseline = None

    rules = _load_rules(repo_path) if repo_path else Rules()

    # Partial index of the changed files
    indexer = CodeIndexer(repo_path)
    try:
        partial_state = indexer.index_files(request.files)
    except Exception as exc:
        logger.warning("Partial index failed for compliance check: {}", exc)
        partial_state = None

    issues: list[dict[str, Any]] = []
    drift_score: float = 0.0

    if baseline is not None and partial_state is not None:
        detector = DriftDetector(baseline=baseline, rules=rules)
        try:
            report = detector.detect(partial_state, changed_files=request.files)
            issues = [
                {
                    "severity": e.severity.value,
                    "type": e.type.value,
                    "title": e.title,
                    "description": e.description,
                    "file": e.file,
                    "line": e.line,
                    "suggested_fix": e.suggested_fix,
                }
                for e in report.events
            ]
            drift_score = report.drift_score
            blocking = report.has_blocking_issues
        except Exception as exc:
            logger.warning("Drift detection skipped in compliance check: {}", exc)
            blocking = False
    else:
        # Rules-only check — no baseline comparison
        if rules.invariants and partial_state is not None:
            from gyrocompass.rules import RulesEngine

            engine = RulesEngine(rules=rules, repo_path=repo_path)
            violations = engine.check_invariants(partial_state, changed_files=request.files)
            issues = [
                {
                    "severity": v.severity.value,
                    "type": v.rule_type,
                    "title": f"Rule violated: {v.rule_id}",
                    "description": v.description,
                    "file": v.file,
                    "line": v.line,
                    "suggested_fix": v.suggested_fix,
                }
                for v in violations
            ]
            blocking = any(
                i["severity"] in ("critical", "high") for i in issues
            )
        else:
            blocking = False

    compliant = not blocking
    critical = sum(1 for i in issues if i["severity"] == "critical")
    high = sum(1 for i in issues if i["severity"] == "high")
    medium = sum(1 for i in issues if i["severity"] == "medium")

    if not issues:
        summary = "No compliance issues found. Change appears architecturally safe."
    elif blocking:
        summary = (
            f"Compliance check FAILED — {len(issues)} issue(s) detected "
            f"({critical} critical, {high} high, {medium} medium). "
            "Resolve blocking issues before proceeding."
        )
    else:
        summary = (
            f"Compliance check passed with {len(issues)} advisory issue(s) "
            f"({critical} critical, {high} high, {medium} medium)."
        )

    logger.info(
        "Compliance check complete: compliant={} issues={} score={:.2f}",
        compliant,
        len(issues),
        drift_score,
    )

    return ComplianceCheckResult(
        compliant=compliant,
        summary=summary,
        issues=issues,
        blocking=blocking,
        drift_score=drift_score,
    )
