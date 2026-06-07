"""Installs GyroCompass enforcement hooks into a repository.

Two integration points:

  1. Git pre-commit / post-commit hooks (.git/hooks/) — the non-bypassable
     gate. pre-commit runs `gyro check` and blocks the commit on failure;
     post-commit archives the attestation under .gyro/attestations/<sha>.yaml.

  2. Claude Code hooks (.claude/settings.json) — PreToolUse/PostToolUse entries
     that call `python -m gyrocompass.hooks.claude_hooks` so the agent gets
     architectural context (and protected-component blocks) inline.

Both are idempotent: re-running detects existing GyroCompass-managed hooks and
updates them in place without clobbering unrelated hook content.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

from loguru import logger

from gyrocompass import gitutils

# Marker used to detect/replace GyroCompass-managed blocks
_MARKER = "# >>> gyrocompass managed >>>"
_MARKER_END = "# <<< gyrocompass managed <<<"

# ── Git hook bodies ──────────────────────────────────────────────────────────
#
# The hook must reliably find the interpreter that actually has GyroCompass
# installed. Relying on bare `gyro`/`python` being on PATH is fragile: git
# hooks run with a minimal environment, virtualenvs aren't activated, and some
# systems only ship `python3`. So at install time we bake in `sys.executable`
# (the exact interpreter running the installer) as the primary runner, then
# fall back to an installed `gyro` console script, then `python3`/`python`.


def _build_hook(runner: str, command: str, comment: str) -> str:
    """Construct a POSIX-sh hook body with a baked-in interpreter + fallbacks."""
    return f"""#!/bin/sh
{_MARKER}
# {comment}
# Bypass once with:  git commit --no-verify
GYRO_PY="{runner}"
if [ -x "$GYRO_PY" ]; then
    "$GYRO_PY" -m gyrocompass.cli.main {command}
elif command -v gyro >/dev/null 2>&1; then
    gyro {command}
elif command -v python3 >/dev/null 2>&1; then
    python3 -m gyrocompass.cli.main {command}
elif command -v python >/dev/null 2>&1; then
    python -m gyrocompass.cli.main {command}
else
    echo "GyroCompass hook: no Python interpreter found; skipping check." >&2
    exit 0
fi
{_MARKER_END}
"""


def _post_commit_hook(runner: str) -> str:
    """Post-commit archives the attestation; failure must never block."""
    return f"""#!/bin/sh
{_MARKER}
# GyroCompass: archive the attestation for the commit just made (best-effort).
GYRO_PY="{runner}"
if [ -x "$GYRO_PY" ]; then
    "$GYRO_PY" -m gyrocompass.cli.main attest --archive >/dev/null 2>&1 || true
elif command -v gyro >/dev/null 2>&1; then
    gyro attest --archive >/dev/null 2>&1 || true
