# Demo: GyroCompass on Meridian (a customer's codebase)

`meridian-demo/` is a realistic fintech SaaS backend (33 Python files, strict
4-layer architecture). It plays the role of a **customer who just installed
GyroCompass**. This runbook is the exact, verified flow.

## Setup (once)
```bash
GP=/Users/saurabh_yergattikar/Desktop/code_base/gyrocompass_ai_agent_guardrails/gyrocompass
source $GP/.venv/bin/activate           # puts `gyro` on PATH
cd /Users/saurabh_yergattikar/Desktop/code_base/gyrocompass_ai_agent_guardrails/meridian-demo
```
Meridian already has GyroCompass initialized + committed. To reset to that
clean state at any time: `git reset --hard HEAD && git clean -fd meridian/`.

---

## Act 1 — Immediate benefit on install (≈90s)

**1. The architecture, extracted from source:**
```bash
gyro analyze
```
→ 8 components (api, api/routes, services, db, db/repositories, clients, core,
tests), 5 capabilities, 9 API endpoints, 17 data entities, tech stack.
**Say:** "No one wrote these docs. GyroCompass read the source and built a
living model — for your team to read and your agents to work from."

**2. Ship-readiness audit — what the AI left behind:**
```bash
gyro audit
gyro audit -o markdown      # the agent-ready punch list
```
→ Finds: hardcoded Stripe key in `core/config.py`, PII (email) in
`notification_service.py`, unguarded `DELETE /accounts/{id}/delete`, vulnerable
`requests`/`pyyaml`. **Say:** "Paste this into Claude Code and it fixes them in
one loop."

---

## Act 2 — Prevention: an agent's PR gets caught (≈2 min)

Meridian's rule `no-direct-db-from-routes` (ADR-001) says edge handlers must go
through services, never touch the DB directly.

**3. An AI agent adds a "quick" bulk-export endpoint that reads the DB directly:**
```bash
cat >> meridian/api/routes/orders.py <<'PY'


from meridian.db.repositories.order_repo import list_all_orders
from meridian.db.session import get_session

def bulk_export_orders():
    """Bulk export — reads orders straight from the database."""
    return list_all_orders(get_session())
PY
git add -A
git commit -m "feat: add bulk order export endpoint"
```
→ **COMMIT BLOCKED** by the pre-commit hook:
```
🛑 rules   2 invariant violation(s)
   • no-direct-db-from-routes violated: "from meridian.db.repositories.order_repo
     import list_all_orders"  (meridian/api/routes/orders.py:71)
   • no-direct-db-from-routes violated: "from meridian.db.session import
     get_session"  (meridian/api/routes/orders.py:72)
⚠️ drift   3 event(s), score 45%
   • Undocumented relationship added: meridian/api/routes → uses_meridian_db_repositories
```
**Say:** "The agent crossed an architectural boundary. GyroCompass caught it
locally — it never reaches review. Other tools only warn; here it actually
blocks the commit."

**4. The PR-comment view (what CI posts):**
```bash
gyro drift --files meridian/api/routes/orders.py --pr 412 -o markdown
```
→ A clean drift report: 45%, 2 rule violations with file:line + fix
suggestions, 1 new undocumented relationship. This is what the GitHub Action
comments on the PR.

**5. Reset:**
```bash
git restore --staged --worktree meridian/api/routes/orders.py
```

---

## Act 3 — The agent loop (MCP)

Show `plugin/claude/CLAUDE.md` — the 10 MCP tools. **Say:** "Connected to Claude
Code, the agent calls `get_context` and `check_compliance` *before* writing
code, so most violations never happen. `run_audit` lets it audit and fix itself
mid-session. Prevention first, the pre-commit hook is the safety net."

---

## The one-liner
"Your AI builds fast. GyroCompass makes sure it builds **inside the lines you
drew** — and tells you the moment it goes outside them."
