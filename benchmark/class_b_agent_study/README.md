# Class B Agent Study — Token & Quality Benchmark

A runnable harness to test, **honestly**, whether giving an AI coding agent
GyroCompass's architecture context reduces tokens consumed **and** improves
output quality, versus a baseline agent that gets only the task prompt.

This is the apparatus that produces the *legitimate* version of a
"X% fewer tokens / higher quality" claim. It ships with **no result numbers**.
You run it with your own API key and budget, and the numbers come out of *your*
run — not from this repo.

> Status: scaffold. The code is complete and runnable; it simply has not been
> run yet (no API key is consumed at build time). `results.json` is created only
> after a real `--run`.

---

## Hypothesis

> **H1 (tokens):** An agent given GyroCompass architecture context (GYRO arm)
> consumes *fewer total tokens* to complete a layered-architecture coding task
> than a baseline agent (BASELINE arm).
>
> **H2 (quality):** The GYRO arm produces solutions that *respect the layered
> architecture* (api → services → db/clients) more often, and are at least as
> correct, as the BASELINE arm.

Note the two effects can pull in opposite directions: the GYRO arm pays an
up-front prompt-token cost for the injected context, but may save completion
tokens by not exploring wrong (cross-layer) approaches and not needing rework.
The study measures the *net* effect on `total_tokens`, plus quality separately.

---

## What it measures

For each task, in each arm, across N trials:

| Metric | Source |
| --- | --- |
| `prompt_tokens`, `completion_tokens`, `total_tokens` | the model `usage` object |
| deterministic compliance (pass/fail) | regex match for forbidden cross-layer imports in the output |
| judge constraint score (1–10) | LLM-as-judge: "respects the stated architecture constraint" |
| judge correctness score (1–10) | LLM-as-judge: "correct & complete for the task" |

### Aggregates and deltas

Per arm: mean total/prompt/completion tokens, deterministic compliant rate,
mean judge scores. Then the **BASELINE → GYRO deltas**:

- `percent_fewer_tokens_gyro` = `(mean_total_baseline − mean_total_gyro) / mean_total_baseline × 100`
- `compliance_lift_pct_points` = `(gyro_compliant_rate − baseline_compliant_rate) × 100`
- `judge_constraint_lift`, `judge_correctness_lift` = difference in mean judge scores

A *positive* `percent_fewer_tokens_gyro` and a *positive* compliance lift support
the hypotheses. A negative token delta is a perfectly valid (and honest) outcome —
it would mean the context cost more than it saved on this task set/model.

---

## Methodology

Two arms, identical except for context:

- **BASELINE** — system prompt + the task prompt only.
- **GYRO** — the same, plus GyroCompass's architecture context prepended to the
  system prompt. This context is built from a real `.gyro/` state when present
  (via `gyrocompass.specs.SpecManager.get_context_for_agent`, the exact content
  the MCP `get_context` tool injects), and otherwise falls back to an embedded
  architecture summary so the harness always runs.

Both arms run the **same** tasks (`tasks.yaml`). Crucially, the architectural
*constraint* for each task (the rule it must respect) is **never shown to the
agent** — it is used only by the graders. The whole point is that GyroCompass
supplies that missing context to the GYRO arm and the baseline has to infer it.

Each (task × arm) cell is run `--trials` times to average out sampling noise.

### Grading

Two independent graders, each toggleable by flag:

1. **Deterministic** (`--deterministic`, on by default). Each task lists
   `forbidden_imports` regexes that signal a layering violation (e.g.
   `from db.repositories` inside a router). Any match ⇒ non-compliant. Cheap,
   reproducible, no API cost, but coarse.
2. **LLM-as-judge** (`--judge`, off by default — it adds one API call per
   trial). A second cheap model scores the solution 1–10 on constraint
   adherence and correctness. Captures nuance the regex misses, at the cost of
   judge noise/bias.

Using both lets you cross-check: the deterministic grader catches obvious
violations objectively; the judge catches subtle ones. Disagreement between them
is itself informative.

---

## How to run

```bash
# 1. Preview the matrix — NO API key, NO openai install needed (stdlib + pyyaml).
python run_study.py --dry-run

# 2. Install deps and set your key when ready to spend budget.
pip install -r requirements.txt
export OPENAI_API_KEY=sk-...
export STUDY_MODEL=gpt-4o-mini        # optional; default gpt-4o-mini
export STUDY_JUDGE_MODEL=gpt-4o-mini  # optional

# 3. Run for real. Deterministic grader on by default; add --judge for the LLM judge.
python run_study.py --run --trials 3 --judge

# Results -> results.json + a printed summary table.
```

Useful flags: `--trials N`, `--model`, `--judge-model`, `--judge`,
`--no-deterministic`. Run `python run_study.py --help` for the full list.

If you run `--run` without `OPENAI_API_KEY`, the harness prints setup
instructions and exits cleanly. **It never makes a network call without a key,
and it never makes any network call on import or during `--dry-run`.**

### Cost estimate

The dry run prints the exact number of planned API calls:
`tasks × arms (2) × trials` agent calls, plus the same again for judge calls if
`--judge` is on. With the shipped 12 tasks, `--trials 3`, and `--judge`, that is
`12 × 2 × 3 = 72` agent calls + `72` judge calls = `144` calls. Size your budget
from that and your model's per-token price.

---

## Honest caveats

**Read these before quoting any number this harness produces.**

- **Sample size.** The shipped suite is 12 tasks. At `--trials 3` that is 36
  trials per arm — borderline. For statistically meaningful per-arm means and
  deltas, target **N ≥ 30 trials per arm** *and* enough task diversity (the dry
  run warns when N is low). Add tasks to `tasks.yaml` and/or raise `--trials`.
- **Task selection drives the result.** Results are only as representative as
  the tasks. These tasks are deliberately layered-architecture tickets where
  context *should* help; they are not a random sample of all coding work. Report
  the task set alongside any number.
- **Model dependence.** Effects vary by model. A number from `gpt-4o-mini` does
  not transfer to other models/versions. Always report the model and date.
- **Grader limitations.** The regex grader only catches the specific forbidden
  patterns enumerated per task; a novel violation can slip through. The LLM
  judge is itself noisy and can be biased toward longer answers. Treat scores as
  signals, not ground truth; spot-check the raw `output` saved in `results.json`.
- **Prompt-token confound.** The GYRO arm's prompt is longer by construction. A
  token *win* for GYRO means completion/rework savings exceeded that overhead —
  report prompt vs completion tokens separately (the harness does) so the
  mechanism is visible.
- **No cherry-picking.** Report all trials, including errors. Do not drop arms or
  tasks after seeing results. `results.json` contains every trial for audit.

A result like "GYRO used N% fewer total tokens and was M points more
constraint-compliant on this 12-task layered suite with model X on date Y,
N=K trials/arm" is defensible. Anything that hides the task set, model, or N is
not.

---

## Files

| File | Purpose |
| --- | --- |
| `run_study.py` | The harness. `--dry-run` (default, no network) / `--run`. |
| `tasks.yaml` | The ~12 layered-architecture tasks + per-task constraints + the architecture summary. |
| `requirements.txt` | `openai`, `pyyaml`, `tenacity` (only `pyyaml` needed for `--dry-run`). |
| `results.json` | Created only after a real `--run`. Not committed; never fabricated. |
