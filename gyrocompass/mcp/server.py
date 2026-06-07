"""GyroCompass MCP Server — the primary agent integration point.

This module exposes architecture state, drift reports, compliance checks, and
rule management as MCP tools that AI coding agents (Claude, Cursor, Cline, …)
can call directly from their tool loop.

Architecture
------------
The server is built on FastMCP (``mcp >= 1.21.1``) and supports two transports:

* **stdio**            — default; works with any MCP host via JSON-RPC over stdin/stdout.
* **streamable-http**  — for daemon / network deployments (e.g. VS Code extension host).

Entry points
------------
* ``create_mcp_server()``         — returns a configured ``FastMCP`` instance.
* ``start_server(repo_path, transport)`` — convenience wrapper that also calls ``run()``.

Tool inventory
--------------
1.  get_context          — full architecture context as Markdown
2.  get_file_context     — per-file architectural role
3.  get_impact           — blast-radius analysis for a set of changed files
4.  check_compliance     — check a proposed change against active rules
5.  get_drift_report     — full drift analysis (baseline vs current)
6.  add_rule             — add a new principle / invariant / ADR to .gyrorules.yaml
7.  search_specs         — full-text search over components, capabilities, rules
8.  get_status           — check whether gyrocompass is configured
9.  prepare_attestation  — generate a commit attestation YAML template
"""

from __future__ import annotations

import json
import os
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger


# ── Logging — redirect to stderr so it doesn't pollute the MCP stdio stream ───

logger.remove()
logger.add(
    sys.stderr,
    level=os.environ.get("GYRO_LOG_LEVEL", "INFO"),
    format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | gyrocompass.mcp | {message}",
)


# ── Repo resolution helpers ────────────────────────────────────────────────────


def _resolve_repo_path(override: str | None = None) -> Path:
    """Determine the target repository root.

    Priority order:
    1. Explicit *override* argument.
    2. ``GYRO_REPO_PATH`` env var.
    3. ``TARGET_REPO_PATH`` env var (from config).
    4. ``CLAUDE_PROJECT_ROOT`` / ``PWD`` env vars (set by Claude Desktop / Code).
    5. Current working directory.
    """
    candidate = (
        override
        or os.environ.get("GYRO_REPO_PATH")
        or os.environ.get("TARGET_REPO_PATH")
        or os.environ.get("CLAUDE_PROJECT_ROOT")
        or os.environ.get("PWD")
    )
    path = Path(candidate).resolve() if candidate else Path.cwd()
    if not path.exists():
        logger.warning("Repo path '{}' does not exist — falling back to CWD", path)
        path = Path.cwd()
    logger.debug("Resolved repo path: {}", path)
    return path


# ── FastMCP server factory ─────────────────────────────────────────────────────


