# Installing GyroCompass

GyroCompass is a Python CLI + MCP server. A team adopts it per-repository in
about two minutes and gets value on the **first run**.

## Requirements
- Python 3.12+
- Git
- (Optional) An LLM API key for richer descriptions — works without one.
- (Optional) Docker, only for the deep graph engine (Memgraph + Qdrant).

## 1. Install

```bash
pip install gyrocompass
# or, isolated:
pipx install gyrocompass
# or, from source:
git clone https://github.com/gyrocompass-io/gyrocompass && cd gyrocompass && pip install -e .
```

This installs the `gyro` command.

```bash
gyro --version
```

## 2. Onboard your repo — one command (immediate benefit)

```bash
cd your-repo
gyro setup                # init + extract architecture + install hooks + audit summary
```

That single command extracts your architecture, installs the enforcement
hooks, and shows what the ship-readiness audit found. Then commit `.gyro/`.

Prefer to go step by step? `gyro setup` is exactly this:

```bash
gyro init                 # creates .gyro/ (config, rules, state templates)
gyro analyze --save       # extracts your architecture from source → living docs
gyro install-hooks        # local enforcement
```

`gyro analyze` immediately gives you:
- **An extracted architecture model** — components, capabilities, API surface,
  data entities, tech stack — written to `.gyro/.gyrostate.yaml` (version it).
- **A file→component map** (`.gyro/.gyromap.yaml`).

Then, before you've written any rules, run the **ship-readiness audit**:

```bash
gyro audit                # secrets, PII in logs, vuln deps, injection, missing tests/CI
gyro audit -o markdown    # agent-ready punch list to paste into Claude Code / Cursor
```

## 3. Define your boundaries (rules)

Edit `.gyro/.gyrorules.yaml` to encode your architecture decisions — e.g.
"edge handlers never touch the database directly". See `examples/.gyrorules.yaml`.

## 4. Turn on enforcement

```bash
gyro install-hooks        # git pre-commit gate + Claude Code PreToolUse/PostToolUse hooks
```

Now any commit that violates an invariant is **blocked locally**, before it
ever reaches review. Tune strictness in `.gyro/config.yaml` under `enforcement:`.

## 5. Connect your coding agent (MCP)

Add GyroCompass as an MCP server so Claude Code / Cursor work from your
architecture at planning time:

```json
// ~/.claude/settings.json
{
  "mcpServers": {
    "gyrocompass": {
      "command": "gyro",
      "args": ["mcp", "start"]
    }
  }
}
```

Your agent now has `get_context`, `get_impact`, `check_compliance`,
`get_drift_report`, `run_audit`, and more — grounded in your real architecture.

## 6. Catch drift on every PR (CI)

Add the GitHub Action so every pull request gets an architectural-drift comment:

```yaml
# .github/workflows/gyrocompass.yml
name: GyroCompass Drift Check
on: [pull_request]
jobs:
  gyrocompass:
    uses: gyrocompass-io/gyrocompass/.github/workflows/gyrocompass-drift.yml@main
    with:
      fail_on_critical: true
    secrets: inherit
```

## What you get, when
| Moment | Benefit |
|---|---|
| `gyro analyze` | Instant architecture docs + diagram data |
| `gyro audit` | Security/ship-readiness punch list |
| `gyro install-hooks` | Bad commits blocked locally |
| MCP connected | Agents code inside your architecture |
| Pull request | Drift report comment + optional merge block |

## Local-first / private
Set `GYRO_LLM_PROVIDER=ollama` to run fully offline — no code leaves your machine.
GyroCompass works with no LLM key at all (descriptions are just less verbose).
