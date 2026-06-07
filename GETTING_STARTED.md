# GyroCompass — Complete Getting-Started Guide

From `git clone` to using every capability. This is the full onboarding a team
follows after adopting GyroCompass.

> **Two repos to keep straight:**
> - **GyroCompass** — the tool you install (this repo).
> - **Your repo** — the codebase you want guardrails on (e.g. `your-app/`).

---

## Step 0 — Prerequisites
- Python **3.12+**
- Git
- (Optional) An LLM API key — works without one; needed only for richer docs.
- (Optional) Docker — only for the advanced deep-graph engine.

---

## Step 1 — Install GyroCompass

**Option A — from PyPI (simplest, once published):**
```bash
pipx install gyrocompass        # isolated, recommended
# or
pip install gyrocompass
```

**Option B — from GitHub source:**
```bash
git clone https://github.com/gyrocompass-io/gyrocompass.git
cd gyrocompass
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

Verify:
```bash
gyro --version       # → gyrocompass 0.1.0
gyro --help          # lists all commands
```

You now have 14 commands: `setup, init, analyze, drift, audit, check, verify,
attest, install-hooks, mcp, spec, memory, graph, status`.

---

## Step 2 — Onboard your repository (the fast path)

**One command does it all** — init, extract architecture, install hooks, and
show an audit summary:

```bash
cd /path/to/your-app
gyro setup
```

That's the whole onboarding. It prints what it extracted (components,
capabilities) and what the audit found, with next steps. Then commit `.gyro/`.

> Prefer to do it step by step? The individual commands below (Steps 3–6) are
> exactly what `gyro setup` runs for you.

---

## Step 3 — Extract your architecture (what `setup` runs)

```bash
gyro init                 # creates .gyro/ (config, rules, state templates)
gyro analyze --save       # extracts architecture → living docs
```

This Tree-sitter-parses your code and produces:
- **`.gyro/.gyrostate.yaml`** — living architecture: components, capabilities,
  API endpoints, data entities, tech stack.
- **`.gyro/.gyromap.yaml`** — which files implement which component.

See it any time:
```bash
gyro status               # config + architecture summary
gyro spec list            # list components
gyro spec show <component>   # details for one component
```

Re-run `gyro analyze --save` whenever you want to refresh the baseline.

### Visualize it (people like to see things)

```bash
gyro docs                 # generates + opens an interactive view in your browser
```
Produces (in `.gyro/`):
- **`architecture.html`** — a standalone, interactive system map (drag nodes,
  zoom, hover) **plus a drift-evolution timeline** showing how your architecture
  changed since GyroCompass started watching. No server — just open the file.
- **`ARCHITECTURE.md`** — renders natively on GitHub (Mermaid diagram + tables).
- **`architecture.json`** — graph nodes/edges for any tool (D3, Cytoscape, CI).

`gyro setup` already generates these for you on first run.

---

## Step 4 — Ship-readiness audit (second benefit, no setup needed)

```bash
gyro audit                # pretty table of findings
gyro audit -o markdown    # agent-ready punch list — paste into Claude Code/Cursor
gyro audit -o json        # machine-readable
gyro audit --only secrets,dependencies     # run specific scanners
gyro audit --fail-on high                   # exit non-zero (for CI gates)
```

Scans for: hardcoded secrets, PII in logs, vulnerable dependencies, SQL/eval
injection, unguarded sensitive routes, missing tests/CI/observability. Each
finding is mapped to the architecture component it lives in.

---

## Step 5 — Define your architecture rules

Edit **`.gyro/.gyrorules.yaml`**. Three rule types:
- **principles** — ongoing guidelines ("always X")
- **adrs** — point-in-time decisions with context
- **invariants** — hard limits that block ("never X")

Example (edge handlers must not touch the DB directly):
```yaml
invariants:
  no-direct-db-from-routes:
    description: Route handlers must go through services, never import the DB layer.
    status: active
    enforcement: block
    scope:
      - architecture.src/api/routes
    evidence:
      - pattern: "from app.db.repositories"
        type: code_pattern
```
Add rules from the CLI too:
```bash
gyro spec add-rule
```
See `examples/.gyrorules.yaml` for a full reference.

---

## Step 6 — Turn on local enforcement (git hooks)

```bash
gyro install-hooks
```

Installs:
- **git pre-commit hook** → runs `gyro check` and **blocks** commits that
  violate invariants or introduce blocking drift.
- **git post-commit hook** → archives the commit attestation.
- **Claude Code hooks** (`.claude/settings.json`) → feeds architectural context
  to your agent before edits; blocks edits to protected components.

Configure strictness in `.gyro/config.yaml`:
```yaml
enforcement:
  rules_mode: block          # block | warn | off
  drift_mode: warn
  block_on_severities: [critical, high]
  require_attestation: false
  protected_components: []   # e.g. ["src/payments"]