def create_mcp_server(repo_path: str | None = None):  # -> FastMCP
    """Create and return a configured FastMCP server instance.

    This is the primary factory function.  The CLI (``gyro mcp start``) imports
    this and calls ``server.run()`` / ``server.run(transport="streamable-http")``.

    Args:
        repo_path: Optional path to the repository root.  When *None* the path
                   is resolved from environment variables or CWD.

    Returns:
        A ``FastMCP`` instance with all GyroCompass tools registered.
    """
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "The 'mcp' package (>= 1.21.1) is required.\n"
            "Install it with: pip install 'mcp>=1.21.1'"
        ) from exc

    _repo_path = _resolve_repo_path(repo_path)

    mcp = FastMCP(
        name="GyroCompass",
        instructions=textwrap.dedent(f"""\
            GyroCompass is your architecture guardrails co-pilot.

            It knows the documented architecture of the project at:
              {_repo_path}

            Use these tools BEFORE writing code to understand constraints, DURING
            development to check compliance, and AFTER changes to detect drift.

            Quick workflow:
              1. Call get_context() to understand the architecture.
              2. Call get_file_context(file_path) before editing a file.
              3. Call get_impact(changed_files=[...]) to assess blast radius.
              4. Call check_compliance(description, files) before committing.
              5. Call get_drift_report() if you suspect architectural drift.
        """),
        host=os.environ.get("GYRO_MCP_HOST", "127.0.0.1"),
        port=int(os.environ.get("GYRO_MCP_PORT", "7701")),
    )

    # ── Tool 1: get_context ───────────────────────────────────────────────────

    @mcp.tool(
        name="get_context",
        description=(
            "Returns the full architecture context for the project as formatted Markdown. "
            "Includes components, capabilities, tech stack, active rules, and API surface. "
            "Pass an optional query to focus on relevant parts. "
            "Call this first when starting work on an unfamiliar part of the codebase."
        ),
    )
    async def get_context(query: str | None = None) -> str:
        """Return full architecture context as Markdown.

        Args:
            query: Optional natural-language query to focus the output on
                   relevant components (e.g. "authentication", "payment service").
        """
        try:
            from gyrocompass.specs import SpecManager

            mgr = SpecManager(_repo_path)
            state = mgr.load_state()
            rules = mgr.load_rules()

            if state is None:
                return _not_initialized_message(_repo_path)

            return mgr.get_context_for_agent(state, rules, query=query)

        except Exception as exc:
            logger.exception("get_context failed")
            return _error_message("get_context", exc)

    # ── Tool 2: get_file_context ──────────────────────────────────────────────

    @mcp.tool(
        name="get_file_context",
        description=(
            "Returns architectural context for a specific file. "
            "Shows which component it belongs to, its relationships with other components, "
            "relevant rules, and any API endpoints it may expose. "
            "Call this before editing a file to understand its architectural role."
        ),
    )
    async def get_file_context(file_path: str) -> str:
        """Return architectural context for a specific file.

        Args:
            file_path: Path to the file (relative to repo root or absolute).
                       Examples: "src/services/auth.py", "api/routes/users.ts"
        """
        try:
            from gyrocompass.specs import SpecManager

            mgr = SpecManager(_repo_path)
            state = mgr.load_state()

            if state is None:
                return _not_initialized_message(_repo_path)

            return mgr.get_file_context(state, file_path)

        except Exception as exc:
            logger.exception("get_file_context failed for '{}'", file_path)
            return _error_message("get_file_context", exc)

    # ── Tool 3: get_impact ────────────────────────────────────────────────────

    @mcp.tool(
        name="get_impact",
        description=(
            "Blast-radius analysis for a set of changed files. "
            "Returns which architecture components are directly affected, "
            "which downstream components depend on them (and may be impacted transitively), "
            "and an overall risk rating. "
            "Pass the list of files you are about to change or have already changed."
        ),
    )
    async def get_impact(changed_files: list[str]) -> str:
        """Analyse the blast radius of a set of file changes.

        Args:
            changed_files: List of file paths that have changed or will change.
                           Use relative paths from the repo root.
                           Examples: ["src/services/auth.py", "src/models/user.py"]
        """
        try:
            from gyrocompass.specs import SpecManager

            if not changed_files:
                return "No files provided — pass a non-empty list to `changed_files`."

            mgr = SpecManager(_repo_path)
            state = mgr.load_state()

            if state is None:
                return _not_initialized_message(_repo_path)

            return mgr.get_impact_analysis(state, changed_files)

        except Exception as exc:
            logger.exception("get_impact failed")
            return _error_message("get_impact", exc)

    # ── Tool 4: check_compliance ──────────────────────────────────────────────

    @mcp.tool(
        name="check_compliance",
        description=(
            "Check whether a proposed change violates active architecture rules. "
            "Provide a plain-English description of the change and optionally the "
            "list of files involved. Returns any violations found and suggestions. "
            "Use this before committing to catch rule violations early."
        ),
    )
    async def check_compliance(
        description: str,
        files: list[str] | None = None,
    ) -> str:
        """Check a proposed change against active architecture rules.

        Args:
            description: Plain-English description of the change, e.g.
                         "Adding a new REST endpoint that skips authentication
                          for performance reasons."
            files:       Optional list of file paths involved in the change.
                         When provided, file-scoped rules are checked against
                         these files specifically.
        """
        try:
            from gyrocompass.rules import RulesEngine
            from gyrocompass.specs import SpecManager

            mgr = SpecManager(_repo_path)
            state = mgr.load_state()
            rules = mgr.load_rules()

            if state is None:
                return _not_initialized_message(_repo_path)

            engine = RulesEngine(rules, repo_path=_repo_path)
            violations = engine.check_all(state, changed_files=files)

            lines: list[str] = []
            lines.append("## Compliance Check")
            lines.append("")
            lines.append(f"**Change Description:** {description}")
            if files:
                lines.append(f"**Files:** {', '.join(f'`{f}`' for f in files[:10])}")
            lines.append("")

            if not violations:
                lines.append(
                    "**No rule violations detected.**  "
                    "The proposed change appears compliant with the current architecture rules."
                )
            else:
                blocking = [v for v in violations if v.rule_type == "invariant" and "block" in getattr(v, "evidence", "") or v.severity.value in ("critical", "high")]
                warnings = [v for v in violations if v not in blocking]

                if blocking:
                    lines.append(f"**{len(blocking)} BLOCKING violation(s) — must fix before merging:**")
                    lines.append("")
                    for v in blocking:
                        lines.append(f"### `{v.rule_id}` ({v.severity.value.upper()})")
                        lines.append(f"> {v.description}")
                        if v.file:
                            loc = f"`{v.file}`" + (f":{v.line}" if v.line else "")
                            lines.append(f"> *Location:* {loc}")
                        if v.suggested_fix:
                            lines.append(f"> *Fix:* {v.suggested_fix}")
                        lines.append("")

                if warnings:
                    lines.append(f"**{len(warnings)} warning(s) — review recommended:**")
                    lines.append("")
                    for v in warnings:
                        lines.append(f"- **`{v.rule_id}`** ({v.severity.value}): {v.description}")
                        if v.suggested_fix:
                            lines.append(f"  *Fix:* {v.suggested_fix}")

            return "\n".join(lines)

        except Exception as exc:
            logger.exception("check_compliance failed")
            return _error_message("check_compliance", exc)

    # ── Tool 5: get_drift_report ──────────────────────────────────────────────

    @mcp.tool(
        name="get_drift_report",
        description=(
            "Run a full architectural drift analysis comparing the current codebase "
            "against the documented baseline in .gyro/.gyrostate.yaml. "
            "Optionally scope the analysis to a list of changed files for faster CI runs. "
            "Returns a Markdown report with all detected deviations, their severity, "
            "and suggested fixes."
        ),
    )
    async def get_drift_report(changed_files: list[str] | None = None) -> str:
        """Run full drift analysis.

        Args:
            changed_files: Optional list of file paths that changed.  When provided,
                           file-scoped rule checks are restricted to these files,
                           making the analysis faster for CI use.
                           Leave None to run a full analysis.
        """
        try:
            from gyrocompass.drift import DriftDetector
            from gyrocompass.indexer import CodeIndexer
            from gyrocompass.specs import SpecManager

            mgr = SpecManager(_repo_path)
            baseline = mgr.load_state()
            rules = mgr.load_rules()

            if baseline is None:
                return _not_initialized_message(_repo_path)

            # Index current state
            logger.info("Indexing current codebase for drift analysis…")
            indexer = CodeIndexer(_repo_path)
            if changed_files:
                current = indexer.index_files(changed_files)
            else:
                current = indexer.index()

            detector = DriftDetector(baseline=baseline, rules=rules)
            report = detector.detect(current, changed_files=changed_files)
            return report.to_markdown()

        except Exception as exc:
            logger.exception("get_drift_report failed")
            return _error_message("get_drift_report", exc)

    # ── Tool 6: add_rule ──────────────────────────────────────────────────────

    @mcp.tool(
        name="add_rule",
        description=(
            "Add a new architecture rule to .gyro/.gyrorules.yaml. "
            "Supports three rule types: 'principle' (guidelines), 'invariant' (enforced), "
            "and 'adr' (Architecture Decision Records). "
            "The rule is immediately persisted and will be checked in future drift analyses."
        ),
    )
    async def add_rule(
        rule_type: str,
        rule_id: str,
        description: str,
        scope: list[str] | None = None,
        enforcement: str | None = None,
    ) -> str:
        """Add a rule to .gyrorules.yaml.

        Args:
            rule_type:   One of ``"principle"``, ``"invariant"``, or ``"adr"``.
            rule_id:     Unique kebab-case slug, e.g. ``"no-direct-db-access"``.
            description: Human-readable description of what the rule enforces.
            scope:       Optional list of glob patterns or directory prefixes
                         that scope the rule, e.g. ``["src/", "**/*.py"]``.
            enforcement: For invariants: ``"block"`` (default), ``"warn"``, or ``"suggest"``.
                         Ignored for principles and ADRs.
        """
        try:
            from gyrocompass.specs import SpecManager

            mgr = SpecManager(_repo_path)

            rt = rule_type.lower()
            if rt not in ("principle", "invariant", "adr"):
                return (
                    f"**Error:** Unknown rule_type `{rule_type}`.  "
                    "Must be one of: `principle`, `invariant`, `adr`."
                )

            rule_data: dict[str, Any] = {"description": description}

            if rt == "principle":
                rule_data["status"] = "active"
                rule_data["scope"] = scope or []

            elif rt == "invariant":
                rule_data["status"] = "active"
                rule_data["enforcement"] = enforcement or "block"
                rule_data["scope"] = scope or []

            elif rt == "adr":
                # ADRs need title + context + decision — use description for all three as a starter
                rule_data = {
                    "title": description[:100],
                    "status": "proposed",
                    "context": description,
                    "decision": description,
                    "scope": scope or [],
                    "consequences": [],
                }

            mgr.add_rule(rule_type=rt, rule_id=rule_id, rule_data=rule_data)

            return (
                f"**Rule added successfully.**\n\n"
                f"- **ID:** `{rule_id}`\n"
                f"- **Type:** {rt}\n"
                f"- **Description:** {description}\n"
                f"- **File:** `.gyro/.gyrorules.yaml`\n\n"
                f"The rule will be checked in the next drift analysis."
            )

        except Exception as exc:
            logger.exception("add_rule failed")
            return _error_message("add_rule", exc)

    # ── Tool 7: search_specs ──────────────────────────────────────────────────

    @mcp.tool(
        name="search_specs",
        description=(
            "Full-text search over architecture specs: components, capabilities, "
            "rules, tech stack, and API endpoints. "
            "Returns matching items with their descriptions ranked by relevance. "
            "Use this to quickly find the component or rule relevant to your task."
        ),
    )
    async def search_specs(query: str) -> str:
        """Search architecture specs.

        Args:
            query: Search terms, e.g. ``"authentication"``, ``"payment service"``,
                   ``"postgres"``.
        """
        try:
            from gyrocompass.specs import SpecManager

            if not query or not query.strip():
                return "**Error:** Provide a non-empty search query."

            mgr = SpecManager(_repo_path)
            state = mgr.load_state()
            rules = mgr.load_rules()

            if state is None:
                return _not_initialized_message(_repo_path)

            query_lower = query.lower()
            tokens = set(query_lower.split())

            lines: list[str] = []
            lines.append(f"## Search Results for `{query}`")
            lines.append("")

            # ── Architecture components ──────────────────────────────────────
            comp_hits: list[tuple[int, str, str]] = []
            for elem_id, elem in state.architecture.items():
                score = _text_score(
                    query_lower,
                    tokens,
                    " ".join([elem_id, elem.description] + elem.facts + elem.tags),
                )
                if score > 0:
                    comp_hits.append((score, elem_id, elem.description))
            comp_hits.sort(key=lambda x: x[0], reverse=True)

            if comp_hits:
                lines.append("### Components")
                lines.append("")
                for score, elem_id, desc in comp_hits[:8]:
                    lines.append(f"- **`{elem_id}`**: {desc[:120]}")
                lines.append("")

            # ── Capabilities ─────────────────────────────────────────────────
            cap_hits: list[tuple[int, str, str]] = []
            for cap_id, cap in state.capabilities.items():
                score = _text_score(
                    query_lower,
                    tokens,
                    " ".join([cap_id, cap.description] + (cap.acceptance_criteria or [])),
                )
                if score > 0:
                    cap_hits.append((score, cap_id, cap.description))
            cap_hits.sort(key=lambda x: x[0], reverse=True)

            if cap_hits:
                lines.append("### Capabilities")
                lines.append("")
                for score, cap_id, desc in cap_hits[:6]:
                    lines.append(f"- **{cap_id}** ({state.capabilities[cap_id].status}): {desc[:120]}")
                lines.append("")

            # ── Rules ────────────────────────────────────────────────────────
            rule_hits: list[tuple[int, str, str, str]] = []
            for p_id, p in rules.principles.items():
                score = _text_score(query_lower, tokens, f"{p_id} {p.description} {p.rationale or ''}")
                if score > 0:
                    rule_hits.append((score, "principle", p_id, p.description))
            for i_id, inv in rules.invariants.items():
                score = _text_score(query_lower, tokens, f"{i_id} {inv.description}")
                if score > 0:
                    rule_hits.append((score, "invariant", i_id, inv.description))
            for a_id, adr in rules.adrs.items():
                score = _text_score(query_lower, tokens, f"{a_id} {adr.title} {adr.decision} {adr.context}")
                if score > 0:
                    rule_hits.append((score, "adr", a_id, adr.title))
            rule_hits.sort(key=lambda x: x[0], reverse=True)

            if rule_hits:
                lines.append("### Rules")
                lines.append("")
                for score, rtype, r_id, rdesc in rule_hits[:8]:
                    lines.append(f"- **`{r_id}`** [{rtype}]: {rdesc[:120]}")
                lines.append("")

            # ── Tech stack ────────────────────────────────────────────────────
            ts_hits = [
                (tech_id, item)
                for tech_id, item in state.tech_stack.items()
                if query_lower in tech_id.lower()
                or (item.vendor and query_lower in item.vendor.lower())
            ]
            if ts_hits:
                lines.append("### Tech Stack")
                lines.append("")
                for tech_id, item in ts_hits[:6]:
                    vendor = f" — {item.vendor}" if item.vendor else ""
                    lines.append(f"- **{tech_id}** ({item.type}){vendor}")
                lines.append("")

            # ── API endpoints ─────────────────────────────────────────────────
            ep_hits = [
                (ep_key, ep)
                for ep_key, ep in state.surface_area.items()
                if query_lower in ep_key.lower() or query_lower in ep.summary.lower()
            ]
            if ep_hits:
                lines.append("### API Endpoints")
                lines.append("")
                for ep_key, ep in ep_hits[:6]:
                    auth = "🔒" if ep.auth_required else "🔓"
                    lines.append(f"- {auth} `{ep_key}` — {ep.summary[:80]}")
                lines.append("")

            total = len(comp_hits) + len(cap_hits) + len(rule_hits) + len(ts_hits) + len(ep_hits)
            if total == 0:
                lines.append(f"_No results found for `{query}`._")
                lines.append("")
                lines.append("Tips:")
                lines.append("- Try a shorter or more general query.")
                lines.append("- Use `get_context()` to browse the full architecture.")
            else:
                lines.append(f"_Found {total} result(s) for `{query}`._")

            return "\n".join(lines)

        except Exception as exc:
            logger.exception("search_specs failed")
            return _error_message("search_specs", exc)

    # ── Tool 8: get_status ────────────────────────────────────────────────────

    @mcp.tool(
        name="get_status",
        description=(
            "Check whether GyroCompass is configured for the current repository. "
            "Returns a JSON object with initialization status, file paths, "
            "state statistics, and available tools. "
            "Call this if you're unsure whether GyroCompass has been set up."
        ),
    )
    async def get_status() -> str:
        """Check GyroCompass configuration status.

        Returns:
            JSON string with status fields:
            - initialized: bool
            - repo_path: str
            - state_exists: bool
            - rules_exists: bool
            - config_exists: bool
            - components: int  (0 if not initialized)
            - capabilities: int
            - endpoints: int
            - last_captured: str | null
        """
        try:
            from gyrocompass.config import GYRORULES_FILE, GYROSTATE_FILE, GYROCONFIG_FILE
            from gyrocompass.specs import SpecManager

            mgr = SpecManager(_repo_path)
            initialized = mgr.is_initialized()
            state_path = _repo_path / GYROSTATE_FILE
            rules_path = _repo_path / GYRORULES_FILE
            config_path = _repo_path / GYROCONFIG_FILE

            status: dict[str, Any] = {
                "initialized": initialized,
                "repo_path": str(_repo_path),
                "state_exists": state_path.exists(),
                "rules_exists": rules_path.exists(),
                "config_exists": config_path.exists(),
                "components": 0,
                "capabilities": 0,
                "endpoints": 0,
                "data_entities": 0,
                "last_captured": None,
                "project": None,
                "serial": None,
            }

            if state_path.exists():
                try:
                    state = mgr.load_state()
                    if state:
                        status["components"] = len(state.architecture)
                        status["capabilities"] = len(state.capabilities)
                        status["endpoints"] = len(state.surface_area)
                        status["data_entities"] = len(state.data_model.entities)
                        status["project"] = state.metadata.project
                        status["serial"] = state.metadata.serial
                        captured = state.metadata.captured_at
                        status["last_captured"] = (
                            captured.isoformat()
                            if isinstance(captured, datetime)
                            else str(captured)
                        )
                except Exception:
                    pass

            if rules_path.exists():
                try:
                    rules = mgr.load_rules()
                    status["principles"] = len(rules.principles)
                    status["invariants"] = len(rules.invariants)
                    status["adrs"] = len(rules.adrs)
                except Exception:
                    pass

            if not initialized:
                status["hint"] = (
                    "Run `gyro init` in the repository root to initialise GyroCompass. "
                    "Then run `gyro analyze` to index the codebase."
                )

            return json.dumps(status, indent=2, default=str)

        except Exception as exc:
            logger.exception("get_status failed")
            return _error_message("get_status", exc)

    # ── Tool 9: prepare_attestation ───────────────────────────────────────────

    @mcp.tool(
        name="prepare_attestation",
        description=(
            "Generate a commit attestation template for the staged changes. "
            "The attestation YAML documents which architecture components are affected, "
            "confirms compliance with active rules, and includes drift score. "
            "Attach this to your commit message or PR description to signal architectural intent. "
            "Returns a pre-filled YAML template ready for human review."
        ),
    )
    async def prepare_attestation(
        staged_files: list[str],
        description: str,
    ) -> str:
        """Generate a commit attestation template.

        Args:
            staged_files: List of files staged for commit (relative paths).
            description:  One-sentence summary of what this commit does.

        Returns:
            YAML attestation template as a string, ready to be included in the
            commit message or PR description.
        """
        try:
            import yaml as _yaml

            from gyrocompass.specs import SpecManager

            mgr = SpecManager(_repo_path)
            state = mgr.load_state()
            rules = mgr.load_rules()

            if state is None:
                return (
                    "**GyroCompass not initialised.**  "
                    "Run `gyro init` and `gyro analyze` first."
                )

            # Identify affected components
            affected: list[str] = []
            for sf in staged_files:
                for elem_id in state.architecture:
                    if sf.startswith(elem_id) or elem_id in sf:
                        if elem_id not in affected:
                            affected.append(elem_id)

            if not affected:
                for sf in staged_files:
                    parent = str(Path(sf).parent)
                    for elem_id in state.architecture:
                        if parent in elem_id or elem_id in parent:
                            if elem_id not in affected:
                                affected.append(elem_id)

            # Determine rules to attest
            active_invariants = [
                i_id
                for i_id, inv in rules.invariants.items()
                if inv.status.value == "active"
            ]

            now = datetime.now(tz=timezone.utc)

            attestation: dict[str, Any] = {
                "gyrocompass_attestation": {
                    "version": "1.0",
                    "timestamp": now.isoformat(),
                    "project": state.metadata.project,
                    "state_serial": state.metadata.serial,
                    "commit": {
                        "description": description,
                        "staged_files": staged_files,
                        "file_count": len(staged_files),
                    },
                    "impact": {
                        "affected_components": affected if affected else ["(none identified — new code)"],
                        "component_count": len(affected),
                    },
                    "compliance": {
                        "invariants_checked": active_invariants,
                        "author_confirms": [
                            "No hardcoded secrets introduced",
                            "No direct database access bypassing ORM",
                            "Authentication not weakened on existing endpoints",
                            "Architecture documentation updated if structure changed",
                        ],
                        "violations": [],
                    },
                    "sign_off": {
                        "attested_by": "AI coding agent",
                        "reviewed_by": None,
                        "timestamp": now.isoformat(),
                    },
                }
            }

            yaml_str = _yaml.dump(
                attestation,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
                width=100,
            )

            return (
                "## Commit Attestation\n\n"
                "Copy this YAML into your commit message or PR description:\n\n"
                "```yaml\n"
                f"{yaml_str}"
                "```\n\n"
                "> **Review the `compliance.violations` list** and add any findings "
                "before committing.\n"
                "> Update `sign_off.reviewed_by` with your name or team."
            )

        except Exception as exc:
            logger.exception("prepare_attestation failed")
            return _error_message("prepare_attestation", exc)

    # ── Tool 10: run_audit ────────────────────────────────────────────────────

    @mcp.tool(
        name="run_audit",
        description=(
            "Runs a ship-readiness audit of the codebase and returns an "
            "architecture-aware, agent-ready punch list (Markdown). Checks for "
            "exposed secrets, PII in logs, vulnerable dependencies, injection "
            "patterns, unguarded sensitive routes, and missing tests/CI/"
            "observability. Each finding is mapped to the architecture component "
            "GyroCompass extracted, so fixes stay grounded in your system's design. "
            "Use this before shipping: run it, fix the findings, then run it again "
            "to confirm. Optionally restrict to specific scanners via `only`."
        ),
    )
    async def run_audit(only: list[str] | None = None) -> str:
        """Run the ship-readiness audit and return an architecture-aware punch list.

        Args:
            only: Optional list of scanner names to run. Any of: secrets, pii,
                  injection, auth, tests, ci, observability, dependencies.
                  Omit to run every scanner.
        """
        try:
            from gyrocompass.audit import AuditEngine

            report = AuditEngine(_repo_path).run(only=only or None)
            # Ground each finding in the extracted architecture: annotate the
            # owning component so the agent fixes with architectural context.
            comp_map = _load_component_map(_repo_path)
            if comp_map:
                for finding in report.findings:
                    if finding.file:
                        comp = _component_for_file(finding.file, comp_map)
                        if comp:
                            finding.message = f"[component: {comp}] {finding.message}"
            return report.to_markdown()

        except Exception as exc:
            logger.exception("run_audit failed")
            return _error_message("run_audit", exc)

    logger.info(
        "GyroCompass MCP server configured — repo={} tools=10",
        _repo_path,
    )
    return mcp


