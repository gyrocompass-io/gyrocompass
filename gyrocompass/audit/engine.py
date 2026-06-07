"""Audit engine — orchestrates all ship-readiness scanners into one report."""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from gyrocompass.audit.scanners import ALL_SCANNERS, iter_source_files
from gyrocompass.models import AuditReport


class AuditEngine:
    """Runs the ship-readiness checklist over a repository."""

    def __init__(self, repo_path: Path | str | None = None) -> None:
        self.repo_path = Path(repo_path).resolve() if repo_path else Path.cwd()

    def run(self, only: list[str] | None = None, progress=None) -> AuditReport:
        """Run all scanners (or a subset by name). Returns an AuditReport.

        Args:
            only:     optional list of scanner names to run (keys of ALL_SCANNERS).
            progress: optional callback(name, idx, total) for UI.
        """
        scanners = ALL_SCANNERS if not only else {
            k: v for k, v in ALL_SCANNERS.items() if k in only
        }
        report = AuditReport(project=self.repo_path.name)
        report.files_scanned = len(iter_source_files(self.repo_path))

        total = len(scanners)
        for idx, (name, fn) in enumerate(scanners.items(), 1):
            if progress:
                progress(name, idx, total)
            try:
                findings = fn(self.repo_path)
                report.findings.extend(findings)
                logger.debug("audit scanner '{}' → {} finding(s)", name, len(findings))
            except Exception as exc:  # one bad scanner shouldn't kill the audit
                logger.warning("audit scanner '{}' failed: {}", name, exc)

        return report
