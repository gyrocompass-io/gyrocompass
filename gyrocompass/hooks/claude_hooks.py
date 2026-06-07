"""Claude Code hook handlers for GyroCompass.

These run inside the agent's tool loop (configured in .claude/settings.json):

  • PreToolUse  (Edit|Write|MultiEdit) — before the agent edits a file, inject
    the file's architectural role + applicable rules so the agent stays in
    bounds. If the file belongs to a protected component, optionally BLOCK.

  • PostToolUse (Edit|Write|MultiEdit) — after an edit, remind the agent to run
    `gyro check` / refresh the attestation before committing.

Hook protocol (Claude Code):
  - stdin  : JSON event payload ({tool_name, tool_input{file_path,...}, ...})
  - stdout : text shown to the agent as additional context
  - exit 0 : allow
  - exit 2 : block; stderr is shown to the agent as the block reason

Run via:  python -m gyrocompass.hooks.claude_hooks pre|post
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _read_event() -> dict:
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, ValueError):
        return {}


def _extract_file_path(event: dict) -> str | None:
    tool_input = event.get("tool_input") or {}
    # Edit/Write use file_path; MultiEdit too. Some tools nest differently.
    return (
        tool_input.get("file_path")
        or tool_input.get("path")
        or tool_input.get("notebook_path")
    )


def _repo_root() -> Path:
    from gyrocompass import gitutils

    try:
        if gitutils.is_git_repo():
            return gitutils.repo_root()
    except Exception:
        pass
    return Path.cwd()


def _relative(file_path: str, repo: Path) -> str:
    try:
        return str(Path(file_path).resolve().relative_to(repo))
    except ValueError:
        return file_path


def pre_tool_use() -> int:
    """Inject architectural context before an edit; block protected components."""
    event = _read_event()
    file_path = _extract_file_path(event)
    if not file_path:
        return 0

    repo = _repo_root()
    rel = _relative(file_path, repo)

    try:
        from gyrocompass.config import get_state_path
        from gyrocompass.enforce import EnforcementEngine

        state_path = get_state_path(repo)
        if not state_path.exists():
            return 0  # not initialized — stay quiet

        import yaml

        from gyrocompass.models import ArchitectureState
        from gyrocompass.specs import SpecManager

        state = ArchitectureState.model_validate(
            yaml.safe_load(state_path.read_text(encoding="utf-8"))
        )
        rules = _load_rules(repo)
        config = EnforcementEngine(repo).config

        # Which component owns this file?
        owning = _owning_component(rel, repo)

        # Build the context message
        ctx = SpecManager(repo).get_file_context(state, rel)

        relevant_rules = _rules_for_component(rules, owning)

        # Protected-component gate
        if owning and owning in config.protected_components:
            msg = (
                f"⚠️  GyroCompass: `{rel}` belongs to PROTECTED component "
                f"`{owning}`.\n\n{ctx}\n"
            )
            if relevant_rules:
                msg += "\nRules that apply:\n" + "\n".join(f"  • {r}" for r in relevant_rules)
            msg += (
                "\n\nThis component is protected. Confirm the change respects its "
                "boundaries and rules before proceeding."
            )
            # Block so the agent must consciously re-issue with awareness.
            print(msg, file=sys.stderr)
            return 2

        # Non-protected: inject context as helpful guidance (non-blocking)
        if relevant_rules or owning:
            print(f"🧭 GyroCompass context for `{rel}`:\n{ctx}")
            if relevant_rules:
                print("Applicable rules:\n" + "\n".join(f"  • {r}" for r in relevant_rules))
        return 0
    except Exception as exc:  # never break the agent loop on hook failure
        print(f"(gyrocompass pre-hook skipped: {exc})", file=sys.stderr)
        return 0


def post_tool_use() -> int:
    """Remind the agent to verify before committing."""
    event = _read_event()
    file_path = _extract_file_path(event)
    if not file_path:
        return 0

    repo = _repo_root()
    from gyrocompass.config import get_state_path

    if not get_state_path(repo).exists():
        return 0

    rel = _relative(file_path, repo)
    print(
        f"🧭 GyroCompass: edited `{rel}`. Before committing, run "
        f"`gyro check` to verify no rules/drift are violated, and refresh the "
        f"attestation with `gyro attest` if enforcement requires it."
    )
    return 0


# ── Helpers ───────────────────────────────────────────────────────────────


def _load_rules(repo: Path):
    import yaml

    from gyrocompass.config import get_rules_path
    from gyrocompass.models import Rules

    path = get_rules_path(repo)
    if not path.exists():
        return Rules()
    return Rules.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


def _owning_component(rel_path: str, repo: Path) -> str | None:
    """Find which architecture element a file maps to, via .gyromap.yaml."""
    import yaml

    from gyrocompass.config import get_map_path

    map_path = get_map_path(repo)
    if not map_path.exists():
        return None
    try:
        gmap = yaml.safe_load(map_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return None
    for key, entries in gmap.items():
        if not key.startswith("architecture."):
            continue
        for entry in entries or []:
            if isinstance(entry, dict) and entry.get("file") == rel_path:
                return key.split(".", 1)[1].split(".")[0]
    return None


def _rules_for_component(rules, component: str | None) -> list[str]:
    if not component:
        return []
    out = []
    scope_key = f"architecture.{component}"
    for name, inv in rules.invariants.items():
        if any(scope_key in s for s in inv.scope):
            out.append(f"[invariant] {name}: {inv.description}")
    for name, pr in rules.principles.items():
        if any(scope_key in s for s in pr.scope):
            out.append(f"[principle] {name}: {pr.description}")
    return out


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    mode = argv[0] if argv else "pre"
    if mode in ("pre", "pre_tool_use", "PreToolUse"):
        return pre_tool_use()
    if mode in ("post", "post_tool_use", "PostToolUse"):
        return post_tool_use()
    print(f"Unknown hook mode: {mode}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