fi
{_MARKER_END}
"""


def install_git_hooks(repo_path: Path | str | None = None) -> list[Path]:
    """Install pre-commit and post-commit hooks. Returns paths written."""
    import sys

    repo = Path(repo_path) if repo_path else gitutils.repo_root()
    if not gitutils.is_git_repo(repo):
        raise gitutils.GitError(f"{repo} is not a git repository")

    hooks_dir = repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)

    # The exact interpreter running this installer — guaranteed to have
    # gyrocompass importable.
    runner = sys.executable or "python3"

    pre_commit = _build_hook(
        runner,
        "check --staged --repo .",
        "GyroCompass enforcement gate. Blocks commits that violate rules/drift.",
    )
    post_commit = _post_commit_hook(runner)

    written = []
    for name, body in (("pre-commit", pre_commit), ("post-commit", post_commit)):
        path = hooks_dir / name
        _write_or_merge_hook(path, body)
        path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        written.append(path)
        logger.debug(f"Installed git hook: {path} (runner={runner})")
    return written


def _write_or_merge_hook(path: Path, body: str) -> None:
    """Write a hook, preserving any non-GyroCompass content already present."""
    if not path.exists():
        path.write_text(body, encoding="utf-8")
        return

    existing = path.read_text(encoding="utf-8")
    if _MARKER in existing and _MARKER_END in existing:
        # Replace our managed block in place
        pre = existing.split(_MARKER)[0].rstrip("\n")
        post = existing.split(_MARKER_END)[1].lstrip("\n")
        managed = body.split(_MARKER, 1)[1].rsplit(_MARKER_END, 1)[0]
        new = f"{pre}\n{_MARKER}{managed}{_MARKER_END}\n{post}".strip() + "\n"
        path.write_text(new, encoding="utf-8")
    else:
        # Append our managed block (keep their script, ensure shebang once)
        sep = "" if existing.endswith("\n") else "\n"
        managed_block = body.split("#!/bin/sh", 1)[-1].lstrip("\n")
        path.write_text(f"{existing}{sep}{managed_block}", encoding="utf-8")


def uninstall_git_hooks(repo_path: Path | str | None = None) -> list[Path]:
    """Remove GyroCompass-managed blocks from git hooks. Returns touched paths."""
    repo = Path(repo_path) if repo_path else gitutils.repo_root()
    hooks_dir = repo / ".git" / "hooks"
    touched = []
    for name in ("pre-commit", "post-commit"):
        path = hooks_dir / name
        if not path.exists():
            continue
        content = path.read_text(encoding="utf-8")
        if _MARKER not in content:
            continue
        pre = content.split(_MARKER)[0]
        post = content.split(_MARKER_END)[1] if _MARKER_END in content else ""
        remainder = (pre + post).strip()
        if remainder in ("", "#!/bin/sh"):
            path.unlink()
        else:
            path.write_text(remainder + "\n", encoding="utf-8")
        touched.append(path)
    return touched


# ── Claude Code hooks ────────────────────────────────────────────────────────


def install_claude_hooks(repo_path: Path | str | None = None) -> Path:
    """Add PreToolUse/PostToolUse hooks to .claude/settings.json (idempotent)."""
    repo = Path(repo_path) if repo_path else gitutils.repo_root()
    claude_dir = repo / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    settings_path = claude_dir / "settings.json"

    settings = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("Existing .claude/settings.json is invalid JSON; backing up")
            settings_path.rename(settings_path.with_suffix(".json.bak"))
            settings = {}

    hooks = settings.setdefault("hooks", {})

    pre_cmd = "python -m gyrocompass.hooks.claude_hooks pre"
    post_cmd = "python -m gyrocompass.hooks.claude_hooks post"

    hooks["PreToolUse"] = _merge_hook_matcher(
        hooks.get("PreToolUse", []), "Edit|Write|MultiEdit|NotebookEdit", pre_cmd
    )
    hooks["PostToolUse"] = _merge_hook_matcher(
        hooks.get("PostToolUse", []), "Edit|Write|MultiEdit|NotebookEdit", post_cmd
    )

    settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    logger.debug(f"Installed Claude Code hooks into {settings_path}")
    return settings_path


def _merge_hook_matcher(existing: list, matcher: str, command: str) -> list:
    """Insert/update a hook entry for `matcher` without duplicating ours."""
    out = []
    for entry in existing:
        # Drop any prior gyrocompass entry for the same matcher
        if entry.get("matcher") == matcher and any(
            "gyrocompass.hooks" in h.get("command", "")
            for h in entry.get("hooks", [])
        ):
            continue
        out.append(entry)
    out.append(
        {
            "matcher": matcher,
            "hooks": [{"type": "command", "command": command}],
        }
    )
    return out


def install_all(repo_path: Path | str | None = None) -> dict[str, list[str]]:
    """Install both git and Claude Code hooks. Returns a summary dict."""
    git_paths = install_git_hooks(repo_path)
    claude_path = install_claude_hooks(repo_path)
    return {
        "git_hooks": [str(p) for p in git_paths],
        "claude_settings": [str(claude_path)],
    }
