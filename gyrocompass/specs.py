"""GyroCompass spec manager — read/write .gyro/ state files (YAML) and LLM-powered spec generation.

Primary responsibilities
------------------------
1. File I/O   — load/save .gyro/.gyrostate.yaml, .gyrorules.yaml, config.yaml
2. Serialisation — ArchitectureState / Rules ↔ human-readable, git-diffable YAML
3. LLM-powered generation — enhance raw indexed state with richer descriptions/facts
4. Agent-facing helpers — get_context_for_agent, get_file_context, get_impact_analysis
"""

from __future__ import annotations

import re
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from loguru import logger

from gyrocompass.config import (
    GYROCONFIG_FILE,
    GYRORULES_FILE,
    GYROSTATE_FILE,
    get_gyro_dir,
    get_rules_path,
    get_state_path,
)
from gyrocompass.models import (
    ADR,
    ApiEndpoint,
    ArchitectureElement,
    ArchitectureState,
    Capability,
    DataAttribute,
    DataEntity,
    DataModel,
    DataRelationship,
    ElementStatus,
    ElementType,
    Invariant,
    Principle,
    ProjectConfig,
    Relationship,
    RelationType,
    Rules,
    RuleStatus,
    StateMetadata,
    TechStackItem,
)

if TYPE_CHECKING:
    from gyrocompass.llm.providers import BaseLLMProvider


# ── YAML presenter tweaks ─────────────────────────────────────────────────────
# Produce nice multi-line strings and preserve insertion order.


def _str_representer(dumper: yaml.Dumper, data: str) -> yaml.Node:
    """Use literal block style for multi-line strings."""
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


def _none_representer(dumper: yaml.Dumper, _: None) -> yaml.Node:
    """Emit null as empty string for readability."""
    return dumper.represent_scalar("tag:yaml.org,2002:null", "")


# Build a custom dumper once so callers don't repeat themselves.
class _GyroYamlDumper(yaml.Dumper):
    pass


_GyroYamlDumper.add_representer(str, _str_representer)
_GyroYamlDumper.add_representer(type(None), _none_representer)


def _dump(data: Any, stream=None) -> str:
    return yaml.dump(
        data,
        stream=stream,
        Dumper=_GyroYamlDumper,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
        width=120,
    )


# ── SpecManager ───────────────────────────────────────────────────────────────


