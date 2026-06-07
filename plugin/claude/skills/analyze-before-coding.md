---
name: analyze-before-coding
description: >
  Stop and analyze before writing any code. Load the architecture, trace what
  the change touches, estimate the blast radius, and mentally simulate the system
  with the change in place. Use this skill whenever you are about to implement
  something that modifies existing behavior, touches shared components, or
  interacts with more than one part of the system.
  Triggers on implementation tasks (features, bugs, refactors). Even if the user
  says "just do it", take 60 seconds to load context first.
---

# Analyze Before Coding

When a senior engineer reads a ticket, they don't start typing. They sit and
think. That thinking looks like doing nothing, but it's three distinct cognitive
processes running simultaneously: impact loading, blast radius estimation, and
mental simulation. This skill makes those invisible processes explicit.

The goal isn't to produce a document. The goal is to build an accurate mental
model of the change before touching code, so that when you do start coding you're
making informed decisions rather than discovering problems halfway through.

---

## Step 0: Load Architecture Context with GyroCompass

Before analyzing anything manually, check whether the system's architecture,
rules, and dependency graph are available. This turns the analysis from educated
guesswork into ground-truth reasoning.

**If GyroCompass MCP tools are available**, call these tools first — in order:

### 1. `get_context(scope="architecture")`

Load the system's containers, components, relationships, and element metadata.
This gives you the map of what exists before you trace dependencies. Pay
attention to:

- Which element type is the file you're about to edit? (service, database,
  queue, external_system …)
- What does that element depend on? (its `relationships` block)
- What does the element description say about its purpose?

### 2. `get_context(scope="rules")`

Load active ADRs, principles, and invariants. These are the constraints your
change must respect. Read every active rule before forming an implementation
plan — an ADR might already have decided the approach you're about to invent.

### 3. `get_file_context(file_path="<path-to-file-you-will-edit>")`

Call this for each file you plan to touch. It returns:

- The architectural element the file belongs to
- The element's relationships (what calls it, what it calls)
- Any rules that explicitly scope to this file or component

This replaces manual `grep`-based dependency tracing with the truth.

### 4. `check_compliance(description="<your-planned-change>")`

Before writing a line of code, describe what you intend to do and run it through
the rules engine. GyroCompass will flag any planned approach that would violate
an active principle, ADR, or invariant — saving you from implementing the wrong
thing.

### 5. `get_impact()` (after staging a stub or if changes already exist)

If you have any uncommitted changes (even a stub), get the real blast radius
from the actual dependency graph. This replaces manual import tracing and tells
you exactly which downstream consumers are affected.

**If GyroCompass is not available**, proceed with manual analysis in Steps 1–3
below. The thinking process is identical; GyroCompass just gives you higher-
fidelity inputs.

---

## Step 1: Impact Loading

Before changing anything, trace what the change touches. This isn't just "which
files will I edit" — it's the full web of dependencies, consumers, and side
effects.

### Trace the Dependency Web

Starting from the component you're about to change, map outward:

- **What does this component depend on?** Database tables, external APIs, shared
  libraries, configuration values, environment variables, other services.
- **What depends on this component?** Other services that call it, UI components
  that render its output, background jobs that consume its events, tests that
  assert on its behavior.
- **What shares state with this component?** Database tables written by multiple
  services, shared caches, message queues, global configuration.

### Identify the Contracts

For each dependency boundary, identify the implicit or explicit contracts:

- **API contracts** — request/response shapes, status codes, error formats
- **Data contracts** — schema expectations, invariants, foreign key relationships
- **Behavioral contracts** — ordering guarantees, timing assumptions, idempotency
- **Performance contracts** — expected latency, throughput, resource budget

Your change must preserve these contracts, or you need to explicitly plan how
consumers will adapt.

### Ask: "What Doesn't Know About This Change?"

The most dangerous impacts are where a system depends on behavior you're about to
change, but nobody told that system. Look for:

