<p align="center">
  <img src="assets/logo.svg" alt="GyroCompass" width="96" height="96"/>
</p>

<h1 align="center">GyroCompass</h1>

<p align="center"><b>AI writes 90% of your code & you're shipping fast.<br/>Don't fly blind — keep architectural guardrails.</b></p>

<p align="center">
1000s of PRs later — does your architecture still make sense? Or has it become spaghetti?<br/>
GyroCompass reads your living architecture from source, blocks drift the moment a commit crosses a<br/>
boundary, and keeps Claude Code, Cursor, and every AI agent oriented — so your system stays coherent<br/>
no matter how fast you ship.
</p>

<p align="center">
  <a href="https://gyrocompass.vercel.app"><b>🌐 gyrocompass.vercel.app</b></a>
</p>

<p align="center">
<a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-1877f2.svg" alt="MIT License"/></a>
<a href="https://python.org"><img src="https://img.shields.io/badge/python-3.12+-1877f2.svg" alt="Python 3.12+"/></a>
<img src="https://img.shields.io/badge/works%20with-Claude%20Code%20·%20Cursor%20·%20Codex%20·%20any%20MCP%20agent-0f2148.svg" alt="Works with"/>
<img src="https://img.shields.io/badge/status-launching%20soon-1877f2.svg" alt="Status"/>
</p>

---

## What is GyroCompass?

AI coding agents (Claude Code, Cursor, Windsurf, Aider, Copilot) generate code faster than any human can review. GyroCompass is the **architectural source of truth** that keeps your system consistent as agents fill in the details.

It automatically extracts your living architecture from source code, detects drift when AI-generated changes deviate from your documented design, and feeds real-time context back to agents via MCP — so they make better decisions from the start.

**Think of it like a coloring book.** You designed the outlines. GyroCompass makes sure the agent colors inside the lines.

---

## Features

| Feature | Description |
|---|---|
| **Architecture Discovery** | Automatically extracts components, containers, relationships, data models, and API endpoints from code using Tree-sitter |
| **Living Architecture State** | `.gyro/.gyrostate.yaml` stays current with every commit — never stale docs |
| **Drift Detection** | PRs get instant drift analysis: components added without documentation, capability regressions, rule violations |
| **MCP Server** | Feeds live architecture context to Claude Code, Cursor, Windsurf, or any MCP-compatible agent |
| **Rules Engine** | Define principles, invariants, and ADRs in `.gyro/.gyrorules.yaml` — violations block PRs |
| **GitHub Action** | Drop-in drift check that comments on every PR with architectural impact analysis |
| **Local-First** | Works 100% offline with Ollama — no data leaves your machine |
| **Any LLM** | OpenAI, Anthropic, Ollama, or your company's custom LLM endpoint |
| **Plugin Ecosystem** | Engineering skills for Claude Code (analyze-before-coding, perform-review, prepare-for-commit) |

---

## Quick Start

### 1. Install

```bash
pip install gyrocompass
# or
pipx install gyrocompass
```

### 2. Onboard your repo — one command

```bash
cd my-project
gyro setup
```

`gyro setup` does everything: creates `.gyro/`, extracts your architecture with
Tree-sitter, installs the enforcement hooks, and prints a ship-readiness audit
summary. Then commit `.gyro/`.

> Step by step instead? `gyro init` → `gyro analyze --save` → `gyro install-hooks`.

### 3. Connect to Your Agent (MCP)

```bash
gyro mcp start
```

Then add to Claude Code's `~/.claude/settings.json`:
```json
{
  "mcpServers": {
    "gyrocompass": {
      "command": "gyro",
      "args": ["mcp", "start"],
      "env": {
        "OPENAI_API_KEY": "your-key"
      }
    }
  }
}
```

Or for Cursor, add `plugin/server.json` to your `.cursor/mcp.json`.

### 4. Check Drift

```bash
gyro drift
```

---

## LLM Configuration

GyroCompass works with any LLM provider. Set one of:

```bash
# OpenAI (default)
export OPENAI_API_KEY=sk-...

# Anthropic Claude
export ANTHROPIC_API_KEY=sk-ant-...
export GYRO_LLM_PROVIDER=anthropic

# Ollama (local, privacy-first — zero data leaves your machine)
export GYRO_LLM_PROVIDER=ollama
export OLLAMA_BASE_URL=http://localhost:11434
export OLLAMA_MODEL=llama3.2

# Your company's LLM endpoint (OpenAI-compatible)
export GYRO_LLM_BASE_URL=https://llm.company.internal/v1
export GYRO_LLM_API_KEY=internal-key
export GYRO_LLM_MODEL=gpt-4o
```

---

## GitHub Action

Add architectural drift detection to every PR in 3 lines:

```yaml
# .github/workflows/drift-check.yml
name: Architecture Drift Check
on: [pull_request]
jobs:
  drift:
    uses: gyrocompass-io/gyrocompass/.github/workflows/gyrocompass-drift.yml@main
    secrets:
      OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
      # Or: ANTHROPIC_API_KEY, GYRO_LLM_BASE_URL + GYRO_LLM_API_KEY
```

PRs automatically get comments like:

```
## GyroCompass — Architectural Drift Report

Drift Score: 23%  |  Issues: 2 (1 high, 1 medium)

🟠 High (1)
Rule Violation (rule_violation)
> Payment flow bypasses SQS queue — direct DB write detected in checkout.ts:84
> Element: payment-service
> Suggestion: Route writes through PaymentService.processAsync()

🟡 Medium (1)
Undocumented Component (component_added)
> New component analytics-tracker detected in src/analytics/ but not in .gyrostate.yaml
> Suggestion: Run `gyro analyze --save` to document this component
```

---

## Architecture State Format

GyroCompass stores state in human-readable, git-diffable YAML:

```yaml
# .gyro/.gyrostate.yaml
metadata:
  version: "1.0"
  project: my-saas-app

# ── Architecture ──────────────────────────────────────────────────
architecture:
  api-server:
    type: container
    description: FastAPI REST server handling all business logic
    facts:
      - Requires authentication on all non-public endpoints
      - Uses SQLAlchemy for all database access
    relationships:
      postgres:
        type: sync
        protocol: TCP/PostgreSQL
        description: Primary data store

# ── Capabilities ──────────────────────────────────────────────────
capabilities:
  user-authentication:
    description: User signup, login, session management
    status: active
    acceptance_criteria:
      - OAuth via Google and GitHub
      - Session tokens expire after 24h

# ── Rules ─────────────────────────────────────────────────────────
# Defined in .gyro/.gyrorules.yaml
```

---

## MCP Tools

When GyroCompass is connected as an MCP server, your agents have access to:

| Tool | What it does |
|---|---|
| `get_context` | Full architecture overview — components, relationships, capabilities, rules |
| `get_file_context` | Which component owns this file and what rules apply |
| `get_impact` | Blast radius of your uncommitted changes |
| `check_compliance` | Does your plan violate any rules? |
| `get_drift_report` | Full drift analysis against documented architecture |
| `add_rule` | Add a principle, invariant, or ADR |
| `search_specs` | Search components, capabilities, rules by keyword |
| `prepare_attestation` | Generate a commit attestation documenting what changed |
| `get_status` | GyroCompass configuration status |

---

## Self-Hosting

```bash
# Clone and start all services
git clone https://github.com/gyrocompass-io/gyrocompass
cd gyrocompass
cp .env.example .env
# Edit .env with your LLM API key
docker-compose up -d

# API: http://localhost:7700
# Dashboard: http://localhost:3000
```

---

## Supported Languages

| Language | Parsing | API Detection | Data Models |
|---|---|---|---|
| Python | ✅ Tree-sitter | ✅ FastAPI, Flask, Django | ✅ Pydantic, SQLAlchemy |
| TypeScript/JavaScript | ✅ Tree-sitter | ✅ Express, Next.js | ✅ TypeScript interfaces |
| Go | ✅ Tree-sitter | ✅ net/http, Gin, Echo | ✅ Structs |
| Rust | ✅ Tree-sitter | ✅ Axum, Actix | ✅ Structs, Enums |
| Java | ✅ Tree-sitter | ✅ Spring MVC | ✅ Classes |
| Ruby | 🔜 Planned | 🔜 Planned | 🔜 Planned |

---

## Why GyroCompass

| | GyroCompass | Closed-source SaaS alternatives |
|---|---|---|
| License | MIT (open source) | Proprietary SaaS |
| Self-host | ✅ Yes | ❌ Cloud only |
| Local/Offline mode | ✅ Ollama support | ❌ Typically not |
| Custom LLM endpoint | ✅ Any OpenAI-compatible | ❌ Usually locked |
| Price | Free | Paid / per-seat |
| Source available | ✅ Full source | ❌ No |
| Architecture discovery | ✅ | Varies |
| MCP server | ✅ | Varies |
| Drift detection | ✅ | Varies |
| Rules engine | ✅ | Varies |
| PR comments | ✅ GitHub Action | Varies |
| Dashboard | ✅ | Varies |

---

## Contributing

Contributions are welcome! Areas where help is needed:

- **Language parsers** — Add Ruby, Kotlin, Swift, PHP
- **Framework detection** — Extend tech stack detection rules
- **Rules library** — Contribute common rules for popular stacks
- **Frontend** — Improve the dashboard

```bash
git clone https://github.com/gyrocompass-io/gyrocompass
cd gyrocompass
uv install
uv run pytest
```

---

## License

MIT — free for personal and commercial use.

---

_Built for teams who refuse to ship blind._
