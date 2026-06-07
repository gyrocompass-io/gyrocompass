"""Git helpers for GyroCompass enforcement.

Thin wrappers around git plumbing used by the attestation and enforcement
layers. Everything here is deterministic and side-effect free (read-only),
except where explicitly noted.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path


class GitError(RuntimeError):
    """Raised when a git command fails or the repo is not a git repo."""


def _run_git(args: list[str], cwd: Path | str | None = None) -> str:
    """Run a git command and return stdout. Raises GitError on failure."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError as exc:  # git not installed
        raise GitError("git executable not found on PATH") from exc
    except subprocess.CalledProcessError as exc:
        raise GitError(
            f"git {' '.join(args)} failed (exit {exc.returncode}): {exc.stderr.strip()}"
        ) from exc
    return result.stdout


def is_git_repo(path: Path | str | None = None) -> bool:
    """True if `path` (or CWD) is inside a git working tree."""
    try:
        out = _run_git(["rev-parse", "--is-inside-work-tree"], cwd=path)
    except GitError:
        return False
    return out.strip() == "true"


def repo_root(path: Path | str | None = None) -> Path:
    """Absolute path to the git repo root."""
    out = _run_git(["rev-parse", "--show-toplevel"], cwd=path)
    return Path(out.strip())


def current_sha(path: Path | str | None = None) -> str | None:
    """Current HEAD commit SHA, or None if there are no commits yet."""
    try:
        return _run_git(["rev-parse", "HEAD"], cwd=path).strip()
    except GitError:
        return None


def staged_diff(path: Path | str | None = None) -> str:
    """The full unified diff of staged changes (`git diff --staged`)."""
    return _run_git(["diff", "--staged"], cwd=path)


def diff_between(base: str, head: str, path: Path | str | None = None) -> str:
    """Unified diff between two refs (`git diff base head`)."""
    return _run_git(["diff", base, head], cwd=path)


def staged_files(path: Path | str | None = None) -> list[str]:
    """List of staged file paths (added/copied/modified/renamed), repo-relative."""
    out = _run_git(
        ["diff", "--staged", "--name-only", "--diff-filter=ACMR"], cwd=path
    )
    return [line.strip() for line in out.splitlines() if line.strip()]


def changed_files_between(
    base: str, head: str, path: Path | str | None = None
) -> list[str]:
    """List of files changed between two refs, repo-relative."""
    out = _run_git(["diff", "--name-only", "--diff-filter=ACMR", base, head], cwd=path)
    return [line.strip() for line in out.splitlines() if line.strip()]


def hash_diff(diff_text: str) -> str:
    """SHA-256 of a diff string — the canonical attestation binding hash.

    Mirrors `git diff --staged | shasum -a 256 | cut -d' ' -f1` so attestations
    written by agents (which may shell out to that pipeline) match what the
    hook computes in Python.
    """
    return hashlib.sha256(diff_text.encode("utf-8")).hexdigest()


def staged_diff_hash(path: Path | str | None = None) -> str:
    """Convenience: hash of the current staged diff."""
    return hash_diff(staged_diff(path))
