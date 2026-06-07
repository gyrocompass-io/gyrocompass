This plugin provides engineering skills and architecture context via GyroCompass.

Skills are always available. Use them when analyzing code, planning changes,
reviewing PRs, or preparing commits.

If GyroCompass MCP tools are available, use them to ground your analysis in
ground-truth architecture data — not educated guesses:

- `get_context`         — orient yourself: load the project's architecture elements,
                          design principles, active ADRs, invariants, capabilities, and
                          tech stack in a single call. Always run this first when entering
                          a codebase you haven't touched recently.

- `get_file_context`    — before editing a specific file, understand its architectural
                          role: which component it belongs to, what it depends on, and
                          what depends on it. Prevents changes that accidentally break
                          undocumented contracts.

- `get_impact`          — check the blast radius of uncommitted changes (uses git diff
                          HEAD). Returns a ranked list of affected components and
                          downstream consumers so you can calibrate how careful to be
                          before writing more code or proposing a merge.

- `check_compliance`    — validate a proposed change (or the current working tree)
                          against all active rules: principles, ADRs, and invariants.
                          Returns pass/fail per rule with a plain-language explanation.
                          Call this before every commit.

- `get_drift_report`    — retrieve the latest full drift report for the repo or a
                          specific pull request. Useful for code review and for
                          understanding what the CI drift check flagged.

- `add_rule`            — record a new architectural rule (principle, ADR, or invariant)
                          directly from a conversation. Use this when a human states a
                          design decision that should become a checked constraint, so it
                          doesn't drift back in three months.

- `search_specs`        — full-text search across the architecture state, capability
                          specs, and rules. Use this to answer questions like "does the
                          system already have a rate-limiting component?" before adding
                          a new one.

- `get_status`          — check whether GyroCompass is correctly configured in the repo:
                          state file present, rules file present, MCP server reachable,
                          indexing up-to-date. Run this if tools return unexpected errors.

- `run_audit`           — run a ship-readiness audit of the codebase and get back an
                          architecture-aware punch list: exposed secrets, PII in logs,
                          vulnerable dependencies, injection patterns, unguarded sensitive
                          routes, and missing tests/CI/observability. Each finding is tagged
                          with the architecture component it lives in. Use the audit → fix →
                          re-audit loop: run it before shipping, fix what it finds, then run
                          it again to confirm. Pass `only` to restrict to specific scanners.

- `prepare_attestation` — two-phase commit attestation. Phase 1 (draft): pass your
                          intent (what you planned to change and why) to get a draft
                          attestation object with rule checksums. Phase 2 (finalize):
                          after the change is implemented, finalize the attestation with
                          actual vs. predicted blast radius and any rule deviations.
                          The finalized attestation is stored with the commit.

If MCP tools are not connected, skills still work — they just use manual analysis.
The thinking process is the same; GyroCompass gives you higher-fidelity inputs.

Reference files in the `references/` directory explain the GyroCompass state file
formats (.gyrostate.yaml, .gyrorules.yaml, .gyromap.yaml, attestation). Consult
these when you need to understand the data model or author state files by hand.

## Quick-start: which tool to call first

| Situation                                 | First tool(s)                              |
|-------------------------------------------|--------------------------------------------|
| Starting work on a new task               | `get_context` → `get_file_context`         |
| About to write code                       | `get_impact` → `check_compliance`          |
| Reviewing a PR or someone else's code     | `get_context` → `get_impact` → `get_drift_report` |
| About to commit                           | `check_compliance` → `prepare_attestation` |
| Before shipping / "is this production-ready?" | `run_audit` → fix findings → `run_audit` again |
| Adding a new architectural decision       | `add_rule`                                 |
| Searching for an existing component       | `search_specs`                             |
| Debugging tool errors                     | `get_status`                               |
