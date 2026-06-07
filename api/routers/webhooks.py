"""GitHub webhook handler.

Listens for GitHub pull_request and push events, runs drift analysis, and
posts a formatted report back to the PR as a review comment.

Route
-----
POST /api/webhooks/github
"""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, status
from loguru import logger

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# ── Signature verification ─────────────────────────────────────────────────────

_SIGNATURE_HEADER = "x-hub-signature-256"


def _verify_signature(payload: bytes, signature_header: str | None, secret: str) -> None:
    """Verify the HMAC-SHA256 signature from GitHub.

    Raises HTTP 403 if the signature is absent or does not match.

    GitHub computes:
        HMAC-SHA256(secret, payload)
    and sends it as:
        X-Hub-Signature-256: sha256=<hex-digest>
    """
    if not signature_header:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing X-Hub-Signature-256 header.",
        )
    if not signature_header.startswith("sha256="):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="X-Hub-Signature-256 must start with 'sha256='.",
        )

    expected = hmac.new(
        secret.encode("utf-8"),
        msg=payload,
        digestmod=hashlib.sha256,
    ).hexdigest()

    received = signature_header.removeprefix("sha256=")

    if not hmac.compare_digest(expected, received):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="GitHub webhook signature mismatch. Verify GITHUB_WEBHOOK_SECRET.",
        )


# ── Helpers ────────────────────────────────────────────────────────────────────


def _get_webhook_secret() -> str | None:
    """Return the configured webhook secret or None."""
    try:
        from gyrocompass.config import settings

        return settings.GITHUB_WEBHOOK_SECRET
    except Exception:
        import os

        return os.environ.get("GITHUB_WEBHOOK_SECRET")


def _get_github_token() -> str | None:
    """Return the configured GitHub token or None."""
    try:
        from gyrocompass.config import settings

        return settings.GITHUB_TOKEN
    except Exception:
        import os

        return os.environ.get("GITHUB_TOKEN")


def _resolve_repo_for_installation(full_name: str) -> Path:
    """Resolve the local repository path for a GitHub repo full name.

    Strategy:
    1. Check ``GYRO_REPO_BASE_DIR`` env var — expected to contain directories
       named ``{owner}__{repo}`` or ``{repo}``.
    2. Fall back to CWD.
    """
    import os

    base_dir = os.environ.get("GYRO_REPO_BASE_DIR")
    if base_dir:
        base = Path(base_dir)
        # Try {owner}__{repo} then just {repo}
        owner, _, repo = full_name.partition("/")
        candidates = [
            base / f"{owner}__{repo}",
            base / repo,
            base / full_name.replace("/", "__"),
        ]
        for c in candidates:
            if c.is_dir():
                return c.resolve()

    return Path.cwd()


def _get_changed_files_from_pr(repo_full_name: str, pr_number: int, token: str) -> list[str]:
    """Retrieve the list of changed files for a GitHub PR."""
    try:
        import github  # PyGithub

        gh = github.Github(token)
        gh_repo = gh.get_repo(repo_full_name)
        pull = gh_repo.get_pull(pr_number)
        return [f.filename for f in pull.get_files()]
    except Exception as exc:
        logger.warning("Could not fetch PR files for {}/#{}: {}", repo_full_name, pr_number, exc)
        return []


def _post_pr_comment(
    repo_full_name: str,
    pr_number: int,
    body: str,
    token: str,
) -> None:
    """Post a comment on a GitHub PR, replacing any previous GyroCompass comment."""
    try:
        import github  # PyGithub

        gh = github.Github(token)
        gh_repo = gh.get_repo(repo_full_name)
        pull = gh_repo.get_pull(pr_number)

        # Delete previous GyroCompass comments to avoid duplicates
        _GYRO_MARKER = "<!-- gyrocompass-report -->"
        for existing in pull.get_issue_comments():
            if _GYRO_MARKER in (existing.body or ""):
                try:
                    existing.delete()
                except Exception:
                    pass

        pull.create_issue_comment(f"{_GYRO_MARKER}\n{body}")
        logger.info("Posted drift report to PR #{} of {}", pr_number, repo_full_name)
    except Exception as exc:
        logger.error(
            "Failed to post comment on PR #{} of {}: {}", pr_number, repo_full_name, exc
        )