# ── Architecture-aware audit helpers ───────────────────────────────────────────


def _load_component_map(repo_path: Path) -> dict[str, str]:
    """Build a {file_path: component_id} map from the extracted .gyromap.yaml.

    This is what makes the audit *architecture-aware*: findings get tagged with
    the component GyroCompass extracted, not just a bare file path.
    """
    import yaml as _yaml

    from gyrocompass.config import get_map_path

    map_path = get_map_path(repo_path)
    if not map_path.exists():
        return {}
    try:
        raw = _yaml.safe_load(map_path.read_text(encoding="utf-8")) or {}
    except _yaml.YAMLError:
        return {}

    out: dict[str, str] = {}
    for key, entries in raw.items():
        if key == "metadata" or not isinstance(entries, list):
            continue
        # key looks like "architecture.<component-id>"
        component = key.split(".", 1)[1] if "." in key else key
        for entry in entries:
            f = entry.get("file") if isinstance(entry, dict) else entry
            if f:
                out[f] = component
    return out


def _component_for_file(file_path: str, comp_map: dict[str, str]) -> str | None:
    """Resolve a finding's file to its architecture component (exact or by dir)."""
    if file_path in comp_map:
        return comp_map[file_path]
    # Fall back to longest matching directory prefix among mapped files.
    best: str | None = None
    best_len = -1
    from pathlib import PurePosixPath

    target_dir = str(PurePosixPath(file_path).parent)
    for mapped_file, component in comp_map.items():
        mapped_dir = str(PurePosixPath(mapped_file).parent)
        if (target_dir == mapped_dir or target_dir.startswith(mapped_dir + "/")) and len(mapped_dir) > best_len:
            best, best_len = component, len(mapped_dir)
    return best


