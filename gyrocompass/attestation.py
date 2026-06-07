"""Attestation management — create, persist, and verify commit attestations.

An attestation is a declaration (by an agent or human) of what a commit changes
and whether it complies with the project's rules. Its integrity comes from
binding to the exact staged diff via a SHA-256 hash: the pre-commit hook
recomputes that hash and rejects the commit if the attestation doesn't match
the actual staged changes.

Unlike advisory "guardrail" tooling that only suggests, GyroCompass enforces
this gate in a real pre-commit hook — a non-compliant or stale attestation
blocks the commit.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import yaml
from loguru import logger

from gyrocompass import gitutils
from gyrocompass.config import get_gyro_dir
from gyrocompass.models import (
    Attestation,
    ComplianceStatus,
    PrimitiveChange,
    RuleComplianceEntry,
)

# ── Paths ───────────────────────────────────────────────────────────────────

ATTESTATION_FILENAME = ".attestation.yaml"
ATTESTATION_ARCHIVE_DIR = "attestations"


def attestation_path(repo_path: Path | str | None = None) -> Path:
    """Working attestation file: .gyro/.attestation.yaml"""
    return get_gyro_dir(repo_path) / ATTESTATION_FILENAME


def archive_dir(repo_path: Path | str | None = None) -> Path:
    """Where finalized attestations are archived by commit hash."""
    return get_gyro_dir(repo_path) / ATTESTATION_ARCHIVE_DIR


# ── Verdict ─────────────────────────────────────────────────────────────────


class VerificationResult:
    """Outcome of verifying an attestation against the staged diff."""

    def __init__(
        self,
        valid: bool,
        reason: str,
        *,
        expected_hash: str | None = None,
        actual_hash: str | None = None,
        unresolved: list[str] | None = None,
    ) -> None:
        self.valid = valid
        self.reason = reason
        self.expected_hash = expected_hash
        self.actual_hash = actual_hash
        self.unresolved = unresolved or []

    def __bool__(self) -> bool:
        return self.valid


class AttestationManager:
    """Read/write/verify attestations for a repository."""

    def __init__(self, repo_path: Path | str | None = None) -> None:
        self.repo_path = Path(repo_path) if repo_path else Path.cwd()

    # ── Persistence ──────────────────────────────────────────────────────────

    def load(self) -> Attestation | None:
        """Load the working attestation, or None if absent/invalid."""
        path = attestation_path(self.repo_path)
        if not path.exists():
            return None
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            logger.warning(f"Attestation YAML parse error: {exc}")
            return None
        return self._from_yaml_dict(raw)

    def save(self, attestation: Attestation) -> Path:
        """Write the working attestation to .gyro/.attestation.yaml."""
        path = attestation_path(self.repo_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump(attestation.to_yaml_dict(), sort_keys=False, default_flow_style=False),
            encoding="utf-8",
        )
        logger.debug(f"Attestation written to {path}")
        return path

    def archive(self, attestation: Attestation, commit_sha: str) -> Path:
        """Archive a finalized attestation under attestations/<sha>.yaml (post-commit)."""
        adir = archive_dir(self.repo_path)
        adir.mkdir(parents=True, exist_ok=True)
        dest = adir / f"{commit_sha}.yaml"
        dest.write_text(
            yaml.safe_dump(attestation.to_yaml_dict(), sort_keys=False, default_flow_style=False),
            encoding="utf-8",
        )
        # Clear the working attestation so the next commit needs a fresh one
        wp = attestation_path(self.repo_path)
        if wp.exists():
            wp.unlink()
        logger.debug(f"Attestation archived to {dest}")
        return dest

    # ── Verification ─────────────────────────────────────────────────────────

    def verify(self, attestation: Attestation | None = None) -> VerificationResult:
        """Verify the attestation binds to the current staged diff and is complete.

        Checks, in order:
          1. An attestation exists.
          2. Its staged_diff_hash matches the actual staged diff (integrity).
          3. rules_checked is true and no applicable rule is left 'violated'
             or 'needs-review' without a note.
        """
        att = attestation if attestation is not None else self.load()
        if att is None:
            return VerificationResult(False, "No attestation found (.gyro/.attestation.yaml missing)")

        # ── Integrity: hash must match the real staged diff ──────────────────
        try:
            actual_hash = gitutils.staged_diff_hash(self.repo_path)
        except gitutils.GitError as exc:
            return VerificationResult(False, f"Could not compute staged diff hash: {exc}")

        if att.staged_diff_hash != actual_hash:
            return VerificationResult(
                False,
                "Attestation does not match staged changes — it is stale. "
                "Regenerate it after staging your final changes.",
                expected_hash=att.staged_diff_hash,
                actual_hash=actual_hash,
            )

        # ── Completeness: rules must be checked, no unresolved violations ─────
        if not att.rules_checked:
            return VerificationResult(False, "Attestation does not declare rules_checked: true")

        unresolved = [
            r.id
            for r in att.rules
            if r.status in (ComplianceStatus.violated, ComplianceStatus.needs_review)
            and not r.note
        ]
        if unresolved:
            return VerificationResult(
                False,
                "Attestation has unresolved rule findings (violated / needs-review without a note)",
                unresolved=unresolved,
            )

        return VerificationResult(True, "Attestation valid and bound to staged changes", actual_hash=actual_hash)

    # ── Construction ─────────────────────────────────────────────────────────

    def new_for_staged(
        self,
        *,
        agent: str = "unknown",
        summary: str = "",
        provenance_type: str = "ad-hoc",
        provenance_ref: str | None = None,
    ) -> Attestation:
        """Create a fresh attestation bound to the current staged diff."""
        diff_hash = gitutils.staged_diff_hash(self.repo_path)
        return Attestation(
            staged_diff_hash=diff_hash,
            agent=agent,
            summary=summary,
            provenance_type=provenance_type,
            provenance_ref=provenance_ref,
            timestamp=datetime.utcnow(),
        )

    # ── (De)serialization ──────────────────────────────────────────────────

    def _from_yaml_dict(self, raw: dict) -> Attestation:
        prov = raw.get("provenance") or {}
        rules_block = raw.get("rules") or {}
        changes = raw.get("changes") or {}

        def _prim(key: str) -> PrimitiveChange:
            blk = changes.get(key) or {}
            return PrimitiveChange(
                changed=bool(blk.get("changed", False)),
                details=[str(d) for d in (blk.get("details") or [])],
            )

        applicable = []
        for r in rules_block.get("applicable") or []:
            try:
                applicable.append(
                    RuleComplianceEntry(
                        id=r["id"],
                        status=ComplianceStatus(r["status"]),
                        note=r.get("note"),
                    )
                )
            except (KeyError, ValueError) as exc:
                logger.warning(f"Skipping malformed rule entry {r}: {exc}")

        ts = raw.get("timestamp")
        timestamp = datetime.fromisoformat(ts) if isinstance(ts, str) else datetime.utcnow()

        return Attestation(
            staged_diff_hash=raw.get("staged_diff_hash", ""),
            timestamp=timestamp,
            agent=raw.get("agent", "unknown"),
            agent_provider=raw.get("agent_provider"),
            agent_model=raw.get("agent_model"),
            agent_session_id=raw.get("agent_session_id"),
            provenance_type=prov.get("type", "ad-hoc"),
            provenance_ref=prov.get("ref"),
            rules_checked=bool(rules_block.get("checked", False)),
            rules=applicable,
            architecture=_prim("architecture"),
            data_model=_prim("data_model"),
            data_flows=_prim("data_flows"),
            rules_changes=_prim("rules"),
            capabilities=_prim("capabilities"),
            api_surface=_prim("api_surface"),
            external_dependencies=_prim("external_dependencies"),
            tech_stack=_prim("tech_stack"),
            summary=raw.get("summary", ""),
        )
