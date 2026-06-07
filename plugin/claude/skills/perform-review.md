---
name: perform-review
description: >
  Architecture-grounded code review from three expert lenses: Senior Tech Lead,
  Product Manager, and QA Engineer. Use this skill when the user wants to verify
  a feature is complete, high-quality, and safe to merge. Triggers when the user
  says things like "review this code", "is this production ready", "check this
  PR", or any request for substantive code review. Also triggers when you are
  reviewing your own implementation before reporting that you're done.
---

# Perform Review

Review the implementation through three expert lenses. Output is a unified
checklist where each item is tagged with the persona that raised it:
`[TL]`, `[PM]`, or `[QA]`.

---

## Step 0: Load Review Context with GyroCompass

A review is only as good as the standards it's measured against. Before
reviewing any code, load the team's architecture and rules so you're reviewing
against actual documented expectations — not generic best practices.

**If GyroCompass MCP tools are available**, run these in order:

### 1. `get_context(scope="architecture")`

Load the system's containers, components, and relationships. The Tech Lead
persona uses this to verify that the implementation fits existing architectural
patterns and respects component boundaries. Flag any code that:

- Crosses a boundary that should be mediated by a defined interface
- Introduces a new dependency not reflected in the architecture
- Modifies a component in a way inconsistent with its documented role

### 2. `get_context(scope="rules")`

Load ADRs, principles, and invariants. Every active rule becomes a checklist
item for the Tech Lead persona. A deviation from any rule is a blocking finding
unless the rule is explicitly superseded or the implementation includes a new
ADR documenting the exception.

### 3. `get_context(scope="capabilities")`

Load the feature's documented capabilities and acceptance criteria. The PM
persona checks that every acceptance criterion has a corresponding implementation.
A capability marked `planned` that is still unimplemented is a blocker.

### 4. `get_impact()`

Get the blast radius of the changes from the actual dependency graph. The QA
persona verifies that every affected component has test coverage proportional to
the impact. High-impact changes with thin test coverage are a blocking finding.

### 5. `get_drift_report()`

Retrieve the latest drift report for the branch or PR. Review any flagged drift
events as part of the Tech Lead findings. If the CI drift check already flagged
something, the review should acknowledge it — not re-discover it.

**If GyroCompass is not available**, review any available design docs, ticket
descriptions, and existing code conventions to establish the review baseline.

---

## Senior Tech Lead

Focus on:

- **Correctness** — does the logic handle all cases, not just the happy path?
- **Error handling** — are errors caught, logged, and surfaced appropriately?
  Silent swallowing of exceptions is always a finding.
- **Performance** — N+1 queries, unbounded loops, missing pagination, large
  in-memory allocations, synchronous calls that should be async
- **Security** — input validation, authentication/authorization checks,
  injection risks (SQL, shell, template), secret handling, sensitive data
  in logs or responses
- **Naming and organization** — clear names, appropriate abstraction level,
  not over- or under-engineered
- **Test coverage** — are the important paths tested? Do tests verify behavior,
  not implementation details? Are critical and edge-case paths covered?
- **Consistency** — does the code follow existing patterns in the codebase?
- **Rule compliance** — cross-reference every active rule from
  `get_context(scope="rules")`. Any deviation is a finding.
- **Architecture fit** — does the implementation respect the component
  boundaries and relationships from `get_context(scope="architecture")`?

---

## Product Manager

Focus on:

- **Requirements match** — does the implementation satisfy the stated ticket,
  spec, or task description?
- **Acceptance criteria** — cross-reference every criterion from
  `get_context(scope="capabilities")`. Missing criteria are blockers.
- **UX and workflow** — are there user-visible implications that weren't
  addressed? Error states, loading states, empty states?
- **Business logic edge cases** — scenarios a real user would hit that aren't
  covered by the happy path in the ticket
- **Graceful degradation** — if a dependency fails, does the user see a helpful
  message or a stack trace?
- **Discoverability** — if the feature added new functionality, is there any
  way for users to find it?

---

## QA Engineer

Focus on:

- **Untested code paths** — branches, error handlers, and edge cases with no
  corresponding test
- **Boundary conditions** — empty collections, null/None inputs, maximum-length
  strings, zero-value numerics
- **Concurrency** — race conditions, non-atomic read-modify-write sequences,
  shared mutable state
- **Test data realism** — are tests using trivial fixtures that wouldn't catch
  real production bugs?
- **Integration points** — database queries, external API calls, message queue
  interactions: are they tested at the integration level, or only mocked?
- **Regression risk** — cross-reference `get_impact()` output: every affected
  component should have a test asserting it still behaves correctly
- **Flaky test patterns** — time-dependent (`datetime.now()`), order-dependent,
  or non-deterministic test logic that will cause intermittent CI failures

---

## Output Format

```
## Code Review

### Architecture Context
[1-2 sentences: what GyroCompass tools returned, or "manual analysis" if unavailable]

### Findings

- [ ] [TL] Missing error handling in `process_payment()` — no catch for network timeouts
- [x] [TL] Rule "api-auth-required" satisfied — all new endpoints check the JWT middleware
- [ ] [PM] Acceptance criterion "user receives email on order completion" not implemented
- [x] [PM] All other acceptance criteria from capability "checkout-flow" satisfied
- [ ] [QA] No test for empty cart edge case in checkout; `CartService.calculate_total([])` not covered
- [ ] [QA] `test_refund_webhook` relies on `datetime.now()` — will flake on slow CI
- [x] [TL] Component boundaries respected — payment logic stays in payment-service

### Summary

<2-3 sentence overall assessment: what is strong, what needs attention>

### Verdict

**[Approve / Approve with nits / Request changes]**
Blocking: N | Non-blocking: N
```

Items with `[x]` pass. Items with `[ ]` need attention. Clearly separate
blocking items from non-blocking nits.

---

## Review Attestation

After completing the review, record the findings as a formal attestation:

```
## Review Attestation

**Architecture checked**: [elements loaded from get_context(scope="architecture")]
**Rules verified**: [ADRs/principles checked — pass/deviation per rule]
**Capabilities verified**: [acceptance criteria status from get_context(scope="capabilities")]
**Blast radius assessed**: [from get_impact — affected components and test coverage status]
**Drift events**: [from get_drift_report — any open drift findings]
**Verdict**: [Approve / Approve with nits / Request changes]
**Blocking findings**: [count and one-line summary each]
**Non-blocking findings**: [count and one-line summary each]
```

The review attestation is the audit record proving the implementation was
checked against the team's documented standards before merge — not just eyeballed.
