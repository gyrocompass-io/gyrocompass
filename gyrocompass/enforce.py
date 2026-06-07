"""Enforcement engine — the non-bypassable gate.

Combines three checks into a single block/warn/pass decision:
  1. rules        — invariant & principle violations (RulesEngine)
  2. drift        — architectural drift above configured severities
  3. attestation  — a valid attestation bound to the staged diff (optional)

Used by the pre-commit hook (`gyro check`) and the CI drift action. Each check
honours an EnforcementMode (block | warn | off) from the project's
EnforcementConfig, so teams dial strictness up over time without code changes.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from gyrocompass.attestation import AttestationManager
from gyrocompass.config import get_rules_path, get_state_path
from gyrocompass.models import (
    ArchitectureState,
    DriftReport,
    DriftSeverity,
    EnforcementConfig,
    EnforcementMode,
    GateCheck,
    GateResult,
    GateStatus,
    Rules,
)


class EnforcementEngine:
    """Runs all configured gates against a set of staged/changed files."""

    def __init__(
        self,
        repo_path: Path | str | None = None,
        config: EnforcementConfig | None = None,
    ) -> None:
        self.repo_path = Path(repo_path) if repo_path else Path.cwd()
        self.config = config or self._load_config()

    # ── Config loading ───────────────────────────────────────────────────────

    def _load_config(self) -> EnforcementConfig:
        """Load enforcement config from .gyro/config.yaml, else defaults."""
        import yaml

        from gyrocompass.config import GYROCONFIG_FILE

        cfg_path = self.repo_path / GYROCONFIG_FILE
        if cfg_path.exists():
            try:
                raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
                enforcement = raw.get("enforcement")
                if enforcement:
                    return EnforcementConfig.model_validate(enforcement)
            except Exception as exc:  # malformed config shouldn't crash the hook
                logger.warning(f"Could not parse enforcement config: {exc}")
        return EnforcementConfig()

    # ── Loading state / rules ────────────────────────────────────────────────

    def _load_state(self) -> ArchitectureState | None:
        import yaml

        path = get_state_path(self.repo_path)
        if not path.exists():
            return None
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            return ArchitectureState.model_validate(raw)
        except Exception as exc:
            logger.warning(f"Could not load architecture state: {exc}")
            return None

    def _load_rules(self) -> Rules:
        import yaml

        path = get_rules_path(self.repo_path)
        if not path.exists():
            return Rules()
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            return Rules.model_validate(raw)
        except Exception as exc:
            logger.warning(f"Could not load rules: {exc}")
            return Rules()

    # ── Public API ─────────────────────────────────────────────────────────

    def check(self, changed_files: list[str] | None = None) -> GateResult:
        """Run all gates. `changed_files` defaults to the staged file set."""
        if changed_files is None:
            changed_files = self._staged_files()

        result = GateResult()

        rules_check = self._gate_rules(changed_files)
        if rules_check:
            result.checks.append(rules_check)

        drift_check = self._gate_drift(changed_files)
        if drift_check:
            result.checks.append(drift_check)

        att_check = self._gate_attestation()
        if att_check:
            result.checks.append(att_check)

        result.blocked = any(c.status == GateStatus.blocked for c in result.checks)
        return result

    # ── Individual gates ─────────────────────────────────────────────────────

    def _gate_rules(self, changed_files: list[str]) -> GateCheck | None:
        if self.config.rules_mode == EnforcementMode.off:
            return None

        state = self._load_state()
        rules = self._load_rules()
        if state is None or (not rules.principles and not rules.invariants):
            return GateCheck(
                name="rules",
                status=GateStatus.pass_,
                mode=self.config.rules_mode,
                message="no rules to enforce",
            )

        from gyrocompass.rules import RulesEngine

        engine = RulesEngine(rules, self.repo_path)
        violations = engine.check_all(state, changed_files)

        # Only block on invariant violations; principles warn.
        blocking = [v for v in violations if v.rule_type == "invariant"]
        warning = [v for v in violations if v.rule_type != "invariant"]

        if not violations:
            return GateCheck(
                name="rules",
                status=GateStatus.pass_,
                mode=self.config.rules_mode,
                message="all rules satisfied",
            )

        details = [
            f"[{v.rule_type}] {v.rule_id}: {v.description}"
            + (f" ({v.file}:{v.line})" if v.file else "")
            for v in violations
        ]

        if blocking and self.config.rules_mode == EnforcementMode.block:
            return GateCheck(
                name="rules",
                status=GateStatus.blocked,
                mode=self.config.rules_mode,
                message=f"{len(blocking)} invariant violation(s), {len(warning)} warning(s)",
                details=details,
            )
        return GateCheck(
            name="rules",
            status=GateStatus.warned,
            mode=self.config.rules_mode,
            message=f"{len(violations)} rule finding(s)",
            details=details,
        )

    def _gate_drift(self, changed_files: list[str]) -> GateCheck | None:
        if self.config.drift_mode == EnforcementMode.off:
            return None

        baseline = self._load_state()
        if baseline is None:
            return GateCheck(
                name="drift",
                status=GateStatus.pass_,
                mode=self.config.drift_mode,
                message="no baseline state — run `gyro analyze --save`",
            )

        try:
            from gyrocompass.drift import DriftDetector
            from gyrocompass.indexer import CodeIndexer

            # Always index the FULL working tree for the "current" state — a
            # partial index of only changed files would make every unchanged
            # component look "removed" (false 100% drift). changed_files is
            # passed to the detector only to scope the rule check.
            indexer = CodeIndexer(self.repo_path)
            current = indexer.index()
            rules = self._load_rules()
            report: DriftReport = DriftDetector(baseline, rules).detect(current, changed_files)
        except Exception as exc:
            logger.warning(f"Drift detection failed: {exc}")
            return GateCheck(
                name="drift",
                status=GateStatus.warned,
                mode=self.config.drift_mode,
                message=f"drift check could not run: {exc}",
            )

        blocking_events = [
            e for e in report.events if e.severity in self.config.block_on_severities
        ]
        score_exceeded = report.drift_score > self.config.max_drift_score

        details = [f"[{e.severity.value}] {e.title}" for e in report.events[:10]]

        if not report.events:
            return GateCheck(
                name="drift",
                status=GateStatus.pass_,
                mode=self.config.drift_mode,
                message=f"no drift (score {report.drift_score:.0%})",
            )

        if (blocking_events or score_exceeded) and self.config.drift_mode == EnforcementMode.block:
            reason = []
            if blocking_events:
                reason.append(f"{len(blocking_events)} blocking event(s)")
            if score_exceeded:
                reason.append(f"score {report.drift_score:.0%} > {self.config.max_drift_score:.0%}")
            return GateCheck(
                name="drift",
                status=GateStatus.blocked,
                mode=self.config.drift_mode,
                message="; ".join(reason),
                details=details,
            )
        return GateCheck(
            name="drift",
            status=GateStatus.warned,
            mode=self.config.drift_mode,
            message=f"{len(report.events)} drift event(s), score {report.drift_score:.0%}",
            details=details,
        )

    def _gate_attestation(self) -> GateCheck | None:
        if not self.config.require_attestation or self.config.attestation_mode == EnforcementMode.off:
            return None

        mgr = AttestationManager(self.repo_path)
        verdict = mgr.verify()

        if verdict.valid:
            return GateCheck(
                name="attestation",
                status=GateStatus.pass_,
                mode=self.config.attestation_mode,
                message="valid and bound to staged changes",
            )

        details = []
        if verdict.expected_hash and verdict.actual_hash:
            details.append(f"expected {verdict.expected_hash[:12]}…, staged is {verdict.actual_hash[:12]}…")
        if verdict.unresolved:
            details.append("unresolved rules: " + ", ".join(verdict.unresolved))

        status = (
            GateStatus.blocked
            if self.config.attestation_mode == EnforcementMode.block
            else GateStatus.warned
        )
        return GateCheck(
            name="attestation",
            status=status,
            mode=self.config.attestation_mode,
            message=verdict.reason,
            details=details,
        )

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _staged_files(self) -> list[str]:
        from gyrocompass import gitutils

        try:
            return gitutils.staged_files(self.repo_path)
        except gitutils.GitError:
            return []