class SpecManager:
    """Read/write the .gyro/ state files and generate specs from indexed code.

    Args:
        repo_path: Absolute (or relative) path to the root of the target repo.
                   The .gyro/ folder is expected to live directly inside this
                   directory.
    """

    def __init__(self, repo_path: str | Path) -> None:
        self.repo_path = Path(repo_path).resolve()

    # ── Internal paths ─────────────────────────────────────────────────────────

    @property
    def _gyro_dir(self) -> Path:
        return get_gyro_dir(self.repo_path)

    @property
    def _state_path(self) -> Path:
        return get_state_path(self.repo_path)

    @property
    def _rules_path(self) -> Path:
        return get_rules_path(self.repo_path)

    @property
    def _config_path(self) -> Path:
        return self.repo_path / GYROCONFIG_FILE

    # ── File I/O ───────────────────────────────────────────────────────────────

    def load_state(self) -> ArchitectureState | None:
        """Load .gyro/.gyrostate.yaml.  Returns None if not found."""
        if not self._state_path.exists():
            logger.debug("State file not found: {}", self._state_path)
            return None
        try:
            raw = yaml.safe_load(self._state_path.read_text(encoding="utf-8"))
            if not raw:
                return None
            state = self._dict_to_state(raw)
            logger.debug(
                "Loaded state — project={} serial={}",
                state.metadata.project,
                state.metadata.serial,
            )
            return state
        except Exception as exc:
            logger.error("Failed to load state from {}: {}", self._state_path, exc)
            raise

    def save_state(self, state: ArchitectureState) -> None:
        """Save to .gyro/.gyrostate.yaml.  Creates .gyro/ dir if needed."""
        self._gyro_dir.mkdir(parents=True, exist_ok=True)
        yaml_str = self.state_to_yaml(state)
        self._state_path.write_text(yaml_str, encoding="utf-8")
        logger.info("Saved state to {} (serial={})", self._state_path, state.metadata.serial)

    def load_rules(self) -> Rules:
        """Load .gyro/.gyrorules.yaml.  Returns empty Rules if not found."""
        if not self._rules_path.exists():
            logger.debug("Rules file not found: {}", self._rules_path)
            return Rules()
        try:
            raw = yaml.safe_load(self._rules_path.read_text(encoding="utf-8"))
            if not raw:
                return Rules()
            rules = self._dict_to_rules(raw)
            logger.debug(
                "Loaded rules — principles={} invariants={} adrs={}",
                len(rules.principles),
                len(rules.invariants),
                len(rules.adrs),
            )
            return rules
        except Exception as exc:
            logger.error("Failed to load rules from {}: {}", self._rules_path, exc)
            raise

    def save_rules(self, rules: Rules) -> None:
        """Save to .gyro/.gyrorules.yaml.  Creates .gyro/ dir if needed."""
        self._gyro_dir.mkdir(parents=True, exist_ok=True)
        yaml_str = self.rules_to_yaml(rules)
        self._rules_path.write_text(yaml_str, encoding="utf-8")
        logger.info("Saved rules to {}", self._rules_path)

    def load_config(self) -> ProjectConfig | None:
        """Load .gyro/config.yaml."""
        if not self._config_path.exists():
            logger.debug("Config file not found: {}", self._config_path)
            return None
        try:
            raw = yaml.safe_load(self._config_path.read_text(encoding="utf-8"))
            if not raw:
                return None
            return ProjectConfig(**raw)
        except Exception as exc:
            logger.error("Failed to load config from {}: {}", self._config_path, exc)
            raise

    def save_config(self, config: ProjectConfig) -> None:
        """Save to .gyro/config.yaml."""
        self._gyro_dir.mkdir(parents=True, exist_ok=True)
        data: dict[str, Any] = {
            "name": config.name,
            "description": config.description,
            "github_repo": config.github_repo,
            "languages": config.languages,
            "exclude_paths": config.exclude_paths,
            "llm_provider": config.llm_provider,
            "llm_model": config.llm_model,
            "created_at": config.created_at.isoformat(),
        }
        self._config_path.write_text(_dump(data), encoding="utf-8")
        logger.info("Saved config to {}", self._config_path)

    def is_initialized(self) -> bool:
        """True if .gyro/ folder exists with at least config.yaml."""
        return self._gyro_dir.is_dir() and self._config_path.exists()

    # ── LLM-powered generation ─────────────────────────────────────────────────

    def generate_state_from_indexed(
        self,
        indexed_state: ArchitectureState,
        llm_provider: BaseLLMProvider | None = None,
        enhance_with_llm: bool = True,
    ) -> ArchitectureState:
        """Take the raw indexed state and optionally enhance descriptions/facts with LLM.

        When *enhance_with_llm* is True and an LLM provider is given, each
        architecture element's description is enriched and LLM-generated facts
        are appended.  The metadata serial is incremented.

        Args:
            indexed_state:   Raw ArchitectureState produced by CodeIndexer.
            llm_provider:    Optional BaseLLMProvider.  When None the state is
                             returned as-is (with serial bumped).
            enhance_with_llm: Set False to skip LLM calls even if a provider
                              is supplied — useful for tests.

        Returns:
            An enhanced ArchitectureState.
        """
        import copy

        state = copy.deepcopy(indexed_state)
        state.metadata.serial += 1
        state.metadata.captured_at = datetime.now(tz=timezone.utc)

        if not enhance_with_llm or llm_provider is None:
            logger.info("Skipping LLM enhancement (no provider or disabled)")
            return state

        logger.info(
            "Enhancing {} architecture elements with LLM descriptions",
            len(state.architecture),
        )

        system_prompt = textwrap.dedent("""\
            You are a senior software architect.  Given the name and technical
            facts about an architecture component, return a single concise
            paragraph (2–4 sentences) that describes the component's purpose,
            responsibilities, and role in the overall system.  Be specific and
            technical.  Do NOT use markdown.
        """)

        for elem_id, elem in state.architecture.items():
            facts_text = "\n".join(f"- {f}" for f in elem.facts) if elem.facts else "(none)"
            prompt = (
                f"Component: {elem_id}\n"
                f"Type: {elem.type.value}\n"
                f"Current description: {elem.description}\n"
                f"Known facts:\n{facts_text}\n\n"
                f"Write an improved description for this component."
            )
            try:
                enhanced = llm_provider.complete(prompt, system=system_prompt)
                enhanced = enhanced.strip()
                if enhanced:
                    elem.description = enhanced
                    logger.debug("Enhanced description for '{}'", elem_id)
            except Exception as exc:
                logger.warning("LLM enhancement failed for '{}': {}", elem_id, exc)

        return state

    def generate_rules_template(self, state: ArchitectureState) -> Rules:
        """Generate a starter rules file based on the architecture state.

        Produces a set of sensible default principles and invariants derived
        from the detected tech stack, capabilities, and API surface.

        Args:
            state: The current ArchitectureState (indexed or loaded from disk).

        Returns:
            A Rules object pre-populated with starter rules.
        """
        from gyrocompass.models import RuleEvidence

        rules = Rules()
        stack_keys = set(state.tech_stack.keys())

        # ── Principles ─────────────────────────────────────────────────────────

        rules.principles["clean-architecture"] = Principle(
            description=(
                "Business logic must not depend on infrastructure concerns.  "
                "Core domain code should be free of framework-specific imports."
            ),
            status=RuleStatus.active,
            scope=[],
            rationale="Preserves testability and portability of the domain layer.",
        )

        if any(
            kw in " ".join(stack_keys).lower()
            for kw in ("sqlalchemy", "prisma", "gorm", "typeorm", "hibernate")
        ):
            rules.principles["orm-only-db-access"] = Principle(
                description=(
                    "All database access must go through the ORM layer.  "
                    "Raw SQL queries are not permitted outside of migration scripts."
                ),
                status=RuleStatus.active,
                scope=[],
                rationale="Ensures consistent query patterns and prevents SQL injection.",
            )

        if any(kw in " ".join(stack_keys).lower() for kw in ("pydantic", "zod", "joi")):
            rules.principles["validated-inputs"] = Principle(
                description=(
                    "All external inputs (API requests, event payloads) must be validated "
                    "using the project's schema-validation library before processing."
                ),
                status=RuleStatus.active,
                scope=[],
                rationale="Prevents invalid data from reaching business logic.",
            )

        # ── Invariants ─────────────────────────────────────────────────────────

        if state.surface_area:
            auth_endpoints = [k for k, v in state.surface_area.items() if v.auth_required]
            if auth_endpoints:
                rules.invariants["auth-required-on-api"] = Invariant(
                    description=(
                        "All non-health-check API endpoints must require authentication.  "
                        "Public endpoints must be explicitly declared."
                    ),
                    status=RuleStatus.active,
                    scope=[],
                    enforcement="block",
                )

        rules.invariants["no-hardcoded-secrets"] = Invariant(
            description="Secrets, passwords, and API keys must not be hardcoded in source files.",
            status=RuleStatus.active,
            scope=[],
            evidence=[
                RuleEvidence(
                    pattern=r'(?:password|secret|api_key|apikey|token)\s*=\s*["\'][^"\']{8,}["\']',
                    type="code_pattern",
                    description=(
                        "Move secrets to environment variables or a secrets manager."
                    ),
                )
            ],
            enforcement="block",
        )

        rules.invariants["no-print-statements"] = Invariant(
            description="Use structured logging instead of print() for observability.",
            status=RuleStatus.active,
            scope=["**/*.py"],
            evidence=[
                RuleEvidence(
                    pattern=r"^\s*print\s*\(",
                    type="code_pattern",
                    description="Replace print() with logger.info/warning/error.",
                )
            ],
            enforcement="warn",
        )

        # ── ADRs ───────────────────────────────────────────────────────────────

        stack_list = ", ".join(sorted(stack_keys)[:10]) if stack_keys else "undetermined"
        rules.adrs["adr-001-tech-stack"] = ADR(
            title="Approved Technology Stack",
            status="accepted",
            date=datetime.now(tz=timezone.utc).strftime("%Y-%m-%d"),
            context=(
                "The project has adopted a specific technology stack to ensure consistency "
                "across services and simplify onboarding."
            ),
            decision=(
                f"The approved stack includes: {stack_list}.  "
                "Any addition of new technologies requires an ADR and team review."
            ),
            scope=list(state.architecture.keys()),
            consequences=[
                "Reduces cognitive overhead for contributors.",
                "New tech additions are tracked and justified.",
            ],
        )

        logger.info(
            "Generated rules template — principles={} invariants={} adrs={}",
            len(rules.principles),
            len(rules.invariants),
            len(rules.adrs),
        )
        return rules

    def add_rule(
        self,
        rule_type: str,
        rule_id: str,
        rule_data: dict,
    ) -> None:
        """Add a single rule to .gyrorules.yaml.

        Loads the existing rules file (or creates an empty one), inserts the
        new rule, then saves.

        Args:
            rule_type: ``"principle"``, ``"invariant"``, or ``"adr"``.
            rule_id:   Unique slug for the rule (e.g. ``"no-direct-db-access"``).
            rule_data: A dict with the rule's fields (matches the Pydantic model).
        """
        rules = self.load_rules()

        rt = rule_type.lower()
        if rt == "principle":
            rules.principles[rule_id] = Principle(**rule_data)
        elif rt == "invariant":
            rules.invariants[rule_id] = Invariant(**rule_data)
        elif rt == "adr":
            rules.adrs[rule_id] = ADR(**rule_data)
        else:
            raise ValueError(
                f"Unknown rule_type '{rule_type}'.  "
                "Expected 'principle', 'invariant', or 'adr'."
            )

        self.save_rules(rules)
        logger.info("Added {} '{}' to {}", rule_type, rule_id, self._rules_path)

    # ── YAML serialisation ─────────────────────────────────────────────────────

    def state_to_yaml(self, state: ArchitectureState) -> str:
        """Serialize ArchitectureState to a human-readable, git-diffable YAML string."""
        lines: list[str] = []

        # ── File header ───────────────────────────────────────────────────────
        lines.append("# GyroCompass Architecture State")
        lines.append(
            "# Generated by GyroCompass — open-source architecture guardrails"
        )
        lines.append(
            "# https://github.com/gyrocompass-io/gyrocompass"
        )
        lines.append("")

        # ── Metadata ──────────────────────────────────────────────────────────
        lines.append("# ── Metadata ─────────────────────────────────────────────────────────────────")
        meta: dict[str, Any] = {
            "metadata": {
                "version": state.metadata.version,
                "serial": state.metadata.serial,
                "project": state.metadata.project,
                "captured_at": state.metadata.captured_at.isoformat()
                if isinstance(state.metadata.captured_at, datetime)
                else str(state.metadata.captured_at),
                "generator": state.metadata.generator,
            }
        }
        if state.metadata.commit_sha:
            meta["metadata"]["commit_sha"] = state.metadata.commit_sha
        lines.append(_dump(meta).rstrip())
        lines.append("")

        # ── Architecture ───────────────────────────────────────────────────────
        lines.append("# ── Architecture ─────────────────────────────────────────────────────────────")
        arch_dict: dict[str, Any] = {}
        for elem_id, elem in state.architecture.items():
            arch_dict[elem_id] = _elem_to_dict(elem)
        lines.append(_dump({"architecture": arch_dict}).rstrip())
        lines.append("")

        # ── Capabilities ───────────────────────────────────────────────────────
        if state.capabilities:
            lines.append("# ── Capabilities ─────────────────────────────────────────────────────────────")
            cap_dict: dict[str, Any] = {}
            for cap_id, cap in state.capabilities.items():
                cap_dict[cap_id] = _cap_to_dict(cap)
            lines.append(_dump({"capabilities": cap_dict}).rstrip())
            lines.append("")

        # ── Tech Stack ─────────────────────────────────────────────────────────
        if state.tech_stack:
            lines.append("# ── Tech Stack ───────────────────────────────────────────────────────────────")
            ts_dict: dict[str, Any] = {}
            for tech_id, item in state.tech_stack.items():
                ts_dict[tech_id] = _tech_to_dict(item)
            lines.append(_dump({"tech_stack": ts_dict}).rstrip())
            lines.append("")

        # ── API Surface ────────────────────────────────────────────────────────
        if state.surface_area:
            lines.append("# ── API Surface ──────────────────────────────────────────────────────────────")
            sa_dict: dict[str, Any] = {}
            for endpoint, ep in state.surface_area.items():
                sa_dict[endpoint] = _endpoint_to_dict(ep)
            lines.append(_dump({"surface_area": sa_dict}).rstrip())
            lines.append("")

        # ── Data Model ─────────────────────────────────────────────────────────
        if state.data_model.entities:
            lines.append("# ── Data Model ───────────────────────────────────────────────────────────────")
            dm_dict: dict[str, Any] = {}
            for entity_id, entity in state.data_model.entities.items():
                dm_dict[entity_id] = _entity_to_dict(entity)
            lines.append(_dump({"data_model": dm_dict}).rstrip())
            lines.append("")

        return "\n".join(lines)

    def rules_to_yaml(self, rules: Rules) -> str:
        """Serialize Rules to YAML string."""
        lines: list[str] = []
        lines.append("# GyroCompass Architecture Rules")
        lines.append(
            "# Edit this file to define principles, invariants, and ADRs for your project."
        )
        lines.append("")

        if rules.principles:
            lines.append("# ── Principles ───────────────────────────────────────────────────────────────")
            p_dict: dict[str, Any] = {}
            for p_id, p in rules.principles.items():
                p_dict[p_id] = _principle_to_dict(p)
            lines.append(_dump({"principles": p_dict}).rstrip())
            lines.append("")

        if rules.invariants:
            lines.append("# ── Invariants ───────────────────────────────────────────────────────────────")
            i_dict: dict[str, Any] = {}
            for i_id, inv in rules.invariants.items():
                i_dict[i_id] = _invariant_to_dict(inv)
            lines.append(_dump({"invariants": i_dict}).rstrip())
            lines.append("")

        if rules.adrs:
            lines.append("# ── Architecture Decision Records ────────────────────────────────────────────")
            a_dict: dict[str, Any] = {}
            for a_id, adr in rules.adrs.items():
                a_dict[a_id] = _adr_to_dict(adr)
            lines.append(_dump({"adrs": a_dict}).rstrip())
            lines.append("")

        return "\n".join(lines)

    def yaml_to_state(self, yaml_str: str) -> ArchitectureState:
        """Parse YAML string into ArchitectureState."""
        raw = yaml.safe_load(yaml_str)
        if not raw:
            raise ValueError("Empty or invalid YAML")
        return self._dict_to_state(raw)

    def yaml_to_rules(self, yaml_str: str) -> Rules:
        """Parse YAML string into Rules."""
        raw = yaml.safe_load(yaml_str)
        if not raw:
            return Rules()
        return self._dict_to_rules(raw)

    # ── Agent context helpers ──────────────────────────────────────────────────

    def get_context_for_agent(
        self,
        state: ArchitectureState,
        rules: Rules,
        query: str | None = None,
    ) -> str:
        """Return a Markdown string suitable for injection into an AI agent context.

        Includes architecture overview, relevant capabilities, active rules, and
        tech stack.  When *query* is provided the output is focused on the most
        relevant components.  Aimed at ~2000 tokens maximum.

        Args:
            state: The current ArchitectureState.
            rules: The active Rules.
            query: Optional natural-language query to focus the output.

        Returns:
            Markdown string ready for injection into an agent's system prompt
            or context window.
        """
        lines: list[str] = []

        # ── Header ──────────────────────────────────────────────────────────
        lines.append(f"# Architecture Context — {state.metadata.project}")
        lines.append("")
        lines.append(
            f"> *State serial {state.metadata.serial} · "
            f"captured {_fmt_dt(state.metadata.captured_at)} · "
            f"generated by GyroCompass*"
        )
        lines.append("")

        # ── Overview ────────────────────────────────────────────────────────
        lines.append("## Overview")
        lines.append("")
        lines.append(
            f"- **Components:** {len(state.architecture)}"
            f"  |  **Capabilities:** {len(state.capabilities)}"
            f"  |  **API Endpoints:** {len(state.surface_area)}"
            f"  |  **Data Entities:** {len(state.data_model.entities)}"
        )
        lines.append("")

        # ── Tech stack ───────────────────────────────────────────────────────
        if state.tech_stack:
            ts_parts = []
            for tech, item in list(state.tech_stack.items())[:12]:
                vendor = f" ({item.vendor})" if item.vendor else ""
                ts_parts.append(f"`{tech}`{vendor}")
            lines.append(f"**Tech Stack:** {', '.join(ts_parts)}")
            lines.append("")

        # ── Architecture components ──────────────────────────────────────────
        lines.append("## Architecture Components")
        lines.append("")

        # If a query was provided, rank components by relevance
        arch_items = list(state.architecture.items())
        if query:
            arch_items = _rank_by_relevance(arch_items, query)

        # Cap output to avoid token overflow
        shown = arch_items[:20]
        for elem_id, elem in shown:
            rel_list = [
                f"`{t}` via {r.type.value}"
                for t, r in list(elem.relationships.items())[:4]
            ]
            rel_str = f"  →  {', '.join(rel_list)}" if rel_list else ""
            lines.append(
                f"### `{elem_id}`  _{elem.type.value}_"
            )
            lines.append(f"{elem.description}{rel_str}")
            if elem.facts:
                for fact in elem.facts[:3]:
                    lines.append(f"- {fact}")
            lines.append("")

        if len(arch_items) > 20:
            lines.append(
                f"*…and {len(arch_items) - 20} more components not shown.*"
            )
            lines.append("")

        # ── Capabilities ────────────────────────────────────────────────────
        active_caps = {
            k: v for k, v in state.capabilities.items() if v.status == "active"
        }
        if active_caps:
            lines.append("## Active Capabilities")
            lines.append("")
            for cap_id, cap in list(active_caps.items())[:10]:
                lines.append(f"- **{cap_id}**: {cap.description}")
            lines.append("")

        # ── Active rules ────────────────────────────────────────────────────
        active_principles = {
            k: v
            for k, v in rules.principles.items()
            if v.status == RuleStatus.active
        }
        active_invariants = {
            k: v
            for k, v in rules.invariants.items()
            if v.status == RuleStatus.active
        }

        if active_principles or active_invariants:
            lines.append("## Active Architecture Rules")
            lines.append("")

        if active_principles:
            lines.append("### Principles")
            lines.append("")
            for p_id, principle in list(active_principles.items())[:8]:
                lines.append(f"- **{p_id}**: {principle.description}")
            lines.append("")

        if active_invariants:
            lines.append("### Invariants (enforced)")
            lines.append("")
            for i_id, inv in list(active_invariants.items())[:8]:
                enforcement = inv.enforcement.upper()
                lines.append(
                    f"- **{i_id}** `[{enforcement}]`: {inv.description}"
                )
            lines.append("")

        # ── ADRs ─────────────────────────────────────────────────────────────
        accepted_adrs = {
            k: v for k, v in rules.adrs.items() if v.status == "accepted"
        }
        if accepted_adrs:
            lines.append("### Architecture Decision Records")
            lines.append("")
            for a_id, adr in list(accepted_adrs.items())[:6]:
                lines.append(f"- **{a_id}** — {adr.title}: {adr.decision[:120]}")
            lines.append("")

        # ── API surface (brief) ──────────────────────────────────────────────
        if state.surface_area:
            lines.append("## API Surface (sample)")
            lines.append("")
            surface_items = list(state.surface_area.items())
            if query:
                surface_items = _rank_by_relevance(surface_items, query)
            for endpoint, ep in surface_items[:8]:
                auth = "🔒" if ep.auth_required else "🔓"
                lines.append(f"- {auth} `{endpoint}` — {ep.summary[:80]}")
            if len(state.surface_area) > 8:
                lines.append(
                    f"  *…{len(state.surface_area) - 8} more endpoints not shown.*"
                )
            lines.append("")

        return "\n".join(lines)

    def get_file_context(
        self,
        state: ArchitectureState,
        file_path: str,
    ) -> str:
        """Return context about a specific file's architectural role.

        Args:
            state:     The current ArchitectureState.
            file_path: Relative (to repo root) or absolute path to the file.

        Returns:
            Markdown string describing the file's component membership,
            relationships, and relevant rules.
        """
        # Normalise to relative path
        fp = Path(file_path)
        if fp.is_absolute():
            try:
                fp = fp.relative_to(self.repo_path)
            except ValueError:
                pass
        rel = str(fp)

        lines: list[str] = []
        lines.append(f"## File Context — `{rel}`")
        lines.append("")

        # Find which component(s) this file belongs to based on path overlap
        matched_components: list[tuple[str, ArchitectureElement]] = []
        for elem_id, elem in state.architecture.items():
            # Check if any fact mentions this file
            file_mentioned = any(rel in fact for fact in elem.facts)
            # Also check path prefix match
            path_match = rel.startswith(elem_id.rstrip("/"))
            if file_mentioned or path_match:
                matched_components.append((elem_id, elem))

        if not matched_components:
            # Fuzzy: try matching on the parent directory
            parent_dir = str(fp.parent)
            for elem_id, elem in state.architecture.items():
                if parent_dir in elem_id or elem_id in parent_dir:
                    matched_components.append((elem_id, elem))

        if matched_components:
            lines.append("### Component Membership")
            lines.append("")
            for elem_id, elem in matched_components[:3]:
                lines.append(f"- **`{elem_id}`** ({elem.type.value}): {elem.description}")
                if elem.relationships:
                    rel_parts = [
                        f"`{t}` ({r.type.value})"
                        for t, r in list(elem.relationships.items())[:4]
                    ]
                    lines.append(f"  Relationships: {', '.join(rel_parts)}")
            lines.append("")
        else:
            lines.append(f"_No component match found for `{rel}` in the architecture map._")
            lines.append("")

        # Find relevant API endpoints
        relevant_endpoints = [
            (ep_key, ep)
            for ep_key, ep in state.surface_area.items()
        ]
        # File name heuristic: route files expose endpoints
        stem = fp.stem.lower()
        if any(
            kw in stem for kw in ("route", "handler", "view", "controller", "api", "endpoint")
        ) and relevant_endpoints:
            lines.append("### Possibly Related API Endpoints")
            lines.append("")
            for ep_key, ep in relevant_endpoints[:5]:
                auth = "🔒" if ep.auth_required else "🔓"
                lines.append(f"- {auth} `{ep_key}` — {ep.summary[:80]}")
            lines.append("")

        return "\n".join(lines)

    def get_impact_analysis(
        self,
        state: ArchitectureState,
        changed_files: list[str],
    ) -> str:
        """Return impact analysis for a set of changed files.

        Identifies which architecture components are affected by the changes,
        which other components may be impacted transitively via relationships,
        and which rules are most relevant.

        Args:
            state:         The current ArchitectureState.
            changed_files: List of relative file paths that have changed.

        Returns:
            Markdown impact analysis string.
        """
        lines: list[str] = []
        lines.append("## Impact Analysis")
        lines.append("")
        lines.append(
            f"Analysing **{len(changed_files)}** changed file(s) for architectural impact."
        )
        lines.append("")

        # Identify directly affected components
        directly_affected: set[str] = set()
        for cf in changed_files:
            cf_path = Path(cf)
            for elem_id in state.architecture:
                # Path prefix match
                if cf.startswith(elem_id) or elem_id in cf:
                    directly_affected.add(elem_id)
                # Check facts
                elem = state.architecture[elem_id]
                if any(cf in fact for fact in elem.facts):
                    directly_affected.add(elem_id)

        # If no matches via facts, fall back to parent directory matching
        if not directly_affected:
            for cf in changed_files:
                parts = Path(cf).parts
                for elem_id in state.architecture:
                    for part in parts[:-1]:  # exclude filename
                        if part in elem_id:
                            directly_affected.add(elem_id)

        # Transitively affected components (via relationships)
        transitively_affected: set[str] = set()
        for da_id in directly_affected:
            for elem_id, elem in state.architecture.items():
                if elem_id in directly_affected:
                    continue
                for rel_key, rel in elem.relationships.items():
                    if rel.target in directly_affected or rel_key in directly_affected:
                        transitively_affected.add(elem_id)

        # Reverse: who depends on directly_affected
        dependents: set[str] = set()
        for da_id in directly_affected:
            for elem_id, elem in state.architecture.items():
                if elem_id == da_id:
                    continue
                for rel_key, rel in elem.relationships.items():
                    if rel.target == da_id:
                        dependents.add(elem_id)

        # ── Report ───────────────────────────────────────────────────────────
        if directly_affected:
            lines.append("### Directly Affected Components")
            lines.append("")
            for elem_id in sorted(directly_affected):
                elem = state.architecture[elem_id]
                lines.append(f"- **`{elem_id}`** ({elem.type.value}): {elem.description[:100]}")
            lines.append("")
        else:
            lines.append(
                "_No directly affected components identified — "
                "changes may be in untracked or test files._"
            )
            lines.append("")

        if dependents:
            lines.append("### Downstream Dependents (may be impacted)")
            lines.append("")
            for elem_id in sorted(dependents):
                elem = state.architecture[elem_id]
                lines.append(f"- **`{elem_id}`** ({elem.type.value}): {elem.description[:80]}")
            lines.append("")

        if transitively_affected:
            lines.append("### Transitively Affected Components")
            lines.append("")
            for elem_id in sorted(transitively_affected):
                elem = state.architecture[elem_id]
                lines.append(f"- **`{elem_id}`** ({elem.type.value})")
            lines.append("")

        # ── Changed files list ───────────────────────────────────────────────
        lines.append("### Changed Files")
        lines.append("")
        for cf in changed_files[:30]:
            ext = Path(cf).suffix
            icon = _file_icon(ext)
            lines.append(f"- {icon} `{cf}`")
        if len(changed_files) > 30:
            lines.append(f"  *…and {len(changed_files) - 30} more files.*")
        lines.append("")

        # ── Risk summary ─────────────────────────────────────────────────────
        total_affected = len(directly_affected) + len(dependents)
        if total_affected == 0:
            risk = "Low"
        elif total_affected <= 3:
            risk = "Medium"
        else:
            risk = "High"

        lines.append(f"**Estimated Impact Radius:** {total_affected} component(s) — **{risk} risk**")
        lines.append("")

        return "\n".join(lines)

    # ── Private helpers: dict↔model ────────────────────────────────────────────

    def _dict_to_state(self, raw: dict) -> ArchitectureState:
        """Deserialise a parsed YAML dict into an ArchitectureState."""
        meta_raw = raw.get("metadata", {})

        captured_at = meta_raw.get("captured_at")
        if isinstance(captured_at, str):
            try:
                captured_at = datetime.fromisoformat(captured_at)
            except ValueError:
                captured_at = datetime.now(tz=timezone.utc)
        elif captured_at is None:
            captured_at = datetime.now(tz=timezone.utc)

        metadata = StateMetadata(
            version=str(meta_raw.get("version", "1.0")),
            serial=int(meta_raw.get("serial", 1)),
            project=str(meta_raw.get("project", "unknown")),
            commit_sha=meta_raw.get("commit_sha"),
            captured_at=captured_at,
            generator=str(meta_raw.get("generator", "gyrocompass")),
        )

        # Architecture
        architecture: dict[str, ArchitectureElement] = {}
        for elem_id, elem_raw in (raw.get("architecture") or {}).items():
            if not isinstance(elem_raw, dict):
                continue
            architecture[elem_id] = _dict_to_elem(elem_raw)

        # Capabilities
        capabilities: dict[str, Capability] = {}
        for cap_id, cap_raw in (raw.get("capabilities") or {}).items():
            if not isinstance(cap_raw, dict):
                continue
            capabilities[cap_id] = Capability(
                description=str(cap_raw.get("description", "")),
                status=str(cap_raw.get("status", "active")),
                acceptance_criteria=list(cap_raw.get("acceptance_criteria") or []),
                facts=list(cap_raw.get("facts") or []),
            )

        # Tech stack
        tech_stack: dict[str, TechStackItem] = {}
        for tech_id, ts_raw in (raw.get("tech_stack") or {}).items():
            if not isinstance(ts_raw, dict):
                continue
            tech_stack[tech_id] = TechStackItem(
                type=str(ts_raw.get("type", "library")),
                vendor=ts_raw.get("vendor"),
                version=ts_raw.get("version"),
                facts=list(ts_raw.get("facts") or []),
            )

        # Surface area
        surface_area: dict[str, ApiEndpoint] = {}
        for ep_key, ep_raw in (raw.get("surface_area") or {}).items():
            if not isinstance(ep_raw, dict):
                continue
            surface_area[ep_key] = ApiEndpoint(
                type=str(ep_raw.get("type", "api_endpoint")),
                summary=str(ep_raw.get("summary", "")),
                auth_required=bool(ep_raw.get("auth_required", True)),
                method=ep_raw.get("method"),
                request_schema=ep_raw.get("request_schema"),
                response_schema=ep_raw.get("response_schema"),
            )

        # Data model
        data_model = DataModel()
        for entity_id, entity_raw in (raw.get("data_model") or {}).items():
            if not isinstance(entity_raw, dict):
                continue
            attrs: list[DataAttribute] = []
            for attr_raw in entity_raw.get("attributes") or []:
                if isinstance(attr_raw, dict):
                    attrs.append(
                        DataAttribute(
                            name=str(attr_raw.get("name", "")),
                            type=str(attr_raw.get("type", "string")),
                            required=bool(attr_raw.get("required", True)),
                            unique=bool(attr_raw.get("unique", False)),
                            description=attr_raw.get("description"),
                        )
                    )
            data_rels: list[DataRelationship] = []
            for dr_raw in entity_raw.get("relationships") or []:
                if isinstance(dr_raw, dict):
                    data_rels.append(
                        DataRelationship(
                            target=str(dr_raw.get("target", "")),
                            type=str(dr_raw.get("type", "oneToMany")),
                            description=dr_raw.get("description"),
                        )
                    )
            data_model.entities[entity_id] = DataEntity(
                description=str(entity_raw.get("description", "")),
                domain=entity_raw.get("domain"),
                sensitivity=entity_raw.get("sensitivity"),
                facts=list(entity_raw.get("facts") or []),
                attributes=attrs,
                relationships=data_rels,
            )

        return ArchitectureState(
            metadata=metadata,
            architecture=architecture,
            data_model=data_model,
            capabilities=capabilities,
            tech_stack=tech_stack,
            surface_area=surface_area,
        )

    def _dict_to_rules(self, raw: dict) -> Rules:
        """Deserialise a parsed YAML dict into a Rules object."""
        from gyrocompass.models import RuleEvidence

        rules = Rules()

        for p_id, p_raw in (raw.get("principles") or {}).items():
            if not isinstance(p_raw, dict):
                continue
            evidence = [
                RuleEvidence(**ev) if isinstance(ev, dict) else ev
                for ev in (p_raw.get("evidence") or [])
            ]
            rules.principles[p_id] = Principle(
                description=str(p_raw.get("description", "")),
                status=RuleStatus(p_raw.get("status", "active")),
                scope=list(p_raw.get("scope") or []),
                evidence=evidence,
                rationale=p_raw.get("rationale"),
            )

        for i_id, i_raw in (raw.get("invariants") or {}).items():
            if not isinstance(i_raw, dict):
                continue
            evidence = [
                RuleEvidence(**ev) if isinstance(ev, dict) else ev
                for ev in (i_raw.get("evidence") or [])
            ]
            rules.invariants[i_id] = Invariant(
                description=str(i_raw.get("description", "")),
                status=RuleStatus(i_raw.get("status", "active")),
                scope=list(i_raw.get("scope") or []),
                evidence=evidence,
                enforcement=str(i_raw.get("enforcement", "block")),
            )

        for a_id, a_raw in (raw.get("adrs") or {}).items():
            if not isinstance(a_raw, dict):
                continue
            rules.adrs[a_id] = ADR(
                title=str(a_raw.get("title", "")),
                status=str(a_raw.get("status", "proposed")),
                date=a_raw.get("date"),
                context=str(a_raw.get("context", "")),
                decision=str(a_raw.get("decision", "")),
                scope=list(a_raw.get("scope") or []),
                consequences=list(a_raw.get("consequences") or []),
            )

        return rules


