"""Bridge the deep graph engine into drift reports.

When `GRAPH_BACKEND=graph` and Memgraph is populated, this enriches a
DriftReport with call-graph blast-radius context: it annotates drift events
with how many downstream symbols a change affects, and escalates severity when
a change ripples far. This is what turns "a file changed" into "this change
affects 14 functions across 4 modules, including the public API."

Kept entirely separate from drift.py so the lite (NetworkX) path never imports
or pays for the graph stack. Callers invoke `enrich_drift()` opportunistically
and ignore failures — enrichment is additive, never required.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from gyrocompass.models import DriftReport, DriftSeverity


# Escalation thresholds: a change touching this many downstream symbols is
# higher-risk than its raw drift type suggests.
_ESCALATE_TO_HIGH = 10
_ESCALATE_TO_CRITICAL = 25


def enrich_drift(
    report: DriftReport,
    changed_files: list[str],
    repo_path: Path | str | None = None,
) -> DriftReport:
    """Annotate and (where warranted) escalate a DriftReport using blast radius.

    Returns the same report object (mutated). On any failure — graph backend
    unavailable, Memgraph unreachable, empty graph — returns the report
    unchanged. Enrichment is best-effort.
    """
    if not changed_files:
        return report

    try:
        from gyrocompass.config import settings
        from gyrocompass.graph.backend import GraphBackend, graph_status

        if settings.GRAPH_BACKEND != "graph":
            return report

        avail = graph_status(settings.MEMGRAPH_HOST, settings.MEMGRAPH_PORT)
        if not avail.ok:
            logger.debug("Graph backend unavailable for enrichment: {}", avail.reason)
            return report

        backend = GraphBackend(settings.MEMGRAPH_HOST, settings.MEMGRAPH_PORT)
        try:
            if backend.node_count() == 0:
                return report
            radius = backend.blast_radius(changed_files)
        finally:
            backend.close()
    except Exception as exc:  # never let enrichment break drift
        logger.debug("Drift enrichment skipped: {}", exc)
        return report

    affected = radius.total_affected
    if affected == 0:
        return report

    # Append a blast-radius summary line to the report
    radius_note = (
        f"Call-graph blast radius: {radius.summary()}. "
        f"Direct callers: {', '.join(radius.affected_callers[:5]) or 'none'}"
        + ("…" if len(radius.affected_callers) > 5 else "")
    )
    report.summary = (report.summary + "\n\n" + radius_note).strip()

    # Escalate severities of events located in the changed files
    changed_set = {Path(f).name for f in changed_files}
    escalated = 0
    for event in report.events:
        if event.file and Path(event.file).name in changed_set:
            new_sev = _escalated_severity(event.severity, affected)
            if new_sev != event.severity:
                event.severity = new_sev
                event.description += (
                    f" [Escalated: change affects {affected} downstream symbol(s) "
                    f"via the call graph.]"
                )
                escalated += 1

    if escalated:
        logger.info("Blast radius escalated {} drift event(s)", escalated)
        # Recompute drift score after escalation
        report.drift_score = _recalculate_score(report)

    return report


def _escalated_severity(current: DriftSeverity, affected: int) -> DriftSeverity:
    """Bump severity based on blast radius, never downgrade."""
    order = [
        DriftSeverity.info,
        DriftSeverity.low,
        DriftSeverity.medium,
        DriftSeverity.high,
        DriftSeverity.critical,
    ]
    target = current
    if affected >= _ESCALATE_TO_CRITICAL:
        target = DriftSeverity.critical
    elif affected >= _ESCALATE_TO_HIGH:
        target = DriftSeverity.high
    # Never downgrade
    return target if order.index(target) > order.index(current) else current


def _recalculate_score(report: DriftReport) -> float:
    """Mirror DriftDetector's weighting after severity escalation."""
    weights = {
        DriftSeverity.critical: 0.4,
        DriftSeverity.high: 0.2,
        DriftSeverity.medium: 0.05,
        DriftSeverity.low: 0.01,
        DriftSeverity.info: 0.01,
    }
    score = sum(weights.get(e.severity, 0.0) for e in report.events)
    return min(1.0, round(score, 3))