# ── Public start_server helper ─────────────────────────────────────────────────


def start_server(
    repo_path: str | None = None,
    transport: str = "stdio",
) -> None:
    """Create and immediately run the GyroCompass MCP server.

    This is a convenience entry point that bundles ``create_mcp_server()``
    and ``server.run()``.  For more control (e.g. running as part of a larger
    ASGI app) use ``create_mcp_server()`` directly.

    Args:
        repo_path: Path to the repository root.  Defaults to CWD / env vars.
        transport: ``"stdio"`` (default, for Claude Desktop / Cursor) or
                   ``"streamable-http"`` (for network deployments).
    """
    from gyrocompass.config import settings

    _effective_transport = transport or settings.MCP_TRANSPORT

    logger.info(
        "Starting GyroCompass MCP server — transport={} repo={}",
        _effective_transport,
        repo_path or "(auto)",
    )

    server = create_mcp_server(repo_path=repo_path)

    if _effective_transport == "streamable-http":
        host = settings.MCP_HOST
        port = settings.MCP_PORT
        logger.info("HTTP transport — listening on http://{}:{}/mcp", host, port)
        # FastMCP 1.21.1+: run(transport, mount_path) — host/port come from instance settings
        server.run(transport="streamable-http")
    else:
        server.run(transport="stdio")


