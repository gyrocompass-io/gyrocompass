"""Rules engine for GyroCompass.

Evaluates a Rules object (principles, invariants, ADRs) against a current
ArchitectureState and returns a list of RuleViolations.

Rule evidence types
-------------------
import_check  — Scans source files for a forbidden/required import statement.
code_pattern  — Searches source files using a regex pattern.
file_exists   — Asserts that a particular file path exists (or does not exist).
custom        — Reserved for future plugin-based checks; currently treated as
               a pattern check if a regex pattern is present, otherwise skipped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from loguru import logger

from gyrocompass.models import (
    ArchitectureState,
    DriftSeverity,
    Invariant,
    Principle,
    RuleEvidence,
    RuleStatus,
    Rules,
)

# ── RuleViolation ─────────────────────────────────────────────────────────────


@dataclass
class RuleViolation:
    """A single rule violation produced by the RulesEngine."""

    rule_id: str
    rule_type: str  # "principle" | "invariant" | "adr"
    severity: DriftSeverity
    description: str
    file: str | None = None
    line: int | None = None
    evidence: str | None = None
    suggested_fix: str | None = None


# ── Internal match result ─────────────────────────────────────────────────────


@dataclass
class _MatchLocation:
    """Records *where* a pattern was (or was not) found."""

    matched: bool
    file: str | None = None
    line: int | None = None
    snippet: str | None = None


# ── RulesEngine ───────────────────────────────────────────────────────────────


class RulesEngine:
    """Evaluate Rules against the current ArchitectureState.

    Args:
        rules:     The Rules object (principles, invariants, ADRs).
        repo_path: Absolute path to the repository root.  Used to resolve
                   file globs when scanning source code.
    """

    # File extensions that contain text source code worth scanning
    _SOURCE_EXTENSIONS: frozenset[str] = frozenset(
        {
            ".py", ".js", ".ts", ".tsx", ".jsx",
            ".go", ".rs", ".java", ".kt", ".rb",
            ".cs", ".cpp", ".c", ".h", ".hpp",
            ".swift", ".scala", ".php",
        }
    )

    # Directories to skip unconditionally
    _SKIP_DIRS: frozenset[str] = frozenset(
        {
            ".git", "__pycache__", ".venv", "venv", "node_modules",
            "dist", "build", ".next", "target", ".mypy_cache",
            ".ruff_cache", ".pytest_cache",
        }
    )

    # State primitives whose scope entries are element references, not file globs.
    _ELEMENT_SCOPE_PREFIXES = (
        "architecture.",
        "data_model.",
        "capabilities.",
        "surface_area.",
        "services.",
        "tech_stack.",
    )

    def __init__(self, rules: Rules, repo_path: Path) -> None:
        self.rules = rules
        self.repo_path = repo_path.resolve()
        self._gyromap = self._load_gyromap()
        logger.debug(
            "RulesEngine initialised — repo={} principles={} invariants={} adrs={} map_entries={}",
            self.repo_path,
            len(rules.principles),
            len(rules.invariants),
            len(rules.adrs),
            len(self._gyromap),
        )

    def _load_gyromap(self) -> dict[str, list[str]]:
        """Load the file map (.gyromap.yaml): {element_dotpath: [file, ...]}.

        This resolves element-style rule scopes (e.g. ``architecture.routes``)
        to the concrete files that implement them. Empty if no map exists.
        """
        import yaml

        from gyrocompass.config import get_map_path

        path = get_map_path(self.repo_path)
        if not path.exists():
            return {}
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            logger.warning("Could not parse .gyromap.yaml: {}", exc)
            return {}
        out: dict[str, list[str]] = {}
        for key, entries in raw.items():
            if key == "metadata" or not isinstance(entries, list):
                continue
            files = []
            for e in entries:
                if isinstance(e, dict) and e.get("file"):
                    files.append(e["file"])
                elif isinstance(e, str):
                    files.append(e)
            if files:
                out[key] = files
        return out

    # ── Public API ────────────────────────────────────────────────────────────

    def check_all(
        self,
        state: ArchitectureState,
        changed_files: list[str] | None = None,
    ) -> list[RuleViolation]:
        """Check all active rules. Returns a deduplicated list of violations."""
        violations: list[RuleViolation] = []
        violations.extend(self.check_principles(state))
        violations.extend(self.check_invariants(state, changed_files))
        # ADRs are informational only — flag as info-level if status is not accepted
        violations.extend(self._check_adrs(state))
        logger.info(
            "Rules check complete — {} violation(s) across {} principle(s), {} invariant(s), {} adr(s)",
            len(violations),
            len(self.rules.principles),
            len(self.rules.invariants),
            len(self.rules.adrs),
        )
        return violations

    def check_principles(self, state: ArchitectureState) -> list[RuleViolation]:
        """Evaluate each active Principle against the current state."""
        violations: list[RuleViolation] = []
        for rule_id, principle in self.rules.principles.items():
            if principle.status != RuleStatus.active:
                logger.debug("Skipping inactive principle: {}", rule_id)
                continue
            logger.debug("Checking principle: {}", rule_id)
            violations.extend(
                self._evaluate_evidence(
                    rule_id=rule_id,
                    rule_type="principle",
                    description=principle.description,
                    evidence_list=principle.evidence,
                    scope=principle.scope,
                    enforcement="warn",  # Principles warn; invariants block
                    state=state,
                )
            )
        return violations

    def check_invariants(
        self,
        state: ArchitectureState,
        changed_files: list[str] | None,
    ) -> list[RuleViolation]:
        """Evaluate each active Invariant. Scope is optionally restricted to
        changed files so only touched paths are scanned in CI.
        """
        violations: list[RuleViolation] = []
        for rule_id, invariant in self.rules.invariants.items():
            if invariant.status != RuleStatus.active:
                logger.debug("Skipping inactive invariant: {}", rule_id)
                continue
            logger.debug("Checking invariant: {}", rule_id)
            violations.extend(
                self._evaluate_evidence(
                    rule_id=rule_id,
                    rule_type="invariant",
                    description=invariant.description,
                    evidence_list=invariant.evidence,
                    scope=invariant.scope,
                    enforcement=invariant.enforcement,
                    state=state,
                    changed_files=changed_files,
                )
            )
        return violations

    # ── ADR checks ────────────────────────────────────────────────────────────

    def _check_adrs(self, state: ArchitectureState) -> list[RuleViolation]:
        """Flag ADRs that are not in 'accepted' status as info-level items."""
        violations: list[RuleViolation] = []
        for rule_id, adr in self.rules.adrs.items():
            if adr.status not in ("accepted", "superseded"):
                violations.append(
                    RuleViolation(
                        rule_id=rule_id,
                        rule_type="adr",
                        severity=DriftSeverity.info,
                        description=(
                            f"ADR '{rule_id}' ({adr.title}) has status '{adr.status}'. "
                            f"Proposed ADRs should be accepted or rejected."
                        ),
                        suggested_fix=(
                            f"Update the status of ADR '{rule_id}' to 'accepted' or 'superseded' "
                            f"in .gyrorules.yaml."
                        ),
                    )
                )
        return violations

    # ── Evidence evaluation ───────────────────────────────────────────────────

    def _evaluate_evidence(
        self,
        rule_id: str,
        rule_type: str,
        description: str,
        evidence_list: list[RuleEvidence],
        scope: list[str],
        enforcement: str,
        state: ArchitectureState,
        changed_files: list[str] | None = None,
    ) -> list[RuleViolation]:
        """Evaluate every evidence entry for a rule.

        Each evidence item is a *test* that must PASS for the rule to be
        satisfied.  Failure semantics:

        - ``import_check``  → violation if the forbidden import IS found, or
          if a required import is NOT found (determined by the ``type`` prefix
          in the pattern: ``!<pattern>`` means "must not be present").
        - ``code_pattern``  → violation if pattern IS matched (forbidden).
          Prefix ``!`` flips semantics to "must be present".
        - ``file_exists``   → violation if the file does NOT exist.
          Prefix ``!`` means "file must NOT exist".
        - ``custom``        → delegates to ``_check_pattern_rule`` if a
          pattern is present, otherwise skipped.
        """
        violations: list[RuleViolation] = []

        for evidence in evidence_list:
            ev_type = evidence.type.lower()

            if ev_type == "import_check":
                loc = self._check_import_rule(evidence, scope, changed_files)
            elif ev_type == "code_pattern":
                loc = self._check_pattern_rule(evidence, scope, changed_files)
            elif ev_type == "file_exists":
                loc = self._check_file_exists_rule(evidence)
            elif ev_type == "custom":
                # Best-effort: treat as pattern check if pattern looks like regex
                if evidence.pattern:
                    loc = self._check_pattern_rule(evidence, scope, changed_files)
                else:
                    logger.debug(
                        "Skipping custom evidence with no pattern for rule {}", rule_id
                    )
                    continue
            else:
                logger.warning(
                    "Unknown evidence type '{}' for rule {} — skipping", ev_type, rule_id
                )
                continue

            if not loc.matched:
                # The check did not detect a problem — rule satisfied for this evidence
                continue

            severity = _enforcement_to_severity(enforcement)
            snippet_desc = f" Found: \"{loc.snippet}\"" if loc.snippet else ""
            violations.append(
                RuleViolation(
                    rule_id=rule_id,
                    rule_type=rule_type,
                    severity=severity,
                    description=(
                        f"{rule_type.title()} '{rule_id}' violated: {description}.{snippet_desc}"
                    ),
                    file=loc.file,
                    line=loc.line,
                    evidence=(
                        f"[{ev_type}] pattern={evidence.pattern!r}"
                        + (f" file={evidence.file!r}" if evidence.file else "")
                    ),
                    suggested_fix=(
                        evidence.description
                        or f"Review '{rule_id}' in .gyrorules.yaml for remediation guidance."
                    ),
                )
            )
            logger.debug(
                "Rule violated: {} ({}) at {}:{}",
                rule_id,
                ev_type,
                loc.file or "<unknown>",
                loc.line or "?",
            )

        return violations

    # ── Check implementations ─────────────────────────────────────────────────

    def _check_import_rule(
        self,
        evidence: RuleEvidence,
        scope: list[str],
        changed_files: list[str] | None,
    ) -> _MatchLocation:
        """Scan source files for a forbidden (or required) import statement.

        Pattern semantics
        -----------------
        - ``<module>``   — The import is *forbidden*. Violation if found.
        - ``!<module>``  — The import is *required*. Violation if NOT found anywhere.
        """
        pattern = evidence.pattern.strip()
        negate = pattern.startswith("!")
        module = pattern.lstrip("!").strip()

        # Build a regex that matches "import <module>" or "from <module> import ..."
        import_re = re.compile(
            rf"(?:^|\s)(?:import\s+{re.escape(module)}|from\s+{re.escape(module)}\s+import)",
            re.MULTILINE,
        )

        files_to_scan = self._resolve_files(evidence.file, scope, changed_files)
        found_location: _MatchLocation | None = None

        for filepath in files_to_scan:
            content = _read_file_safe(filepath)
            if content is None:
                continue
            match = import_re.search(content)
            if match:
                line_no = content[: match.start()].count("\n") + 1
                snippet = content.splitlines()[line_no - 1].strip()
                found_location = _MatchLocation(
                    matched=True,
                    file=str(filepath.relative_to(self.repo_path)),
                    line=line_no,
                    snippet=snippet,
                )
                break  # First hit is enough for forbidden; for required we need any hit

        if negate:
            # Required import: violation if NOT found in any scanned file
            if found_location is None:
                return _MatchLocation(
                    matched=True,
                    file=None,
                    line=None,
                    snippet=f"Expected import of '{module}' not found in scanned files.",
                )
            return _MatchLocation(matched=False)
        else:
            # Forbidden import: violation if found
            if found_location is not None:
                return found_location
            return _MatchLocation(matched=False)

    def _check_pattern_rule(
        self,
        evidence: RuleEvidence,
        scope: list[str],
        changed_files: list[str] | None,
    ) -> _MatchLocation:
        """Search source files for a regex pattern.

        Pattern semantics
        -----------------
        - ``<regex>``    — Pattern is *forbidden*. Violation if matched.
        - ``!<regex>``   — Pattern is *required*. Violation if NOT matched in any file.
        """
        raw_pattern = evidence.pattern.strip()
        negate = raw_pattern.startswith("!")
        regex_str = raw_pattern.lstrip("!").strip()

        try:
            compiled = re.compile(regex_str, re.MULTILINE)
        except re.error as exc:
            logger.warning(
                "Invalid regex in evidence pattern {!r}: {}", regex_str, exc
            )
            return _MatchLocation(matched=False)

        files_to_scan = self._resolve_files(evidence.file, scope, changed_files)
        found_location: _MatchLocation | None = None

        for filepath in files_to_scan:
            content = _read_file_safe(filepath)
            if content is None:
                continue
            match = compiled.search(content)
            if match:
                line_no = content[: match.start()].count("\n") + 1
                snippet = content.splitlines()[line_no - 1].strip()
                found_location = _MatchLocation(
                    matched=True,
                    file=str(filepath.relative_to(self.repo_path)),
                    line=line_no,
                    snippet=snippet,
                )
                break

        if negate:
            # Required pattern: violation if NOT found
            if found_location is None:
                return _MatchLocation(
                    matched=True,
                    file=None,
                    line=None,
                    snippet=f"Required pattern {regex_str!r} not found in scanned files.",
                )
            return _MatchLocation(matched=False)
        else:
            if found_location is not None:
                return found_location
            return _MatchLocation(matched=False)

    def _check_file_exists_rule(self, evidence: RuleEvidence) -> _MatchLocation:
        """Assert that a file path exists (or does not exist).

        Pattern semantics
        -----------------
        - ``<path>``    — File *must* exist. Violation if absent.
        - ``!<path>``   — File *must not* exist. Violation if present.
        """
        raw = evidence.pattern.strip()
        negate = raw.startswith("!")
        rel_path = raw.lstrip("!").strip()

        # Prefer explicit evidence.file over pattern when both look like paths
        target_rel = evidence.file if evidence.file else rel_path
        target = (self.repo_path / target_rel).resolve()

        exists = target.exists()

        if negate:
            # Must NOT exist
            if exists:
                return _MatchLocation(
                    matched=True,
                    file=target_rel,
                    line=None,
                    snippet=f"File '{target_rel}' exists but should not.",
                )
            return _MatchLocation(matched=False)
        else:
            # Must exist
            if not exists:
                return _MatchLocation(
                    matched=True,
                    file=target_rel,
                    line=None,
                    snippet=f"Required file '{target_rel}' is missing.",
                )
            return _MatchLocation(matched=False)

    # ── File resolution helpers ───────────────────────────────────────────────

    def _resolve_files(
        self,
        evidence_file: str | None,
        scope: list[str],
        changed_files: list[str] | None,
    ) -> list[Path]:
        """Determine which files to scan for an evidence entry.

        Priority:
        1. If the evidence specifies an explicit file, scan only that.
        2. If changed_files are provided, intersect with scope globs.
        3. Otherwise walk the repo constrained by scope patterns.
        """
        if evidence_file:
            p = (self.repo_path / evidence_file).resolve()
            return [p] if p.exists() else []

        # Resolve element-style scopes (architecture.X) → files via the gyromap.
        scoped_files = self._files_from_element_scope(scope)
        if scoped_files is not None:
            if changed_files is not None:
                changed_abs = {(self.repo_path / c).resolve() for c in changed_files}
                intersected = [p for p in scoped_files if p in changed_abs]
                # If the change set doesn't touch scoped files, still scan the
                # scoped files (a forbidden pattern anywhere in scope is a violation).
                return intersected or scoped_files
            return scoped_files

        # Scope entries are file globs (or empty) — use the original behaviour.
        if changed_files is not None:
            return self._filter_changed_files(changed_files, scope)

        return list(self._walk_repo(scope))

    def _files_from_element_scope(self, scope: list[str]) -> list[Path] | None:
        """Map element-reference scopes to files via the gyromap.

        Returns None when the scope contains no element references (so the
        caller falls back to glob/repo-walk behaviour). Returns a (possibly
        empty) file list when the scope IS element-style — in that case we
        fail safe: an element scope with no map coverage scans the whole repo
        so a forbidden pattern is never silently missed.
        """
        element_scopes = [
            s for s in scope if any(s.startswith(p) for p in self._ELEMENT_SCOPE_PREFIXES)
        ]
        if not element_scopes:
            return None

        files: set[Path] = set()
        matched_any = False
        for es in element_scopes:
            for key, file_list in self._gyromap.items():
                # Match exact element or any sub-path (architecture.api-server.relationships.x)
                if key == es or key.startswith(es + "."):
                    matched_any = True
                    for f in file_list:
                        p = (self.repo_path / f).resolve()
                        if p.exists():
                            files.add(p)

        if not matched_any or not files:
            # Fail safe: element scope we can't resolve → scan whole repo.
            logger.debug(
                "Element scope {} unresolved via gyromap — scanning full repo (fail-safe)",
                element_scopes,
            )
            return list(self._walk_repo([]))
        return list(files)

    def _filter_changed_files(
        self, changed_files: list[str], scope: list[str]
    ) -> list[Path]:
        """Return absolute Paths for changed files that match scope and are source files."""
        results: list[Path] = []
        for cf in changed_files:
            p = (self.repo_path / cf).resolve()
            if not p.exists():
                continue
            if p.suffix not in self._SOURCE_EXTENSIONS:
                continue
            if scope and not _matches_any_scope(cf, scope):
                continue
            results.append(p)
        return results

    def _walk_repo(self, scope: list[str]) -> list[Path]:
        """Walk the repository, returning source files that match scope."""
        results: list[Path] = []
        for path in self.repo_path.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix not in self._SOURCE_EXTENSIONS:
                continue
            # Skip known non-source directories
            parts = path.relative_to(self.repo_path).parts
            if any(part in self._SKIP_DIRS for part in parts):
                continue
            rel = str(path.relative_to(self.repo_path))
            if scope and not _matches_any_scope(rel, scope):
                continue
            results.append(path)
        return results


# ── Module-level helpers ──────────────────────────────────────────────────────


def _read_file_safe(path: Path) -> str | None:
    """Read a text file, returning None on decode errors or I/O failures."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except (OSError, PermissionError) as exc:
        logger.debug("Cannot read {}: {}", path, exc)
        return None


def _matches_any_scope(rel_path: str, scope: list[str]) -> bool:
    """Return True if *rel_path* matches at least one scope glob/prefix pattern."""
    import fnmatch

    for pattern in scope:
        # Support both glob patterns and plain directory prefixes
        if fnmatch.fnmatch(rel_path, pattern):
            return True
        if rel_path.startswith(pattern.rstrip("/*")):
            return True
    return False


def _enforcement_to_severity(enforcement: str) -> DriftSeverity:
    """Map an enforcement level string to a DriftSeverity."""
    mapping: dict[str, DriftSeverity] = {
        "block": DriftSeverity.high,
        "warn": DriftSeverity.medium,
        "suggest": DriftSeverity.low,
        "info": DriftSeverity.info,
    }
    return mapping.get(enforcement.lower(), DriftSeverity.medium)
