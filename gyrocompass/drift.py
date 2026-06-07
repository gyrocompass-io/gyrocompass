"""Drift detection engine for GyroCompass.

Compares a baseline ArchitectureState (loaded from .gyro/.gyrostate.yaml)
against a current ArchitectureState (freshly indexed from code) and
produces a DriftReport describing every detected deviation.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from loguru import logger

from gyrocompass.models import (
    ArchitectureElement,
    ArchitectureState,
    DriftEvent,
    DriftReport,
    DriftSeverity,
    DriftType,
    ElementStatus,
    Invariant,
    Principle,
    Rules,
)

# ── Severity weight table ─────────────────────────────────────────────────────

_SEVERITY_WEIGHTS: dict[DriftSeverity, float] = {
    DriftSeverity.critical: 0.4,
    DriftSeverity.high: 0.2,
    DriftSeverity.medium: 0.05,
    DriftSeverity.low: 0.01,
    DriftSeverity.info: 0.01,
}


# ── Helpers ───────────────────────────────────────────────────────────────────


def _event_id() -> str:
    return str(uuid.uuid4())


def _fmt_attrs(attrs: list[Any]) -> str:
    return ", ".join(str(a) for a in attrs) if attrs else "(none)"


# ── DriftDetector ─────────────────────────────────────────────────────────────


class DriftDetector:
    """Compare a baseline and current ArchitectureState and emit a DriftReport.

    Args:
        baseline: The frozen architecture snapshot stored in .gyrostate.yaml.
        rules:    Optional Rules object (principles + invariants). When omitted
                  only structural drift is detected.
    """

    def __init__(self, baseline: ArchitectureState, rules: Rules | None = None) -> None:
        self.baseline = baseline
        self.rules = rules or Rules()

    # ── Public API ────────────────────────────────────────────────────────────

    def detect(
        self,
        current: ArchitectureState,
        changed_files: list[str] | None = None,
    ) -> DriftReport:
        """Compare baseline vs current. Returns a full DriftReport.

        Args:
            current:       Freshly indexed ArchitectureState.
            changed_files: Paths of files that changed in the current PR / commit.
                           Used to restrict rule-check scope.

        Returns:
            A populated DriftReport with zero or more DriftEvents.
        """
        logger.info(
            "Starting drift detection — baseline serial={} vs current serial={}",
            self.baseline.metadata.serial,
            current.metadata.serial,
        )

        files = changed_files or []
        events: list[DriftEvent] = []

        events.extend(self._compare_architecture(self.baseline, current))
        events.extend(self._compare_capabilities(self.baseline, current))
        events.extend(self._compare_api_surface(self.baseline, current))
        events.extend(self._compare_tech_stack(self.baseline, current))
        events.extend(self._compare_data_model(self.baseline, current))
        events.extend(self._check_rules(current, files))

        score = self._calculate_drift_score(events)
        summary = self._generate_summary(events, score)

        report = DriftReport(
            project=current.metadata.project,
            commit_sha=current.metadata.commit_sha,
            events=events,
            drift_score=score,
            summary=summary,
            generated_at=datetime.utcnow(),
        )

        logger.info(
            "Drift detection complete — score={:.2f} events={} (critical={} high={} medium={} low={})",
            score,
            len(events),
            report.critical_count,
            report.high_count,
            report.medium_count,
            report.low_count,
        )
        return report

    # ── Architecture comparison ───────────────────────────────────────────────

    def _compare_architecture(
        self,
        baseline: ArchitectureState,
        current: ArchitectureState,
    ) -> list[DriftEvent]:
        """Detect added / removed / modified components and relationships."""
        events: list[DriftEvent] = []

        baseline_keys = set(baseline.architecture)
        current_keys = set(current.architecture)

        # Removed components
        for name in baseline_keys - current_keys:
            elem = baseline.architecture[name]
            # Skip elements already marked as removed in baseline
            if elem.status == ElementStatus.removed:
                continue
            dependents = _find_dependents(name, baseline.architecture)
            severity = DriftSeverity.critical if dependents else DriftSeverity.high
            fix = (
                f"Restore '{name}' or update its dependents "
                f"({', '.join(dependents)}) to remove the dependency."
                if dependents
                else f"Restore '{name}' or document its removal in .gyrostate.yaml."
            )
            events.append(
                DriftEvent(
                    id=_event_id(),
                    type=DriftType.component_removed,
                    severity=severity,
                    title=f"Component removed: {name}",
                    description=(
                        f"Architecture element '{name}' ({elem.type.value}) was present in the "
                        f"baseline but is missing from the current state."
                        + (
                            f" It has {len(dependents)} dependents: {', '.join(dependents)}."
                            if dependents
                            else ""
                        )
                    ),
                    element=name,
                    suggested_fix=fix,
                )
            )
            logger.debug("component_removed: {} (severity={})", name, severity.value)

        # Added components (undocumented)
        for name in current_keys - baseline_keys:
            elem = current.architecture[name]
            events.append(
                DriftEvent(
                    id=_event_id(),
                    type=DriftType.component_added,
                    severity=DriftSeverity.medium,
                    title=f"Undocumented component added: {name}",
                    description=(
                        f"Architecture element '{name}' ({elem.type.value}) exists in the current "
                        f"codebase but is not described in the baseline architecture."
                    ),
                    element=name,
                    suggested_fix=(
                        f"Add '{name}' to .gyrostate.yaml under the 'architecture' section "
                        f"with a description, type, and relationships."
                    ),
                )
            )
            logger.debug("component_added: {}", name)

        # Modified components — detect relationship changes
        for name in baseline_keys & current_keys:
            b_elem = baseline.architecture[name]
            c_elem = current.architecture[name]
            events.extend(self._diff_relationships(name, b_elem, c_elem))

        return events

    def _diff_relationships(
        self,
        element_name: str,
        baseline_elem: ArchitectureElement,
        current_elem: ArchitectureElement,
    ) -> list[DriftEvent]:
        events: list[DriftEvent] = []
        b_rels = set(baseline_elem.relationships)
        c_rels = set(current_elem.relationships)

        for rel_target in b_rels - c_rels:
            rel = baseline_elem.relationships[rel_target]
            events.append(
                DriftEvent(
                    id=_event_id(),
                    type=DriftType.relationship_removed,
                    severity=DriftSeverity.high,
                    title=f"Relationship removed: {element_name} → {rel_target}",
                    description=(
                        f"The {rel.type.value} relationship from '{element_name}' to '{rel_target}' "
                        f"(protocol: {rel.protocol or 'unspecified'}) was present in the baseline "
                        f"but is absent in the current state."
                    ),
                    element=element_name,
                    suggested_fix=(
                        f"If the dependency was intentionally removed, update the baseline "
                        f"by deleting the '{rel_target}' entry under '{element_name}.relationships'. "
                        f"Otherwise restore the connection."
                    ),
                )
            )
            logger.debug("relationship_removed: {} → {}", element_name, rel_target)

        for rel_target in c_rels - b_rels:
            rel = current_elem.relationships[rel_target]
            events.append(
                DriftEvent(
                    id=_event_id(),
                    type=DriftType.relationship_added,
                    severity=DriftSeverity.medium,
                    title=f"Undocumented relationship added: {element_name} → {rel_target}",
                    description=(
                        f"A new {rel.type.value} relationship from '{element_name}' to '{rel_target}' "
                        f"was detected in the current state but is absent from the baseline."
                    ),
                    element=element_name,
                    suggested_fix=(
                        f"Document the new relationship in .gyrostate.yaml under "
                        f"'{element_name}.relationships.{rel_target}' or remove the dependency."
                    ),
                )
            )
            logger.debug("relationship_added: {} → {}", element_name, rel_target)

        return events

    # ── Capabilities ──────────────────────────────────────────────────────────

    def _compare_capabilities(
        self,
        baseline: ArchitectureState,
        current: ArchitectureState,
    ) -> list[DriftEvent]:
        """Detect capability regressions (capability removed or acceptance criteria violated)."""
        events: list[DriftEvent] = []

        b_caps = baseline.capabilities
        c_caps = current.capabilities

        for cap_name, b_cap in b_caps.items():
            # Skip non-active baseline capabilities
            if b_cap.status != "active":
                continue

            if cap_name not in c_caps:
                events.append(
                    DriftEvent(
                        id=_event_id(),
                        type=DriftType.capability_regression,
                        severity=DriftSeverity.critical,
                        title=f"Capability removed: {cap_name}",
                        description=(
                            f"Capability '{cap_name}' is documented in the baseline as active "
                            f"but is entirely absent from the current state."
                        ),
                        element=cap_name,
                        suggested_fix=(
                            f"Restore the capability '{cap_name}' or mark it as 'deprecated' "
                            f"in .gyrostate.yaml if intentionally removed."
                        ),
                    )
                )
                logger.debug("capability_removed (regression): {}", cap_name)
                continue

            c_cap = c_caps[cap_name]

            # Acceptance criteria regression: criteria present in baseline but missing in current
            b_criteria = set(b_cap.acceptance_criteria)
            c_criteria = set(c_cap.acceptance_criteria)
            missing_criteria = b_criteria - c_criteria
            for criterion in missing_criteria:
                events.append(
                    DriftEvent(
                        id=_event_id(),
                        type=DriftType.capability_regression,
                        severity=DriftSeverity.critical,
                        title=f"Acceptance criterion removed from capability: {cap_name}",
                        description=(
                            f"Capability '{cap_name}' is missing an acceptance criterion that was "
                            f"present in the baseline: \"{criterion}\""
                        ),
                        element=cap_name,
                        suggested_fix=(
                            f"Restore the acceptance criterion for '{cap_name}' or update the "
                            f"baseline to reflect the intentional scope reduction."
                        ),
                    )
                )
                logger.debug(
                    "capability_regression (criterion dropped): {} — {}",
                    cap_name,
                    criterion,
                )

            # Status regression: active → deprecated/removed
            if b_cap.status == "active" and c_cap.status in ("deprecated", "removed"):
                events.append(
                    DriftEvent(
                        id=_event_id(),
                        type=DriftType.capability_regression,
                        severity=DriftSeverity.critical,
                        title=f"Capability status regressed: {cap_name}",
                        description=(
                            f"Capability '{cap_name}' was 'active' in the baseline but is "
                            f"now '{c_cap.status}' in the current state."
                        ),
                        element=cap_name,
                        suggested_fix=(
                            f"Update the baseline to reflect the deprecation, or restore "
                            f"'{cap_name}' to active status."
                        ),
                    )
                )
                logger.debug("capability_status_regressed: {}", cap_name)

        return events

    # ── API surface ───────────────────────────────────────────────────────────

    def _compare_api_surface(
        self,
        baseline: ArchitectureState,
        current: ArchitectureState,
    ) -> list[DriftEvent]:
        """Detect API endpoint additions / removals / modifications."""
        events: list[DriftEvent] = []

        b_surface = baseline.surface_area
        c_surface = current.surface_area

        # Removed endpoints
        for endpoint, b_ep in b_surface.items():
            if endpoint not in c_surface:
                events.append(
                    DriftEvent(
                        id=_event_id(),
                        type=DriftType.api_surface_change,
                        severity=DriftSeverity.high,
                        title=f"API endpoint removed: {endpoint}",
                        description=(
                            f"API endpoint '{endpoint}' ({b_ep.method or 'unknown method'} — "
                            f"{b_ep.summary}) was documented in the baseline but is absent "
                            f"from the current state."
                        ),
                        element=endpoint,
                        suggested_fix=(
                            f"Restore the endpoint '{endpoint}' or mark it as deprecated in "
                            f".gyrostate.yaml and communicate the breaking change."
                        ),
                    )
                )
                logger.debug("api_endpoint_removed: {}", endpoint)

        # Added endpoints (undocumented)
        for endpoint, c_ep in c_surface.items():
            if endpoint not in b_surface:
                events.append(
                    DriftEvent(
                        id=_event_id(),
                        type=DriftType.api_surface_change,
                        severity=DriftSeverity.low,
                        title=f"Undocumented API endpoint added: {endpoint}",
                        description=(
                            f"API endpoint '{endpoint}' ({c_ep.method or 'unknown method'} — "
                            f"{c_ep.summary}) exists in the current state but is absent "
                            f"from the baseline."
                        ),
                        element=endpoint,
                        suggested_fix=(
                            f"Document endpoint '{endpoint}' in .gyrostate.yaml under "
                            f"'surface_area' with auth requirements and a summary."
                        ),
                    )
                )
                logger.debug("api_endpoint_added (undocumented): {}", endpoint)

        # Modified endpoints — auth regression is HIGH, other changes are MEDIUM
        for endpoint in b_surface.keys() & c_surface.keys():
            b_ep = b_surface[endpoint]
            c_ep = c_surface[endpoint]

            if b_ep.auth_required and not c_ep.auth_required:
                events.append(
                    DriftEvent(
                        id=_event_id(),
                        type=DriftType.api_surface_change,
                        severity=DriftSeverity.high,
                        title=f"Auth removed from API endpoint: {endpoint}",
                        description=(
                            f"Endpoint '{endpoint}' required authentication in the baseline "
                            f"but no longer does in the current state. This is a security regression."
                        ),
                        element=endpoint,
                        suggested_fix=(
                            f"Restore authentication on '{endpoint}' or update the baseline "
                            f"with an ADR justifying the change."
                        ),
                    )
                )
                logger.debug("api_auth_removed: {}", endpoint)

            elif b_ep.method != c_ep.method and b_ep.method is not None and c_ep.method is not None:
                events.append(
                    DriftEvent(
                        id=_event_id(),
                        type=DriftType.api_surface_change,
                        severity=DriftSeverity.medium,
                        title=f"API endpoint method changed: {endpoint}",
                        description=(
                            f"Endpoint '{endpoint}' changed HTTP method from "
                            f"'{b_ep.method}' to '{c_ep.method}'."
                        ),
                        element=endpoint,
                        suggested_fix=(
                            f"Update the baseline for '{endpoint}' if the change is intentional, "
                            f"otherwise revert the method change."
                        ),
                    )
                )
                logger.debug(
                    "api_method_changed: {} {} → {}", endpoint, b_ep.method, c_ep.method
                )

        return events

    # ── Tech stack ────────────────────────────────────────────────────────────

    def _compare_tech_stack(
        self,
        baseline: ArchitectureState,
        current: ArchitectureState,
    ) -> list[DriftEvent]:
        """Detect unauthorized tech stack additions."""
        events: list[DriftEvent] = []

        b_stack = baseline.tech_stack
        c_stack = current.tech_stack

        for tech_name in set(c_stack) - set(b_stack):
            c_item = c_stack[tech_name]
            events.append(
                DriftEvent(
                    id=_event_id(),
                    type=DriftType.tech_stack_change,
                    severity=DriftSeverity.medium,
                    title=f"Unauthorized tech stack addition: {tech_name}",
                    description=(
                        f"Technology '{tech_name}' (type: {c_item.type}, "
                        f"vendor: {c_item.vendor or 'unknown'}) was introduced without "
                        f"being documented in the baseline tech stack."
                    ),
                    element=tech_name,
                    suggested_fix=(
                        f"Add '{tech_name}' to .gyrostate.yaml under 'tech_stack' with a "
                        f"justification, or remove the dependency if it was introduced accidentally."
                    ),
                )
            )
            logger.debug("tech_stack_addition (unauthorized): {}", tech_name)

        for tech_name in set(b_stack) - set(c_stack):
            b_item = b_stack[tech_name]
            events.append(
                DriftEvent(
                    id=_event_id(),
                    type=DriftType.tech_stack_change,
                    severity=DriftSeverity.medium,
                    title=f"Tech stack item removed: {tech_name}",
                    description=(
                        f"Technology '{tech_name}' (type: {b_item.type}) was present in the "
                        f"baseline tech stack but is absent from the current state."
                    ),
                    element=tech_name,
                    suggested_fix=(
                        f"Remove '{tech_name}' from .gyrostate.yaml if the removal is intentional, "
                        f"or restore the dependency."
                    ),
                )
            )
            logger.debug("tech_stack_removed: {}", tech_name)

        # Version changes
        for tech_name in set(b_stack) & set(c_stack):
            b_item = b_stack[tech_name]
            c_item = c_stack[tech_name]
            if (
                b_item.version is not None
                and c_item.version is not None
                and b_item.version != c_item.version
            ):
                events.append(
                    DriftEvent(
                        id=_event_id(),
                        type=DriftType.tech_stack_change,
                        severity=DriftSeverity.low,
                        title=f"Tech stack version changed: {tech_name}",
                        description=(
                            f"'{tech_name}' version changed from {b_item.version} to "
                            f"{c_item.version}."
                        ),
                        element=tech_name,
                        suggested_fix=(
                            f"Update the baseline version for '{tech_name}' to {c_item.version} "
                            f"if the upgrade is intentional and tested."
                        ),
                    )
                )
                logger.debug(
                    "tech_version_changed: {} {} → {}", tech_name, b_item.version, c_item.version
                )

        return events

    # ── Data model ────────────────────────────────────────────────────────────

    def _compare_data_model(
        self,
        baseline: ArchitectureState,
        current: ArchitectureState,
    ) -> list[DriftEvent]:
        """Detect data model changes (entity added/removed, attribute changes)."""
        events: list[DriftEvent] = []

        b_entities = baseline.data_model.entities
        c_entities = current.data_model.entities

        # Removed entities
        for entity_name in set(b_entities) - set(c_entities):
            events.append(
                DriftEvent(
                    id=_event_id(),
                    type=DriftType.data_model_change,
                    severity=DriftSeverity.medium,
                    title=f"Data entity removed: {entity_name}",
                    description=(
                        f"Data entity '{entity_name}' was present in the baseline data model "
                        f"but is absent from the current state."
                    ),
                    element=entity_name,
                    suggested_fix=(
                        f"Remove '{entity_name}' from .gyrostate.yaml if the deletion is "
                        f"intentional and covered by a migration, or restore the entity."
                    ),
                )
            )
            logger.debug("data_entity_removed: {}", entity_name)

        # Added entities
        for entity_name in set(c_entities) - set(b_entities):
            events.append(
                DriftEvent(
                    id=_event_id(),
                    type=DriftType.data_model_change,
                    severity=DriftSeverity.medium,
                    title=f"Undocumented data entity added: {entity_name}",
                    description=(
                        f"Data entity '{entity_name}' exists in the current state but is "
                        f"absent from the baseline data model."
                    ),
                    element=entity_name,
                    suggested_fix=(
                        f"Document '{entity_name}' in .gyrostate.yaml under 'data_model.entities' "
                        f"with attributes, relationships, and sensitivity classification."
                    ),
                )
            )
            logger.debug("data_entity_added (undocumented): {}", entity_name)

        # Attribute-level comparison for existing entities
        for entity_name in set(b_entities) & set(c_entities):
            b_entity = b_entities[entity_name]
            c_entity = c_entities[entity_name]
            events.extend(
                self._diff_data_attributes(entity_name, b_entity, c_entity)
            )

        return events

    def _diff_data_attributes(
        self,
        entity_name: str,
        b_entity: Any,
        c_entity: Any,
    ) -> list[DriftEvent]:
        events: list[DriftEvent] = []
        b_attrs = {a.name: a for a in b_entity.attributes}
        c_attrs = {a.name: a for a in c_entity.attributes}

        for attr_name in set(b_attrs) - set(c_attrs):
            attr = b_attrs[attr_name]
            severity = DriftSeverity.high if attr.required else DriftSeverity.medium
            events.append(
                DriftEvent(
                    id=_event_id(),
                    type=DriftType.data_model_change,
                    severity=severity,
                    title=f"Data attribute removed: {entity_name}.{attr_name}",
                    description=(
                        f"Required attribute '{attr_name}' ({attr.type}) was removed from "
                        f"entity '{entity_name}'."
                        if attr.required
                        else f"Optional attribute '{attr_name}' ({attr.type}) was removed from "
                        f"entity '{entity_name}'."
                    ),
                    element=entity_name,
                    suggested_fix=(
                        f"Restore '{attr_name}' on '{entity_name}' or update the baseline and "
                        f"provide a migration that handles existing data."
                    ),
                )
            )
            logger.debug("data_attribute_removed: {}.{}", entity_name, attr_name)

        for attr_name in set(c_attrs) - set(b_attrs):
            attr = c_attrs[attr_name]
            events.append(
                DriftEvent(
                    id=_event_id(),
                    type=DriftType.data_model_change,
                    severity=DriftSeverity.medium,
                    title=f"Data attribute added: {entity_name}.{attr_name}",
                    description=(
                        f"New attribute '{attr_name}' ({attr.type}) added to entity "
                        f"'{entity_name}' but not documented in the baseline."
                    ),
                    element=entity_name,
                    suggested_fix=(
                        f"Document '{entity_name}.{attr_name}' in .gyrostate.yaml and "
                        f"ensure a migration is provided."
                    ),
                )
            )
            logger.debug("data_attribute_added: {}.{}", entity_name, attr_name)

        # Type changes on existing attributes
        for attr_name in set(b_attrs) & set(c_attrs):
            b_attr = b_attrs[attr_name]
            c_attr = c_attrs[attr_name]
            if b_attr.type != c_attr.type:
                events.append(
                    DriftEvent(
                        id=_event_id(),
                        type=DriftType.data_model_change,
                        severity=DriftSeverity.high,
                        title=f"Data attribute type changed: {entity_name}.{attr_name}",
                        description=(
                            f"Attribute '{attr_name}' on entity '{entity_name}' changed type "
                            f"from '{b_attr.type}' to '{c_attr.type}'. "
                            f"This may be a breaking schema change."
                        ),
                        element=entity_name,
                        suggested_fix=(
                            f"Ensure a database migration handles the type change for "
                            f"'{entity_name}.{attr_name}' and update the baseline."
                        ),
                    )
                )
                logger.debug(
                    "data_attribute_type_changed: {}.{} {} → {}",
                    entity_name,
                    attr_name,
                    b_attr.type,
                    c_attr.type,
                )

        return events

    # ── Rule checking ─────────────────────────────────────────────────────────

    def _check_rules(
        self,
        current: ArchitectureState,
        changed_files: list[str],
    ) -> list[DriftEvent]:
        """Check principles and invariants against the current state.

        Imports RulesEngine at call-time to avoid a circular import.
        """
        from gyrocompass.rules import RulesEngine  # local import — avoids circularity

        if not (self.rules.principles or self.rules.invariants):
            return []

        # We need a repo_path; derive it from the state's project name if not available.
        # RulesEngine accepts a Path; use CWD as best effort.
        from pathlib import Path

        engine = RulesEngine(self.rules, repo_path=Path.cwd())
        violations = engine.check_all(current, changed_files or None)

        events: list[DriftEvent] = []
        for v in violations:
            events.append(
                DriftEvent(
                    id=_event_id(),
                    type=DriftType.rule_violation,
                    severity=v.severity,
                    title=f"Rule violated: {v.rule_id}",
                    description=v.description,
                    file=v.file,
                    line=v.line,
                    suggested_fix=v.suggested_fix,
                    rule_id=v.rule_id,
                )
            )

        return events

    # ── Drift score ───────────────────────────────────────────────────────────

    def _calculate_drift_score(self, events: list[DriftEvent]) -> float:
        """Compute a 0.0–1.0 drift score weighted by severity.

        Formula (additive, capped at 1.0):
          critical : 0.40 per event
          high     : 0.20 per event
          medium   : 0.05 per event
          low/info : 0.01 per event
        """
        if not events:
            return 0.0
        raw = sum(_SEVERITY_WEIGHTS.get(e.severity, 0.01) for e in events)
        return min(raw, 1.0)

    # ── Summary ───────────────────────────────────────────────────────────────

    def _generate_summary(self, events: list[DriftEvent], score: float) -> str:
        """Return a 1–3 sentence human-readable summary of drift."""
        if not events:
            return (
                "No architectural drift detected. "
                "All changes are consistent with the documented baseline."
            )

        counts: dict[DriftSeverity, int] = {}
        for e in events:
            counts[e.severity] = counts.get(e.severity, 0) + 1

        severity_parts = []
        for sev in (
            DriftSeverity.critical,
            DriftSeverity.high,
            DriftSeverity.medium,
            DriftSeverity.low,
        ):
            if counts.get(sev):
                severity_parts.append(f"{counts[sev]} {sev.value}")

        score_pct = f"{score:.0%}"
        first = (
            f"Drift score is {score_pct} with {len(events)} deviation(s) detected "
            f"({', '.join(severity_parts)})."
        )

        # Most important event types
        type_counts: dict[DriftType, int] = {}
        for e in events:
            type_counts[e.type] = type_counts.get(e.type, 0) + 1
        top_types = sorted(type_counts.items(), key=lambda x: x[1], reverse=True)[:3]
        type_desc = ", ".join(
            f"{cnt} {t.value.replace('_', ' ')}" for t, cnt in top_types
        )
        second = f"Top issues: {type_desc}."

        if counts.get(DriftSeverity.critical, 0) > 0:
            third = (
                "Critical issues must be resolved before merging — "
                "they indicate breaking changes or rule violations."
            )
        elif counts.get(DriftSeverity.high, 0) > 0:
            third = (
                "High-severity issues indicate significant architectural deviation "
                "and should be reviewed before merging."
            )
        else:
            third = (
                "No blocking issues detected; review recommended before merging."
            )

        return f"{first} {second} {third}"


# ── Private helpers ───────────────────────────────────────────────────────────


def _find_dependents(
    target_name: str,
    architecture: dict[str, ArchitectureElement],
) -> list[str]:
    """Return names of elements that have a relationship pointing at *target_name*."""
    dependents: list[str] = []
    for elem_name, elem in architecture.items():
        if target_name in elem.relationships:
            dependents.append(elem_name)
    return dependents
