---
name: prepare-for-commit
description: >
  Pre-commit checklist grounded in architecture rules. Run before every commit
  to verify the working tree is clean, compliant with all active rules, and
  properly attested. Triggers when the user says "commit this", "ready to
  commit", "pre-commit check", or when you are about to call git commit after
  completing an implementation task.
---

# Prepare for Commit

Before a commit lands in history, three things must be true:

1. The working tree is clean — no dead code, no secrets, no placeholder stubs
2. The change is compliant — no active rules are violated without justification
3. The change is attested — intent and impact are recorded for the audit trail

This skill enforces all three.

---

## Step 1: Working Tree Hygiene

Scan every file in `git diff HEAD` (staged and unstaged). For each file, check:

### Remove before committing

- **Unused imports** — imported but never referenced in the file
- **Commented-out code blocks** — more than 2 consecutive lines of commented
  code that aren't documentation. Temporary scaffolding must ship as code or
  not at all.
- **TODO/FIXME stubs** — placeholders that indicate unfinished work. If a TODO
  is intentional and tracked in a ticket, convert it to a comment referencing
  the ticket ID.
- **Debug print statements / console.log calls** — `print(...)`, `console.log`,
  `debugger`, `pdb.set_trace()`, `breakpoint()` left in production paths
- **Hardcoded secrets** — API keys, passwords, tokens, connection strings.
  Any string matching a secret pattern is a blocker. Use environment variables.
- **Unreachable code** — code after unconditional `return`, `raise`, or `break`

### Verify before committing

- **Empty except/catch blocks** — error handling that silently swallows
  exceptions must at minimum log the error
- **Placeholder values** — `"TODO"`, `"FIXME"`, `"changeme"`, `0` where a real
  value is required, `None` where a real default is needed
- **Test files include actual assertions** — a test with no `assert` statement
  always passes and catches nothing

Report file, line range, category, and recommended action for each finding.
Offer to fix clear-cut issues (unused imports, debug prints) automatically.
Ask before touching anything ambiguous.

---

## Step 2: Compliance Check with GyroCompass

**If GyroCompass MCP tools are available**, run these checks:

### 2a. `check_compliance(description="<summarize the diff in 2-3 sentences>")`

Pass a plain-language description of what the diff does. GyroCompass evaluates
it against every active rule: principles, ADRs, and invariants. The response
will be a pass/fail per rule with a plain-language explanation for each failure.

**Treat every rule violation as a commit blocker unless:**

1. The violation is intentional and a new ADR is being added to document the
   exception (use `add_rule` to record it before committing), or
2. The rule is `status: proposed` (not yet `active`), in which case it is
   advisory rather than blocking.

### 2b. `get_impact()`

Confirm the blast radius matches your pre-implementation estimate. If the
actual impact is significantly larger than expected, stop and run
`analyze-before-coding` again before committing.

### 2c. `get_status()`

Verify GyroCompass is healthy: state file present, rules loaded, indexing
current. A stale index means compliance results may be incomplete.

**If GyroCompass is not available**, perform a manual compliance check:

1. Re-read the `.gyro/.gyrorules.yaml` file
2. For each active rule, ask: "Does my diff violate this?"
3. Document the result for each rule in the attestation

---

## Step 3: Attestation

Every commit should have an attestation — a machine-readable record of what was
checked before the code was committed. This is how the team audits AI agent
behavior over time.

**If GyroCompass MCP tools are available:**

### Phase 1 — Draft (run before implementation, or now if skipped)

```
prepare_attestation(
  phase="draft",
  description="<what this commit does>",
  planned_files=["<list of files changed>"],
  rules_checked=["<rule IDs from check_compliance>"]
)
```

### Phase 2 — Finalize (run now, just before `git commit`)

```
prepare_attestation(
  phase="finalize",
  description="<final description of the commit>",
  actual_files=["<git diff --name-only>"],
  compliance_result="<pass | deviation>",
  deviations=["<list of rule IDs deviated from, with justification>"],
  blast_radius="<small | medium | large>",
  attestation_id="<id from phase 1, if available>"
)
```

The finalized attestation YAML is written to `.gyro/.attestation.yaml`. Include
this file in the commit (`git add .gyro/.attestation.yaml`).

**If GyroCompass is not available**, write the attestation manually:

```yaml
# .gyro/.attestation.yaml
version: "1.0"
commit_sha: null          # filled in by post-commit hook or CI
attested_at: "<ISO timestamp>"
description: "<what this commit does>"
files_changed:
  - "<path>"
rules_checked:
  - id: "<rule-id>"
    status: pass           # pass | deviation
    note: "<optional note>"
blast_radius: "<small | medium | large>"
deviations: []
```

---

## Step 4: Final Checklist

Before running `git commit`, every item below must be checked:

```
## Pre-Commit Checklist

### Hygiene
- [ ] No unused imports
- [ ] No commented-out code
- [ ] No debug print / console.log statements
- [ ] No hardcoded secrets or tokens
- [ ] No TODO/FIXME stubs without a linked ticket
- [ ] No empty catch/except blocks

### Compliance (via check_compliance)
- [ ] All active principles: PASS
- [ ] All active ADRs: PASS
- [ ] All active invariants: PASS
- [ ] Any deviations are documented with a new rule or justification comment

### Attestation
- [ ] prepare_attestation (finalize) called
- [ ] .gyro/.attestation.yaml staged for commit
- [ ] Blast radius matches pre-implementation estimate (or is explained)

### Readiness
- [ ] Tests pass locally
- [ ] No merge conflicts
- [ ] Commit message is clear and describes the "why", not just the "what"
```

A commit with any unchecked box in the Hygiene or Compliance sections is not
ready. Fix the issues first.

---

## Commit Message Format

A good commit message has three parts:

```
<type>(<scope>): <short imperative summary, ≤72 chars>

<optional body: explain WHY, not WHAT. Reference the ticket, the ADR,
or the user story that motivated this change. If a rule was intentionally
deviated from, state it here with justification.>

<optional footer: Fixes #123, Closes #456, Refs ADR-007>
```

Types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`, `perf`, `security`

Example:

```
feat(payment-service): add idempotency key validation on charge endpoint

Prevents duplicate charges when the client retries on timeout.
Follows ADR-004 (all payment mutations must be idempotent).
Idempotency keys are stored in Redis with a 24-hour TTL.

Closes #892
```
