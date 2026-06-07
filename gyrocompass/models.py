"""Core Pydantic models for GyroCompass architecture state."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, computed_field


# ── Enums ─────────────────────────────────────────────────────────────────────


class ElementType(str, Enum):
    container = "container"
    component = "component"
    external_system = "external_system"
    service = "service"
    database = "database"
    queue = "queue"
    cache = "cache"


class C4Depth(str, Enum):
    system = "system"
    container = "container"
    component = "component"


class RelationType(str, Enum):
    sync = "sync"
    async_ = "async"
    event = "event"
    data = "data"


class ElementStatus(str, Enum):
    implemented = "implemented"
    planned = "planned"
    deprecated = "deprecated"
    removed = "removed"


class RuleStatus(str, Enum):
    active = "active"
    superseded = "superseded"
    proposed = "proposed"


class DriftSeverity(str, Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"
    info = "info"


class DriftType(str, Enum):
    component_added = "component_added"
    component_removed = "component_removed"
    component_modified = "component_modified"
    relationship_added = "relationship_added"
    relationship_removed = "relationship_removed"
    capability_regression = "capability_regression"
    rule_violation = "rule_violation"
    tech_stack_change = "tech_stack_change"
    api_surface_change = "api_surface_change"
    data_model_change = "data_model_change"


# ── Architecture Elements ─────────────────────────────────────────────────────


class Relationship(BaseModel):
    target: str
    type: RelationType = RelationType.sync
    protocol: str | None = None
    description: str | None = None
    facts: list[str] = Field(default_factory=list)


class ArchitectureElement(BaseModel):
    type: ElementType
    category: str | None = None
    c4_depth: C4Depth | None = None
    description: str
    facts: list[str] = Field(default_factory=list)
    status: ElementStatus | None = ElementStatus.implemented
    parent: str | None = None
    relationships: dict[str, Relationship] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)


# ── Data Model ────────────────────────────────────────────────────────────────


class DataAttribute(BaseModel):
    name: str
    type: str
    required: bool = True
    unique: bool = False
    description: str | None = None


class DataRelationship(BaseModel):
    target: str
    type: str  # oneToMany, manyToOne, manyToMany, oneToOne
    description: str | None = None


class DataEntity(BaseModel):
    description: str
    domain: str | None = None
    sensitivity: str | None = None  # public, internal, confidential, pii
    facts: list[str] = Field(default_factory=list)
    attributes: list[DataAttribute] = Field(default_factory=list)
    relationships: list[DataRelationship] = Field(default_factory=list)


class DataModel(BaseModel):
    entities: dict[str, DataEntity] = Field(default_factory=dict)


# ── Capabilities ──────────────────────────────────────────────────────────────


class Capability(BaseModel):
    description: str
    status: str = "active"  # active, planned, deprecated
    acceptance_criteria: list[str] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)


# ── Tech Stack ────────────────────────────────────────────────────────────────


class TechStackItem(BaseModel):
    type: str  # framework, library, database, service, tool
    vendor: str | None = None
    version: str | None = None
    facts: list[str] = Field(default_factory=list)


# ── API Surface ───────────────────────────────────────────────────────────────


class ApiEndpoint(BaseModel):
    type: str = "api_endpoint"
    summary: str
    auth_required: bool = True
    method: str | None = None
    request_schema: dict[str, Any] | None = None
    response_schema: dict[str, Any] | None = None


# ── Rules ─────────────────────────────────────────────────────────────────────


class RuleEvidence(BaseModel):
    pattern: str
    type: str  # import_check, code_pattern, file_exists, custom
    file: str | None = None
    description: str | None = None


class Principle(BaseModel):
    description: str
    status: RuleStatus = RuleStatus.active
    scope: list[str] = Field(default_factory=list)
    evidence: list[RuleEvidence] = Field(default_factory=list)
    rationale: str | None = None


class ADR(BaseModel):
    title: str
    status: str = "accepted"  # proposed, accepted, superseded, deprecated
    date: str | None = None
    context: str
    decision: str
    scope: list[str] = Field(default_factory=list)
    consequences: list[str] = Field(default_factory=list)


class Invariant(BaseModel):
    description: str
    status: RuleStatus = RuleStatus.active
    scope: list[str] = Field(default_factory=list)
    evidence: list[RuleEvidence] = Field(default_factory=list)
    enforcement: str = "block"  # block, warn, suggest


class Rules(BaseModel):
    principles: dict[str, Principle] = Field(default_factory=dict)
    adrs: dict[str, ADR] = Field(default_factory=dict)
    invariants: dict[str, Invariant] = Field(default_factory=dict)


# ── File Map ──────────────────────────────────────────────────────────────────


class FileMapping(BaseModel):
    file: str
    methods: list[str] = Field(default_factory=list)
    description: str | None = None


# ── Architecture State (top-level document) ───────────────────────────────────


class StateMetadata(BaseModel):
    version: str = "1.0"
    serial: int = 1
    project: str
    commit_sha: str | None = None
    captured_at: datetime = Field(default_factory=datetime.utcnow)
    generator: str = "gyrocompass"


class ArchitectureState(BaseModel):
    metadata: StateMetadata
    architecture: dict[str, ArchitectureElement] = Field(default_factory=dict)
    data_model: DataModel = Field(default_factory=DataModel)
    capabilities: dict[str, Capability] = Field(default_factory=dict)
    tech_stack: dict[str, TechStackItem] = Field(default_factory=dict)
    surface_area: dict[str, ApiEndpoint] = Field(default_factory=dict)
    services: dict[str, dict[str, Any]] = Field(default_factory=dict)

    def summary(self) -> str:
        return (
            f"Project: {self.metadata.project}\n"
            f"Components: {len(self.architecture)}\n"
            f"Capabilities: {len(self.capabilities)}\n"
            f"API Endpoints: {len(self.surface_area)}\n"
            f"Data Entities: {len(self.data_model.entities)}\n"
            f"Tech Stack: {', '.join(self.tech_stack.keys())}"
        )


# ── Drift Models ──────────────────────────────────────────────────────────────


class DriftEvent(BaseModel):
    id: str
    type: DriftType
    severity: DriftSeverity
    title: str
    description: str
    element: str | None = None
    file: str | None = None
    line: int | None = None
    suggested_fix: str | None = None
    rule_id: str | None = None
    detected_at: datetime = Field(default_factory=datetime.utcnow)


class DriftReport(BaseModel):
    project: str
    commit_sha: str | None = None
    pr_number: int | None = None
    pr_branch: str | None = None
    events: list[DriftEvent] = Field(default_factory=list)
    drift_score: float = 0.0  # 0.0 = no drift, 1.0 = complete divergence
    summary: str = ""
    generated_at: datetime = Field(default_factory=datetime.utcnow)

    # computed_field so these are included in model_dump_json() — the GitHub
    # Action and other JSON consumers read has_blocking_issues / *_count.
    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_blocking_issues(self) -> bool:
        return any(e.severity in (DriftSeverity.critical, DriftSeverity.high) for e in self.events)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def critical_count(self) -> int:
        return sum(1 for e in self.events if e.severity == DriftSeverity.critical)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def high_count(self) -> int:
        return sum(1 for e in self.events if e.severity == DriftSeverity.high)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def medium_count(self) -> int:
        return sum(1 for e in self.events if e.severity == DriftSeverity.medium)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def low_count(self) -> int:
        return sum(1 for e in self.events if e.severity == DriftSeverity.low)

    def to_markdown(self) -> str:
        lines = [
            "## GyroCompass — Architectural Drift Report",
            "",
            f"**Drift Score:** `{self.drift_score:.0%}`  ",
            f"**Issues Found:** {len(self.events)} "
            f"({self.critical_count} critical, {self.high_count} high, "
            f"{self.medium_count} medium, {self.low_count} low)",
            "",
        ]

        if not self.events:
            lines += [
                "✅ **No architectural drift detected.** "
                "Changes are consistent with the documented architecture.",
            ]
            return "\n".join(lines)

        if self.has_blocking_issues:
            lines.append("🚨 **Blocking issues require attention before merge.**")
        else:
            lines.append("⚠️ **Non-blocking issues found — review recommended.**")

        lines.append("")

        severity_order = [
            DriftSeverity.critical, DriftSeverity.high,
            DriftSeverity.medium, DriftSeverity.low, DriftSeverity.info,
        ]
        emoji = {
            DriftSeverity.critical: "🔴",
            DriftSeverity.high: "🟠",
            DriftSeverity.medium: "🟡",
            DriftSeverity.low: "🔵",
            DriftSeverity.info: "⚪",
        }

        for severity in severity_order:
            events = [e for e in self.events if e.severity == severity]
            if not events:
                continue
            lines.append(f"### {emoji[severity]} {severity.value.title()} ({len(events)})")
            lines.append("")
            for event in events:
                lines.append(f"**{event.title}** (`{event.type.value}`)")
                lines.append(f"> {event.description}")
                if event.element:
                    lines.append(f"> *Element:* `{event.element}`")
                if event.file:
                    loc = f"`{event.file}`" + (f":{event.line}" if event.line else "")
                    lines.append(f"> *Location:* {loc}")
                if event.suggested_fix:
                    lines.append(f"> *Suggestion:* {event.suggested_fix}")
                lines.append("")

        lines += [
            "---",
            "_Powered by [GyroCompass](https://github.com/gyrocompass-io/gyrocompass) "
            "— Open-source architecture guardrails for AI-native teams_",
        ]

        return "\n".join(lines)


# ── Project Config ────────────────────────────────────────────────────────────


class ProjectConfig(BaseModel):
    name: str
    description: str | None = None
    github_repo: str | None = None
    languages: list[str] = Field(default_factory=list)
    exclude_paths: list[str] = Field(
        default_factory=lambda: [
            "node_modules", ".git", "__pycache__", ".venv",
            "venv", "dist", "build", ".next", "target",
        ]
    )
    llm_provider: str = "openai"
    llm_model: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    # Enforcement configuration
    enforcement: EnforcementConfig | None = None


# ── Enforcement & Attestation ─────────────────────────────────────────────────


class EnforcementMode(str, Enum):
    """How hard a gate pushes back when a check fails."""

    block = "block"      # exit non-zero — blocks the commit / tool call
    warn = "warn"        # print warning, allow through
    off = "off"          # skip the check entirely


class EnforcementConfig(BaseModel):
    """Per-project enforcement policy, stored in .gyro/config.yaml."""

    # Require a valid attestation matching the staged diff before commit
    require_attestation: bool = False
    attestation_mode: EnforcementMode = EnforcementMode.warn
    # Block commits when rule invariants are violated
    rules_mode: EnforcementMode = EnforcementMode.block
    # Block commits when drift exceeds these severities
    drift_mode: EnforcementMode = EnforcementMode.block
    block_on_severities: list[DriftSeverity] = Field(
        default_factory=lambda: [DriftSeverity.critical, DriftSeverity.high]
    )
    # Components whose files trigger a PreToolUse warning when edited by an agent
    protected_components: list[str] = Field(default_factory=list)
    # Maximum drift score allowed (0.0-1.0); commits above this are blocked
    max_drift_score: float = 1.0


class ComplianceStatus(str, Enum):
    compliant = "compliant"
    not_applicable = "not-applicable"
    remediated = "remediated"
    needs_review = "needs-review"
    violated = "violated"


class RuleComplianceEntry(BaseModel):
    id: str
    status: ComplianceStatus
    note: str | None = None


class PrimitiveChange(BaseModel):
    """One of the eight architecture primitives in an attestation."""

    changed: bool = False
    details: list[str] = Field(default_factory=list)


class Attestation(BaseModel):
    """A commit attestation — declares what changed, why, and rule compliance.

    Bound to a specific staged diff via `staged_diff_hash`. The pre-commit hook
    recomputes the hash from the actual staged changes and refuses the commit
    if it doesn't match — a non-bypassable integrity mechanism that ties an
    attestation to exactly the change it describes.
    """

    # ── Identity / binding ──────────────────────────────────────────────────
    staged_diff_hash: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    # ── Authorship ──────────────────────────────────────────────────────────
    agent: str = "unknown"
    agent_provider: str | None = None
    agent_model: str | None = None
    agent_session_id: str | None = None

    # ── Provenance ──────────────────────────────────────────────────────────
    provenance_type: str = "ad-hoc"   # spec | ticket | bug-fix | ad-hoc
    provenance_ref: str | None = None

    # ── Rule compliance (self-reported, verified against diff) ───────────────
    rules_checked: bool = False
    rules: list[RuleComplianceEntry] = Field(default_factory=list)

    # ── Changes across the eight primitives ─────────────────────────────────
    architecture: PrimitiveChange = Field(default_factory=PrimitiveChange)
    data_model: PrimitiveChange = Field(default_factory=PrimitiveChange)
    data_flows: PrimitiveChange = Field(default_factory=PrimitiveChange)
    rules_changes: PrimitiveChange = Field(default_factory=PrimitiveChange)
    capabilities: PrimitiveChange = Field(default_factory=PrimitiveChange)
    api_surface: PrimitiveChange = Field(default_factory=PrimitiveChange)
    external_dependencies: PrimitiveChange = Field(default_factory=PrimitiveChange)
    tech_stack: PrimitiveChange = Field(default_factory=PrimitiveChange)

    # ── Reasoning ───────────────────────────────────────────────────────────
    summary: str = ""

    def to_yaml_dict(self) -> dict:
        """Serialize to the on-disk attestation YAML structure."""
        return {
            "staged_diff_hash": self.staged_diff_hash,
            "timestamp": self.timestamp.isoformat(),
            "agent": self.agent,
            "agent_provider": self.agent_provider,
            "agent_model": self.agent_model,
            "agent_session_id": self.agent_session_id,
            "provenance": {"type": self.provenance_type, "ref": self.provenance_ref},
            "rules": {
                "checked": self.rules_checked,
                "applicable": [r.model_dump(exclude_none=True) for r in self.rules],
            },
            "changes": {
                "architecture": self.architecture.model_dump(),
                "data_model": self.data_model.model_dump(),
                "data_flows": self.data_flows.model_dump(),
                "rules": self.rules_changes.model_dump(),
                "capabilities": self.capabilities.model_dump(),
                "api_surface": self.api_surface.model_dump(),
                "external_dependencies": self.external_dependencies.model_dump(),
                "tech_stack": self.tech_stack.model_dump(),
            },
            "summary": self.summary,
        }


class GateStatus(str, Enum):
    pass_ = "pass"
    blocked = "blocked"
    warned = "warned"


class GateCheck(BaseModel):
    """Result of a single enforcement check."""

    name: str                       # "rules" | "drift" | "attestation"
    status: GateStatus
    mode: EnforcementMode
    message: str
    details: list[str] = Field(default_factory=list)


class GateResult(BaseModel):
    """Aggregate result of running all enforcement gates."""

    checks: list[GateCheck] = Field(default_factory=list)
    blocked: bool = False

    @property
    def exit_code(self) -> int:
        return 1 if self.blocked else 0

    def to_terminal(self) -> str:
        icon = {GateStatus.pass_: "✅", GateStatus.warned: "⚠️ ", GateStatus.blocked: "🛑"}
        lines = ["", "GyroCompass — Enforcement Gate", "─" * 40]
        for c in self.checks:
            lines.append(f"{icon[c.status]} {c.name:14s} {c.message}")
            for d in c.details:
                lines.append(f"      • {d}")
        lines.append("─" * 40)
        if self.blocked:
            lines.append("🛑 COMMIT BLOCKED — resolve the issues above or override with --no-verify.")
        else:
            lines.append("✅ All gates passed.")
        lines.append("")
        return "\n".join(lines)


# Resolve forward reference: ProjectConfig -> EnforcementConfig
ProjectConfig.model_rebuild()


# ── Audit (ship-readiness scanner) ─────────────────────────────────────────────


class AuditCategory(str, Enum):
    security = "security"
    capability = "capability"
    tech_debt = "tech_debt"


class AuditSeverity(str, Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"


class AuditFinding(BaseModel):
    """A single ship-readiness issue, written so a coding agent can fix it."""

    id: str                          # stable check id, e.g. "secret_in_code"
    category: AuditCategory
    severity: AuditSeverity
    title: str                       # short human label
    message: str                     # agent-actionable description w/ fix guidance
    file: str | None = None
    line: int | None = None
    snippet: str | None = None
    fix: str | None = None           # concrete remediation instruction

    def to_agent_line(self) -> str:
        """One-line, agent-ready punch-list entry."""
        loc = ""
        if self.file:
            loc = f" in `{self.file}`" + (f":{self.line}" if self.line else "")
        out = f"{self.message}{loc}"
        if self.fix:
            out += f" — {self.fix}"
        return out


class AuditReport(BaseModel):
    project: str
    findings: list[AuditFinding] = Field(default_factory=list)
    files_scanned: int = 0
    generated_at: datetime = Field(default_factory=datetime.utcnow)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def by_severity(self) -> dict[str, int]:
        out = {s.value: 0 for s in AuditSeverity}
        for f in self.findings:
            out[f.severity.value] += 1
        return out

    @computed_field  # type: ignore[prop-decorator]
    @property
    def critical_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == AuditSeverity.critical)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def high_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == AuditSeverity.high)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_ship_ready(self) -> bool:
        return self.critical_count == 0 and self.high_count == 0

    def to_markdown(self) -> str:
        cat_emoji = {
            AuditCategory.security: "🔴",
            AuditCategory.capability: "🟡",
            AuditCategory.tech_debt: "🟣",
        }
        sev_rank = {
            AuditSeverity.critical: 0, AuditSeverity.high: 1,
            AuditSeverity.medium: 2, AuditSeverity.low: 3,
        }
        lines = [
            "## GyroCompass — Ship-Readiness Audit",
            "",
            f"**{len(self.findings)} finding(s)** across {self.files_scanned} files — "
            f"{self.critical_count} critical, {self.high_count} high, "
            f"{self.by_severity['medium']} medium, {self.by_severity['low']} low",
            "",
        ]
        if not self.findings:
            lines.append("✅ **Ship-ready.** No issues found by the audit checklist.")
            return "\n".join(lines)
        if self.is_ship_ready:
            lines.append("⚠️ Some lower-severity items to review, but nothing blocking.")
        else:
            lines.append("🚨 **Not ship-ready** — critical/high issues found. Fix before deploy.")
        lines.append("")
        lines.append("_Paste this into your coding agent and let it fix the findings._")
        lines.append("")

        for finding in sorted(
            self.findings, key=lambda f: (sev_rank[f.severity], f.category.value)
        ):
            emoji = cat_emoji[finding.category]
            cat = finding.category.value.upper().replace("_", " ")
            lines.append(f"{emoji} **{cat}** · `{finding.id}`")
            loc = ""
            if finding.file:
                loc = f" ({finding.file}" + (f":{finding.line}" if finding.line else "") + ")"
            lines.append(f"> {finding.message}{loc}")
            if finding.fix:
                lines.append(f"> **Fix:** {finding.fix}")
            lines.append("")

        lines += [
            "---",
            "_Generated by [GyroCompass](https://github.com/gyrocompass-io/gyrocompass) "
            "— ship-readiness audit for AI-built apps_",
        ]
        return "\n".join(lines)