# ── Module-level serialisation helpers ────────────────────────────────────────


def _elem_to_dict(elem: ArchitectureElement) -> dict[str, Any]:
    d: dict[str, Any] = {
        "type": elem.type.value,
        "description": elem.description,
    }
    if elem.c4_depth:
        d["c4_depth"] = elem.c4_depth.value
    if elem.status:
        d["status"] = elem.status.value
    if elem.facts:
        d["facts"] = elem.facts
    if elem.tags:
        d["tags"] = elem.tags
    if elem.parent:
        d["parent"] = elem.parent
    if elem.relationships:
        rels: dict[str, Any] = {}
        for rel_target, rel in elem.relationships.items():
            r: dict[str, Any] = {"type": rel.type.value}
            if rel.protocol:
                r["protocol"] = rel.protocol
            if rel.description:
                r["description"] = rel.description
            if rel.facts:
                r["facts"] = rel.facts
            rels[rel_target] = r
        d["relationships"] = rels
    return d


def _dict_to_elem(raw: dict) -> ArchitectureElement:
    raw_type = raw.get("type", "component")
    try:
        elem_type = ElementType(raw_type)
    except ValueError:
        elem_type = ElementType.component

    raw_status = raw.get("status", "implemented")
    try:
        status = ElementStatus(raw_status) if raw_status else None
    except ValueError:
        status = ElementStatus.implemented

    rels: dict[str, Relationship] = {}
    for rel_key, rel_raw in (raw.get("relationships") or {}).items():
        if not isinstance(rel_raw, dict):
            continue
        raw_rel_type = rel_raw.get("type", "sync")
        try:
            rel_type = RelationType(raw_rel_type)
        except ValueError:
            rel_type = RelationType.sync
        rels[rel_key] = Relationship(
            target=str(rel_raw.get("target", rel_key)),
            type=rel_type,
            protocol=rel_raw.get("protocol"),
            description=rel_raw.get("description"),
            facts=list(rel_raw.get("facts") or []),
        )

    return ArchitectureElement(
        type=elem_type,
        description=str(raw.get("description", "")),
        facts=list(raw.get("facts") or []),
        status=status,
        parent=raw.get("parent"),
        tags=list(raw.get("tags") or []),
        relationships=rels,
    )


