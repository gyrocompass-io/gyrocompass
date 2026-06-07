# GyroCompass — Demo Runbook

A reliable, rehearsed script for demoing GyroCompass live. Everything here runs
on the **lite path** — 100% GyroCompass's own code, zero external engine, no
Docker, no API keys required.

## One-time setup (before the demo)

```bash
cd gyrocompass
uv venv --python 3.12 .venv          # if not already created
uv pip install --python .venv/bin/python -e .   # installs the `gyro` command
source .venv/bin/activate            # so `gyro` is on PATH for the demo
gyro --version                       # should print: gyrocompass 0.1.0
```

> If `gyro` isn't found during the demo, run commands as
> `.venv/bin/gyro …` — the git hooks bake in the venv interpreter at install
> time, so they work regardless of PATH.

## One-command demo seed

Run this to create a fresh, realistic sample repo wired for the demo:

```bash
bash scripts/demo_seed.sh /tmp/gyro-demo
cd /tmp/gyro-demo
```

This produces a 3-layer app (`api → services → db`) already initialized with a
baseline and a layering invariant (`api must not import db directly`).

---

## The demo flow (≈5 minutes)

### 1. Show the living architecture
```bash
gyro status                 # config + architecture summary
gyro analyze --save         # re-index; shows components, tech stack, capabilities
```
**Talking point:** "This is extracted from the actual source with Tree-sitter —
not hand-written docs that go stale."

### 2. The guardrail blocks a bad commit (the headline)
```bash
# An agent/dev takes a shortcut: API imports the DB layer directly
printf 'from src.db.conn import connect\ndef ep(uid): return connect()\n' > src/api/routes.py
git add -A
git commit -m "shortcut: api talks to db directly"     # ← BLOCKED
```
**Talking point:** "The commit is refused — the pre-commit hook ran our
enforcement gate and caught a layering violation with the exact file and line.
This is real enforcement, not an advisory comment."

### 3. Fix it → commit passes
```bash
printf 'from src.services.users import get_user\ndef ep(uid): return get_user(uid)\n' > src/api/routes.py
git add -A
git commit -m "api routes through service layer"        # ← SUCCEEDS
```

### 4. Drift detection + memory
```bash
mkdir -p src/analytics && echo 'def track(e): pass' > src/analytics/track.py
gyro drift                  # detects the undocumented component
gyro memory status          # it was auto-recorded into architectural memory
gyro memory log             # activity feed — drift over time
gyro memory ready           # actionable remediation work
```
**Talking point:** "Every drift event is remembered as a queryable history —
so you can see how the architecture evolves and what debt is outstanding."

### 5. Audit before you ship (the AI-built-app story)
```bash
gyro audit                  # full ship-readiness scan (pretty table)
gyro audit -o markdown      # the agent-ready punch list
```
**Talking point:** "Your AI built it fast — this checks it built it *safely*.
Secrets, PII in logs, vulnerable deps, SQL injection, missing tests/CI — written
as a punch list you paste straight into Claude Code or Cursor to fix in one
loop." (Use a sample app with issues — see `scripts/demo_vuln_app.sh`.)

### 6. Agent integration (MCP)
```bash
gyro mcp status             # shows the MCP connection instructions
```
Show `plugin/claude/CLAUDE.md` and the 9 MCP tools (`get_context`,
`get_impact`, `check_compliance`, …). **Talking point:** "Claude Code / Cursor
call these live so the agent codes *inside* your architecture from the start."

---

## Optional: the deep graph engine (needs Docker)

Only if you want to show call-graph blast radius. Requires the `graph` extra +
a Memgraph container.

```bash
uv pip install --python .venv/bin/python -e ".[graph]"
docker run -d --rm --name gyro-memgraph -p 7687:7687 memgraph/memgraph-mage:latest
export GYRO_GRAPH_BACKEND=graph
gyro graph build
gyro graph status
docker stop gyro-memgraph        # cleanup
```

## Reset between rehearsals
```bash
rm -rf /tmp/gyro-demo            # then re-run the seed script
```

## If something goes wrong
- **`gyro` not found** → use `.venv/bin/gyro` or `source .venv/bin/activate`.
- **Hook didn't block** → check `.git/hooks/pre-commit` exists and
  `GYRO_PY=` points at a python that has gyrocompass; re-run `gyro install-hooks`.
- **Drift shows 0%** → drift is directory-granular in lite mode; add/remove a
  whole subdirectory (a component), not just edit a file.