def _run_drift_for_pr(
    repo_full_name: str,
    pr_number: int,
    branch: str,
    changed_files: list[str],
    token: str,
) -> None:
    """Background task: run drift analysis and post results to the PR."""
    from gyrocompass.drift import DriftDetector
    from gyrocompass.indexer import CodeIndexer
    from gyrocompass.models import Rules

    repo_path = _resolve_repo_for_installation(repo_full_name)
    logger.info(
        "Running drift for {}/#{} (branch={} files={}) at {}",
        repo_full_name,
        pr_number,
        branch,
        len(changed_files),
        repo_path,
    )

    # Load baseline
    try:
        from gyrocompass.config import get_state_path, get_rules_path

        state_path = get_state_path(repo_path)
        if not state_path.exists():
            msg = (
                "## GyroCompass — Drift Report\n\n"
                "**No baseline found** (`'.gyro/.gyrostate.yaml'` is missing).\n\n"
                "Run `gyro analyze --save` in the repository to initialise the baseline.\n\n"
                "_Powered by [GyroCompass](https://github.com/gyrocompass-io/gyrocompass)_"
            )
            _post_pr_comment(repo_full_name, pr_number, msg, token)
            return

        with state_path.open(encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
        from gyrocompass.models import ArchitectureState

        baseline = ArchitectureState.model_validate(raw)

        rules_path = get_rules_path(repo_path)
        rules = Rules()
        if rules_path.exists():
            with rules_path.open(encoding="utf-8") as fh:
                raw_rules = yaml.safe_load(fh) or {}
            rules = Rules.model_validate(raw_rules)

    except Exception as exc:
        logger.error("Failed to load baseline for {}: {}", repo_full_name, exc)
        _post_pr_comment(
            repo_full_name,
            pr_number,
            (
                "## GyroCompass — Drift Report\n\n"
                f"**Analysis failed:** {exc}\n\n"
                "_Powered by [GyroCompass](https://github.com/gyrocompass-io/gyrocompass)_"
            ),
            token,
        )
        return

    # Index changed files
    indexer = CodeIndexer(repo_path)
    try:
        current = indexer.index_files(changed_files) if changed_files else indexer.index()
    except Exception as exc:
        logger.error("Indexing failed for {}: {}", repo_full_name, exc)
        _post_pr_comment(
            repo_full_name,
            pr_number,
            (
                "## GyroCompass — Drift Report\n\n"
                f"**Indexing failed:** {exc}\n\n"
                "_Powered by [GyroCompass](https://github.com/gyrocompass-io/gyrocompass)_"
            ),
            token,
        )
        return

    # Detect drift
    detector = DriftDetector(baseline=baseline, rules=rules)
    report = detector.detect(current, changed_files=changed_files)
    report.pr_number = pr_number
    report.pr_branch = branch

    # Post markdown report
    markdown = report.to_markdown()
    _post_pr_comment(repo_full_name, pr_number, markdown, token)


def _update_cached_state_for_push(
    repo_full_name: str,
    after_sha: str,
    changed_files: list[str],
) -> None:
    """Background task: update cached architecture state after a push to the default branch."""
    repo_path = _resolve_repo_for_installation(repo_full_name)
    logger.info(
        "Updating cached state for {} after push (sha={} files={})",
        repo_full_name,
        after_sha,
        len(changed_files),
    )

    try:
        from gyrocompass.indexer import CodeIndexer
        from gyrocompass.config import get_state_path

        indexer = CodeIndexer(repo_path)
        if changed_files:
            state = indexer.index_files(changed_files)
        else:
            state = indexer.index()

        state_path = get_state_path(repo_path)
        if state_path.exists():
            # Merge partial update into existing baseline
            with state_path.open(encoding="utf-8") as fh:
                raw_baseline = yaml.safe_load(fh) or {}
            # Update commit SHA
            raw_baseline.setdefault("metadata", {})["commit_sha"] = after_sha
            # Merge newly indexed components into the architecture section
            partial = json.loads(state.model_dump_json())
            for comp_id, elem in partial.get("architecture", {}).items():
                raw_baseline.setdefault("architecture", {})[comp_id] = elem
            with state_path.open("w", encoding="utf-8") as fh:
                yaml.dump(
                    raw_baseline, fh,
                    default_flow_style=False,
                    allow_unicode=True,
                    sort_keys=False,
                )
            logger.info("Updated cached state for {} at {}", repo_full_name, state_path)
        else:
            logger.debug("No existing state file at {} — skipping cache update", state_path)

    except Exception as exc:
        logger.warning("Push state update failed for {}: {}", repo_full_name, exc)


# ── Endpoint ───────────────────────────────────────────────────────────────────


@router.post(
    "/github",
    status_code=status.HTTP_202_ACCEPTED,
    summary="GitHub webhook receiver",
)
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: str | None = Header(default=None, alias="x-hub-signature-256"),
    x_github_event: str | None = Header(default=None, alias="x-github-event"),
    x_github_delivery: str | None = Header(default=None, alias="x-github-delivery"),
) -> dict[str, str]:
    """
    Handle incoming GitHub webhook events.

    **Supported events:**

    - `pull_request` (opened, synchronize, reopened) — run drift analysis and
      post a comment with the report on the PR.
    - `push` — update the cached architecture state for the affected branch.
      Only processes pushes to the repository's default branch.

    **Security:** the request body is verified against the HMAC-SHA256
    signature in `X-Hub-Signature-256` using `GITHUB_WEBHOOK_SECRET`.

    Returns HTTP 202 Accepted immediately; all heavy work runs as background tasks.
    """
    # Read raw body for HMAC verification before parsing JSON
    body = await request.body()

    # Verify signature when a secret is configured
    secret = _get_webhook_secret()
    if secret:
        _verify_signature(body, x_hub_signature_256, secret)
    else:
        logger.warning(
            "GITHUB_WEBHOOK_SECRET not set — webhook signature verification is disabled!"
        )

    # Parse event type
    event_type = x_github_event or "unknown"
    delivery_id = x_github_delivery or "?"
    logger.info("GitHub webhook: event={} delivery={}", event_type, delivery_id)

    # Parse payload
    try:
        payload: dict[str, Any] = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid JSON payload: {exc}",
        ) from exc

    token = _get_github_token()

    # ── pull_request ──────────────────────────────────────────────────────────
    if event_type == "pull_request":
        action = payload.get("action", "")
        if action not in ("opened", "synchronize", "reopened"):
            return {"status": "ignored", "reason": f"action '{action}' not monitored"}

        if not token:
            logger.warning(
                "GITHUB_TOKEN not set — cannot post PR comment. Drift analysis skipped."
            )
            return {"status": "ignored", "reason": "GITHUB_TOKEN not configured"}

        pr = payload.get("pull_request", {})
        pr_number: int = pr.get("number", 0)
        branch: str = pr.get("head", {}).get("ref", "unknown")
        repo_full_name: str = payload.get("repository", {}).get("full_name", "")
        installation_token = token  # could be App token in production

        # Fetch changed files (returns [] on failure — full index will run)
        changed_files = _get_changed_files_from_pr(repo_full_name, pr_number, installation_token)

        background_tasks.add_task(
            _run_drift_for_pr,
            repo_full_name=repo_full_name,
            pr_number=pr_number,
            branch=branch,
            changed_files=changed_files,
            token=installation_token,
        )

        logger.info(
            "Enqueued drift analysis for {}/#{} ({} changed files)",
            repo_full_name,
            pr_number,
            len(changed_files),
        )
        return {"status": "accepted", "action": "drift_analysis_queued"}

    # ── push ──────────────────────────────────────────────────────────────────
    elif event_type == "push":
        repo_info = payload.get("repository", {})
        repo_full_name = repo_info.get("full_name", "")
        default_branch: str = repo_info.get("default_branch", "main")
        pushed_ref: str = payload.get("ref", "")

        # Only update cache for pushes to the default branch
        if pushed_ref != f"refs/heads/{default_branch}":
            return {
                "status": "ignored",
                "reason": f"push to '{pushed_ref}' is not the default branch",
            }

        after_sha: str = payload.get("after", "")
        commits: list[dict] = payload.get("commits", [])

        # Collect all unique changed/added files from every commit in the push
        touched: set[str] = set()
        for commit in commits:
            touched.update(commit.get("added", []))
            touched.update(commit.get("modified", []))
            touched.update(commit.get("removed", []))

        background_tasks.add_task(
            _update_cached_state_for_push,
            repo_full_name=repo_full_name,
            after_sha=after_sha,
            changed_files=list(touched),
        )

        logger.info(
            "Enqueued state update for {} after push {} ({} files)",
            repo_full_name,
            after_sha[:8],
            len(touched),
        )
        return {"status": "accepted", "action": "state_update_queued"}

    # ── ping (GitHub sends this on webhook registration) ──────────────────────
    elif event_type == "ping":
        zen: str = payload.get("zen", "")
        logger.info("GitHub ping received: {}", zen)
        return {"status": "pong", "zen": zen}

    else:
        logger.debug("Unhandled GitHub event type: {}", event_type)
        return {"status": "ignored", "reason": f"event '{event_type}' not handled"}