# ── Private helpers ────────────────────────────────────────────────────────────


def _not_initialized_message(repo_path: Path) -> str:
    """Return a user-friendly error when .gyro/ is not initialised."""
    return textwrap.dedent(f"""\
        **GyroCompass is not initialised** for `{repo_path}`.

        Run the following commands to get started:

        ```bash
        # 1. Initialise the .gyro/ directory
        gyro init --repo "{repo_path}"

        # 2. Index the codebase (creates .gyrostate.yaml)
        gyro analyze --repo "{repo_path}"
        ```

        After initialisation, all MCP tools will be available.
    """)


def _error_message(tool_name: str, exc: Exception) -> str:
    """Format a tool error as a readable Markdown message."""
    return textwrap.dedent(f"""\
        **GyroCompass MCP error in `{tool_name}`:**

        ```
        {type(exc).__name__}: {exc}
        ```

        If this persists, check:
        - The `.gyro/` directory exists and is not corrupted.
        - GyroCompass dependencies are installed: `pip install gyrocompass`.
        - The `GYRO_REPO_PATH` environment variable points to the correct repo.
    """)


def _text_score(query_lower: str, tokens: set[str], text: str) -> int:
    """Simple relevance score: count token hits + exact-phrase bonus."""
    text_lower = text.lower()
    score = sum(1 for t in tokens if t and len(t) > 1 and t in text_lower)
    if query_lower in text_lower:
        score += 3  # exact-phrase bonus
    return score