```

Test the gate manually:
```bash
gyro check --staged          # what the pre-commit hook runs
```
Bypass once if you must: `git commit --no-verify`.

---

## Step 7 — Detect drift

```bash
gyro drift                                  # full repo vs baseline
gyro drift --files src/api/routes/x.py      # scope rules to changed files
gyro drift --pr 412 -o markdown             # the PR-comment format
```
Reports added/removed components, capability regressions, undocumented
relationships (boundary crossings), and rule violations — with severity, file,
line, and fix suggestions.

---

## Step 8 — Connect your AI agent (MCP)

Start the server:
```bash
gyro mcp start         # stdio (for Claude Code / Cursor)
gyro mcp status        # connection instructions
```

Add to **Claude Code** (`~/.claude/settings.json`):
```json
{
  "mcpServers": {
    "gyrocompass": { "command": "gyro", "args": ["mcp", "start"] }
  }
}
```

Your agent now has 10 tools: `get_context`, `get_file_context`, `get_impact`,
`check_compliance`, `get_drift_report`, `add_rule`, `search_specs`,
`get_status`, `prepare_attestation`, `run_audit`. It codes *inside* your
architecture and can audit/fix itself mid-session.

---

## Step 9 — Install the agent plugin (skills)

The `plugin/` directory holds engineering skills + the MCP config.

**Claude Code:**
```bash
/plugin marketplace add gyrocompass-io/gyrocompass
/plugin install gyrocompass
```
**Cursor:** add `plugin/server.json` to `.cursor/mcp.json`.

Skills steer the agent to call `get_context` / `check_compliance` *before*
writing code — prevention, not just detection.

---

## Step 10 — Architectural memory (history over time)

Every `gyro drift` run records into a local SQLite knowledge graph
(`.gyro/memory.db`). Query it:
```bash
gyro memory status      # counts: drift events, remediation, decisions
gyro memory log         # activity feed (drift over time)
gyro memory list -t drift_event    # list nodes by type
gyro memory ready       # remediation work that's unblocked & actionable
gyro memory fix <drift_id> "Route export through OrderService"   # open a fix item
gyro memory resolve <node_id> -m "fixed in PR #418"
```

---

## Step 11 — Team-wide enforcement on PRs (CI)

Add the GitHub Action so every pull request gets a drift comment:
```yaml
# .github/workflows/gyrocompass.yml
name: GyroCompass Drift Check
on: [pull_request]
jobs:
  gyrocompass:
    uses: gyrocompass-io/gyrocompass/.github/workflows/gyrocompass-drift.yml@main
    with:
      fail_on_critical: true     # block merge on critical drift
      fail_on_high: false
    secrets: inherit             # provide OPENAI_API_KEY / ANTHROPIC_API_KEY if used
```
PRs get a posted comment with the drift report; critical issues can block merge.

---

## Step 12 — Configure your LLM (optional, any provider)

GyroCompass works with **no** key (lite descriptions). For richer output set one:
```bash
# OpenAI (default)
export OPENAI_API_KEY=sk-...
# Anthropic
export ANTHROPIC_API_KEY=sk-ant-... ; export GYRO_LLM_PROVIDER=anthropic
# Ollama — fully local / private, nothing leaves your machine
export GYRO_LLM_PROVIDER=ollama ; export OLLAMA_MODEL=llama3.2
# Your company's OpenAI-compatible endpoint
export GYRO_LLM_BASE_URL=https://llm.internal/v1 ; export GYRO_LLM_API_KEY=... ; export GYRO_LLM_MODEL=gpt-4o
```
Enhance docs with the LLM: `gyro analyze --save --llm`.

---

## Step 13 — Deep code-graph engine (advanced, optional)

Upgrades drift to **call-graph blast radius** and adds NL queries + semantic
search. Needs Docker (Memgraph + Qdrant).

```bash
pip install "gyrocompass[graph]"
docker-compose --profile graph up -d        # starts Memgraph + Qdrant
export GYRO_GRAPH_BACKEND=graph
gyro graph build                             # build the knowledge graph
gyro graph status                            # node/edge counts
gyro graph query "what calls PaymentService.charge?"   # natural-language → Cypher
gyro graph search "where is auth handled"              # semantic code search
```
When the graph is built, `gyro drift` automatically enriches findings with how
many downstream symbols a change affects.

---

## Step 14 — Self-host the dashboard (optional)

```bash
cp .env.example .env        # set your LLM key etc.
docker-compose up -d        # API on :7700, dashboard on :3000
```

---

## Daily workflow cheat-sheet

| When | Command |
|---|---|
| New repo | `gyro init && gyro analyze --save` |
| Before shipping | `gyro audit` |
| Before committing | `gyro check --staged` (auto via hook) |
| Reviewing a PR | `gyro drift --pr <n> -o markdown` |
| Refresh architecture | `gyro analyze --save` |
| Check outstanding debt | `gyro memory ready` |
| Ask about the codebase | `gyro graph query "..."` (graph mode) |

## The mental model
You draw the outlines (architecture + rules). Your AI colors them in.
GyroCompass shows you — and blocks — the moment it goes outside the lines.