def _cap_to_dict(cap: Capability) -> dict[str, Any]:
    d: dict[str, Any] = {
        "description": cap.description,
        "status": cap.status,
    }
    if cap.acceptance_criteria:
        d["acceptance_criteria"] = cap.acceptance_criteria
    if cap.facts:
        d["facts"] = cap.facts
    return d


def _tech_to_dict(item: TechStackItem) -> dict[str, Any]:
    d: dict[str, Any] = {"type": item.type}
    if item.vendor:
        d["vendor"] = item.vendor
    if item.version:
        d["version"] = item.version
    if item.facts:
        d["facts"] = item.facts
    return d


def _endpoint_to_dict(ep: ApiEndpoint) -> dict[str, Any]:
    d: dict[str, Any] = {
        "summary": ep.summary,
        "auth_required": ep.auth_required,
    }
    if ep.method:
        d["method"] = ep.method
    if ep.request_schema:
        d["request_schema"] = ep.request_schema
    if ep.response_schema:
        d["response_schema"] = ep.response_schema
    return d


def _entity_to_dict(entity: DataEntity) -> dict[str, Any]:
    d: dict[str, Any] = {"description": entity.description}
    if entity.domain:
        d["domain"] = entity.domain
    if entity.sensitivity:
        d["sensitivity"] = entity.sensitivity
    if entity.facts:
        d["facts"] = entity.facts
    if entity.attributes:
        d["attributes"] = [
            {
                "name": a.name,
                "type": a.type,
                "required": a.required,
                **({"unique": True} if a.unique else {}),
                **({"description": a.description} if a.description else {}),
            }
            for a in entity.attributes
        ]
    if entity.relationships:
        d["relationships"] = [
            {
                "target": r.target,
                "type": r.type,
                **({"description": r.description} if r.description else {}),
            }
            for r in entity.relationships
        ]
    return d