- Hardcoded assumptions about current behavior
- Cached values that will become stale
- Background jobs running on an older understanding of the data
- Monitoring or alerting that assumes current patterns

---

## Step 2: Blast Radius Estimation

Now that you know what the change touches, estimate how bad it would be if you
got it wrong.

### Categorize the Risk

- **If this change has a bug, who is affected?**
  One user? A cohort? All users? Downstream services? External partners?
- **If this change breaks, how quickly will we know?**
  Immediately (5xx errors)? After a delay (data corruption)? Weeks later?
- **If this change breaks, how fast can we recover?**
  Instant rollback? Feature flag toggle? Database migration reversal?
- **What's the worst case?**
  Downtime? Data loss? Security breach? Financial impact? Regulatory violation?

### Calibrate Your Approach

| Blast radius | Approach |
|---|---|
| **Small** — feature-specific, easily reversible | Implement directly, test well, deploy normally |
| **Medium** — affects multiple features or teams | Feature flag, dedicated monitoring, rollback plan |
| **Large** — touches core systems, hard to reverse | Design doc, progressive delivery (canary → staged), migration plan, targeted alerts |

---

## Step 3: Mental Simulation

Walk through the system with your change in place. This is the most valuable and
most invisible part of senior engineering — running integration tests in your
head.

### Simulate the Happy Path

Trace a request end-to-end:

1. Request arrives at the entry point
2. Routing reaches your modified code
3. New logic executes
4. Dependencies (database, cache, external services) are touched
5. Response flows back to the caller
6. Side effects (events, notifications, audit logs) fire

At each step: "Does this still work? Does anything see unexpected data? Does
timing change? Does the response shape change?"

### Simulate the Failure Paths

- What if the database query times out?
- What if the external API returns a 500?
- What if the input is malformed?
- What if this code runs concurrently with itself?
- What if old and new versions coexist during a rolling deploy?
- What if the feature flag is half-rolled-out?

### Simulate the Edge Cases

- Empty inputs, null values, maximum-length strings
- First-time users vs. power users with years of data
- Month boundaries, daylight saving time transitions
- Free-tier vs. paid-tier behavior differences

---

## Output: Intent Declaration

This skill produces a brief intent declaration — share it with the user before
writing any code:

```
## Pre-Implementation Analysis

**Change**: [what you're about to do]

**Architecture context loaded**: [elements and rules retrieved from GyroCompass,
or "manual analysis" if tools unavailable]

**Touches**: [components, services, tables affected]
**Depended on by**: [what will break if this is wrong]
**Blast radius**: [small / medium / large] — [1-sentence justification]

**Rules checked** (via check_compliance):
- [rule-id]: [pass / deviation — with justification]

**Key risks identified**:
- [risk 1 and how you'll handle it]
- [risk 2 and how you'll handle it]

**Approach**: [how the blast radius calibrates your implementation strategy]
```

**Present this to the human before writing any code.** If the analysis changes
their mind about the approach, better to know now.

---

## After Implementation: Attestation

Once the work is done, call `prepare_attestation` (phase 2 — finalize) and
compare with your intent declaration:

```
## Implementation Attestation

**Planned change**: [what you said you'd do]
**Actual change**: [what you actually did — summarize the diff]
**Predicted blast radius**: [your pre-implementation estimate]
**Actual blast radius**: [from get_impact — components actually affected]
**Rules checked**: [from check_compliance — rules that applied]
**Rules followed**: [which ones were respected]
**Rules deviated from**: [any deviations, with justification]
**Surprises**: [anything discovered during implementation not in the analysis]
```

This audit trail lets the team evaluate not just what changed, but whether the
agent's analysis was accurate — which builds (or erodes) trust over time.

---

## When to Skip This

You can skip the full analysis for:

- Typo fixes, comment updates, documentation-only changes
- Adding a log line or a metric tag
- Changes isolated entirely to test files
- Changes you've already analyzed as part of a design doc

Even for these, a 10-second mental check is worthwhile: "Is there any way this
seemingly trivial change could break something?" Sometimes the answer is yes.