def _principle_to_dict(p: Principle) -> dict[str, Any]:
    d: dict[str, Any] = {
        "description": p.description,
        "status": p.status.value,
    }
    if p.scope:
        d["scope"] = p.scope
    if p.rationale:
        d["rationale"] = p.rationale
    if p.evidence:
        d["evidence"] = [
            {
                "pattern": ev.pattern,
                "type": ev.type,
                **({"file": ev.file} if ev.file else {}),
                **({"description": ev.description} if ev.description else {}),
            }
            for ev in p.evidence
        ]
    return d


def _invariant_to_dict(inv: Invariant) -> dict[str, Any]:
    d: dict[str, Any] = {
        "description": inv.description,
        "status": inv.status.value,
        "enforcement": inv.enforcement,
    }
    if inv.scope:
        d["scope"] = inv.scope
    if inv.evidence:
        d["evidence"] = [
            {
                "pattern": ev.pattern,
                "type": ev.type,
                **({"file": ev.file} if ev.file else {}),
                **({"description": ev.description} if ev.description else {}),
            }
            for ev in inv.evidence
        ]
    return d


def _adr_to_dict(adr: ADR) -> dict[str, Any]:
    d: dict[str, Any] = {
        "title": adr.title,
        "status": adr.status,
        "context": adr.context,
        "decision": adr.decision,
    }
    if adr.date:
        d["date"] = adr.date
    if adr.scope:
        d["scope"] = adr.scope
    if adr.consequences:
        d["consequences"] = adr.consequences
    return d


# ── Misc helpers ──────────────────────────────────────────────────────────────


def _fmt_dt(dt: datetime | str) -> str:
    if isinstance(dt, datetime):
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    return str(dt)


def _rank_by_relevance(
    items: list[tuple[str, Any]],
    query: str,
) -> list[tuple[str, Any]]:
    """Sort (key, value) pairs by simple keyword relevance to *query*."""
    query_words = set(re.split(r"\W+", query.lower()))

    def score(pair: tuple[str, Any]) -> int:
        key = pair[0].lower()
        # Count query-word hits in the key
        return sum(1 for w in query_words if w and w in key)

    return sorted(items, key=score, reverse=True)


def _file_icon(ext: str) -> str:
    icons = {
        ".py": "🐍",
        ".ts": "📘",
        ".tsx": "📘",
        ".js": "📜",
        ".jsx": "📜",
        ".go": "🐹",
        ".rs": "🦀",
        ".java": "☕",
        ".yaml": "📄",
        ".yml": "📄",
        ".json": "📋",
        ".md": "📝",
        ".sql": "🗄️",
    }
    return icons.get(ext.lower(), "📁")
