#!/usr/bin/env python3
"""Class B Agent Study — token & quality benchmark harness for GyroCompass.

This harness measures whether giving an AI coding agent GyroCompass's
architecture context (the kind of context surfaced by the MCP ``get_context`` /
``check_compliance`` tools) changes two things relative to a baseline agent that
receives only the task prompt:

  1. **Tokens consumed** — does the architecture context cost or save tokens?
  2. **Output quality** — does the agent respect the layered architecture and
     produce a more correct solution?

The study runs a fixed suite of coding tasks (``tasks.yaml``) across two arms:

  * ``BASELINE`` — the agent gets only the task prompt.
  * ``GYRO``     — the agent additionally gets the GyroCompass architecture
                   summary + rules, exactly as ``get_context`` would inject them.

Each (task, arm) pair is run ``--trials`` times. Every solution is graded by
two independent graders (either or both may be enabled):

  * a **deterministic** grader — regex match for forbidden layering imports;
  * an **LLM-as-judge** grader — a second cheap model call scoring 1-10 on
    "respects the stated architecture constraint" and "correctness".

Results are aggregated into per-arm means and the deltas between arms, written
to ``results.json``, and printed as a summary table.

Safety / honesty contract
--------------------------
* Importing this module performs **no** network calls.
* The default mode is ``--dry-run``: it prints the planned matrix (tasks x arms
  x trials, estimated API calls) and exits 0 **without** importing ``openai`` or
  touching the network. The dry run needs only the stdlib + PyYAML.
* ``--run`` performs the real study. If ``OPENAI_API_KEY`` is unset it prints
  setup instructions and exits cleanly — it never makes a network call without
  a key.
* No result numbers are fabricated anywhere. ``results.json`` exists only after
  a real ``--run``.

Run it later with your own key/budget::

    export OPENAI_API_KEY=sk-...
    pip install -r requirements.txt
    python run_study.py --run --trials 3 --judge --deterministic

Preview the matrix now (no key, no openai needed)::

    python run_study.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# ── Paths ────────────────────────────────────────────────────────────────────

HERE = Path(__file__).resolve().parent
TASKS_PATH = HERE / "tasks.yaml"
RESULTS_PATH = HERE / "results.json"

# The repo root is four levels up: benchmark/class_b_agent_study/ -> repo root.
REPO_ROOT = HERE.parent.parent

# ── Embedded fallback architecture summary ─────────────────────────────────────
# Used for the GYRO arm only when a real .gyro/ state is not present. This mirrors
# what SpecManager.get_context_for_agent() would produce from indexed code.

FALLBACK_ARCHITECTURE_SUMMARY = """\
# Architecture Context — Meridian (layered backend)

> Provided by GyroCompass. The dependency direction is strictly top-to-bottom.

## Overview
Meridian is a layered Python web service. Allowed dependency direction:

    api  ->  services  ->  db / clients

## Layers
- **api/** — FastAPI routers. Parse/validate requests, call services, shape
  responses. MUST NOT import from `db/` (repositories/models) or `clients/`.
- **services/** — Business logic and orchestration. The ONLY layer allowed to
  import from `db/` (repositories) and `clients/` (external SDKs). All money
  math, auth, and cross-cutting logic live here.
- **db/** — SQLAlchemy repositories and models. MUST NOT import `services/` or
  `api/`. Repositories only read/write; they contain no business logic.
- **clients/** — Thin wrappers over external APIs (payments, email, search).
  MUST NOT import `services/` or `api/`.

## Active rules / invariants
- Routers never construct DB sessions or call repositories directly.
- Routers never instantiate external client SDKs directly (Stripe, SendGrid,
  Elasticsearch, etc.). Those calls happen inside a service.
- Cross-cutting concerns (auth, money math, discounts, refunds) live in
  services, not routers and not repositories.
- No circular imports across layers; dependencies point downward only.
- The db/ layer never calls "up" into services/ or api/.

## How to comply
When adding an endpoint: put orchestration/business logic in a service method,
have the router call that service, and let the service call repositories and
clients. Never import `db.repositories`, `db.models`, or a client SDK from a
router. Never import `services` or `api` from the `db/` layer.
"""

# ── System prompt shared by both arms ──────────────────────────────────────────

BASE_SYSTEM_PROMPT = """\
You are a senior software engineer working in a layered Python web service
called "Meridian". Implement the requested change. Respond with the code for the
new or modified file(s) only, in fenced code blocks, each preceded by a comment
line giving the file path (e.g. `# api/routers/orders.py`). Do not include prose
explanations outside the code blocks. Write idiomatic, production-quality Python.
"""

GYRO_CONTEXT_PREAMBLE = """\
The following architecture context is provided by GyroCompass. Treat it as
authoritative for how this codebase is structured and which dependencies are
allowed. Respect the layer boundaries it describes.

"""


# ── Data model ─────────────────────────────────────────────────────────────────

ARMS = ("BASELINE", "GYRO")


@dataclass(slots=True)
class TaskSpec:
    """A single benchmark task loaded from tasks.yaml."""

    id: str
    prompt: str
    constraint_description: str
    forbidden_imports: list[str]
    layer: str


@dataclass(slots=True)
class TrialResult:
    """The outcome of one (task, arm, trial) execution."""

    task_id: str
    arm: str
    trial: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    output: str
    deterministic_compliant: bool | None = None
    judge_constraint_score: float | None = None
    judge_correctness_score: float | None = None
    error: str | None = None


@dataclass(slots=True)
class StudyConfig:
    """Resolved CLI/env configuration for a study run."""

    trials: int = 3
    model: str = field(default_factory=lambda: os.environ.get("STUDY_MODEL", "gpt-4o-mini"))
    judge_model: str = field(
        default_factory=lambda: os.environ.get("STUDY_JUDGE_MODEL", "gpt-4o-mini")
    )
    use_deterministic: bool = True
    use_judge: bool = False
    temperature: float = 0.2
    max_output_tokens: int = 1500


# ── Task loading ────────────────────────────────────────────────────────────────


def load_tasks(path: Path = TASKS_PATH) -> tuple[list[TaskSpec], str]:
    """Load tasks and the architecture summary from ``tasks.yaml``.

    Returns:
        A tuple ``(tasks, architecture_summary)``. The architecture summary in
        the YAML is a convenience copy; the GYRO arm prefers a real ``.gyro/``
        state when present (see :func:`build_gyro_context`).

    Raises:
        FileNotFoundError: if the tasks file is missing.
        ValueError: if the YAML is malformed or has no tasks.
    """
    if not path.exists():
        raise FileNotFoundError(f"tasks file not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not raw or "tasks" not in raw:
        raise ValueError(f"no 'tasks' key in {path}")

    tasks: list[TaskSpec] = []
    for entry in raw["tasks"]:
        constraint = entry.get("constraint", {}) or {}
        tasks.append(
            TaskSpec(
                id=entry["id"],
                prompt=" ".join(entry["prompt"].split()),
                constraint_description=" ".join(
                    (constraint.get("description") or "").split()
                ),
                forbidden_imports=list(constraint.get("forbidden_imports", []) or []),
                layer=constraint.get("layer", "unknown"),
            )
        )
    if not tasks:
        raise ValueError(f"tasks list in {path} is empty")

    summary = raw.get("architecture_summary", "").strip()
    return tasks, summary


# ── GYRO-arm context construction ───────────────────────────────────────────────


def build_gyro_context(yaml_summary: str) -> tuple[str, str]:
    """Build the architecture context for the GYRO arm.

    Prefers a real GyroCompass ``.gyro/`` state via
    :class:`gyrocompass.specs.SpecManager` (exactly the content the MCP
    ``get_context`` tool would inject). Falls back to the ``tasks.yaml`` summary,
    and finally to the embedded constant, so the harness is always runnable.

    Args:
        yaml_summary: The ``architecture_summary`` block from ``tasks.yaml``.

    Returns:
        A tuple ``(context_markdown, source)`` where ``source`` is one of
        ``"gyro_state"``, ``"tasks_yaml"``, or ``"embedded_fallback"`` so the
        provenance is recorded in results.
    """
    # Try the real GyroCompass state first.
    try:
        from gyrocompass.specs import SpecManager  # local import: optional dep

        mgr = SpecManager(REPO_ROOT)
        state = mgr.load_state()
        if state is not None:
            rules = mgr.load_rules()
            context = mgr.get_context_for_agent(state, rules)
            if context and context.strip():
                return context, "gyro_state"
    except Exception:
        # SpecManager unavailable or no usable state — fall through to fallback.
        pass

    if yaml_summary:
        return yaml_summary, "tasks_yaml"
    return FALLBACK_ARCHITECTURE_SUMMARY, "embedded_fallback"


def build_messages(task: TaskSpec, arm: str, gyro_context: str) -> list[dict[str, str]]:
    """Build the chat messages for a given task and arm.

    The BASELINE arm gets the base system prompt + the task. The GYRO arm gets
    the same, plus the GyroCompass architecture context prepended to the system
    prompt. The architectural *constraint* itself is never shown to either arm —
    that would defeat the purpose of the study.
    """
    if arm == "GYRO":
        system = BASE_SYSTEM_PROMPT + "\n\n" + GYRO_CONTEXT_PREAMBLE + gyro_context
    else:
        system = BASE_SYSTEM_PROMPT
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Task: {task.prompt}"},
    ]


# ── Graders ─────────────────────────────────────────────────────────────────────


def grade_deterministic(task: TaskSpec, output: str) -> bool:
    """Deterministic grader: True if the output contains NO forbidden pattern.

    Each ``forbidden_imports`` entry is treated as a regex. A single match means
    the solution violated the layering constraint.
    """
    for pattern in task.forbidden_imports:
        try:
            if re.search(pattern, output, flags=re.MULTILINE):
                return False
        except re.error:
            # A malformed pattern should not crash the study; fall back to a
            # plain substring check.
            if pattern in output:
                return False
    return True


JUDGE_SYSTEM_PROMPT = """\
You are a strict code reviewer scoring an AI-generated code solution. You are
given: (1) the task, (2) the architecture constraint the solution must respect,
and (3) the candidate solution. Score the solution on two axes from 1 to 10:

- "constraint": how well it respects the stated architecture constraint
  (10 = fully respects layer boundaries; 1 = blatantly violates them).
- "correctness": how correct and complete the solution is for the task,
  ignoring architecture (10 = correct & complete; 1 = wrong or empty).

Respond with ONLY a compact JSON object, no prose:
{"constraint": <int 1-10>, "correctness": <int 1-10>}
"""


def build_judge_messages(task: TaskSpec, output: str) -> list[dict[str, str]]:
    """Build messages for the LLM-as-judge grader."""
    user = (
        f"## Task\n{task.prompt}\n\n"
        f"## Architecture constraint (the solution must respect this)\n"
        f"{task.constraint_description}\n\n"
        f"## Candidate solution\n{output}\n"
    )
    return [
        {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def parse_judge_response(text: str) -> tuple[float | None, float | None]:
    """Extract the constraint/correctness scores from a judge response.

    Tolerant of extra prose around the JSON object. Returns ``(None, None)`` if
    nothing parseable is found.
    """
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        return None, None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None, None

    def _clamp(v: Any) -> float | None:
        try:
            return max(1.0, min(10.0, float(v)))
        except (TypeError, ValueError):
            return None

    return _clamp(data.get("constraint")), _clamp(data.get("correctness"))


# ── OpenAI execution (only reached on --run) ────────────────────────────────────


def _build_openai_caller(model: str, temperature: float, max_tokens: int):
    """Return a retrying ``(messages) -> (text, usage_dict)`` callable.

    ``openai`` and ``tenacity`` are imported here so that import of this module
    and the --dry-run path never require them.
    """
    from openai import OpenAI  # noqa: PLC0415  (intentional lazy import)
    from tenacity import (  # noqa: PLC0415
        retry,
        retry_if_exception_type,
        stop_after_attempt,
        wait_exponential,
    )

    client = OpenAI()

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    def _call(messages: list[dict[str, str]]) -> tuple[str, dict[str, int]]:
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        text = resp.choices[0].message.content or ""
        usage = resp.usage
        usage_dict = {
            "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
            "total_tokens": getattr(usage, "total_tokens", 0) or 0,
        }
        return text, usage_dict

    return _call


def run_trial(
    task: TaskSpec,
    arm: str,
    trial: int,
    gyro_context: str,
    cfg: StudyConfig,
    agent_call,
    judge_call,
) -> TrialResult:
    """Execute one (task, arm, trial): generate, then grade.

    ``agent_call`` and ``judge_call`` are the prepared OpenAI callables (or the
    judge callable may be ``None`` when ``--judge`` is off).
    """
    messages = build_messages(task, arm, gyro_context)
    try:
        output, usage = agent_call(messages)
    except Exception as exc:  # capture, don't abort the whole study
        return TrialResult(
            task_id=task.id,
            arm=arm,
            trial=trial,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            output="",
            error=f"agent call failed: {exc}",
        )

    result = TrialResult(
        task_id=task.id,
        arm=arm,
        trial=trial,
        prompt_tokens=usage["prompt_tokens"],
        completion_tokens=usage["completion_tokens"],
        total_tokens=usage["total_tokens"],
        output=output,
    )

    if cfg.use_deterministic:
        result.deterministic_compliant = grade_deterministic(task, output)

    if cfg.use_judge and judge_call is not None:
        try:
            judge_text, _ = judge_call(build_judge_messages(task, output))
            con, cor = parse_judge_response(judge_text)
            result.judge_constraint_score = con
            result.judge_correctness_score = cor
        except Exception as exc:
            result.error = (result.error or "") + f" judge call failed: {exc}"

    return result


# ── Aggregation ──────────────────────────────────────────────────────────────────


def _mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def aggregate(results: list[TrialResult], cfg: StudyConfig) -> dict[str, Any]:
    """Aggregate per-arm metrics and the BASELINE->GYRO deltas.

    Computes, per arm: mean total tokens, mean prompt/completion tokens, the
    fraction of solutions that pass the deterministic constraint check, and the
    mean judge scores. Then computes deltas: percent fewer tokens in GYRO vs
    BASELINE, and the compliance/quality lift.
    """
    per_arm: dict[str, Any] = {}
    for arm in ARMS:
        arm_results = [r for r in results if r.arm == arm and r.error is None]
        n = len(arm_results)
        det = [r.deterministic_compliant for r in arm_results if r.deterministic_compliant is not None]
        con = [r.judge_constraint_score for r in arm_results if r.judge_constraint_score is not None]
        cor = [r.judge_correctness_score for r in arm_results if r.judge_correctness_score is not None]
        per_arm[arm] = {
            "trials_counted": n,
            "trials_errored": len([r for r in results if r.arm == arm and r.error is not None]),
            "mean_total_tokens": _mean([r.total_tokens for r in arm_results]),
            "mean_prompt_tokens": _mean([r.prompt_tokens for r in arm_results]),
            "mean_completion_tokens": _mean([r.completion_tokens for r in arm_results]),
            "deterministic_compliant_rate": (sum(det) / len(det)) if det else None,
            "mean_judge_constraint": _mean(con),
            "mean_judge_correctness": _mean(cor),
        }

    base = per_arm["BASELINE"]
    gyro = per_arm["GYRO"]
    deltas: dict[str, Any] = {}

    bt, gt = base["mean_total_tokens"], gyro["mean_total_tokens"]
    if bt and gt is not None and bt > 0:
        deltas["percent_fewer_tokens_gyro"] = round((bt - gt) / bt * 100.0, 2)

    br, gr = base["deterministic_compliant_rate"], gyro["deterministic_compliant_rate"]
    if br is not None and gr is not None:
        deltas["compliance_lift_pct_points"] = round((gr - br) * 100.0, 2)

    bj, gj = base["mean_judge_constraint"], gyro["mean_judge_constraint"]
    if bj is not None and gj is not None:
        deltas["judge_constraint_lift"] = round(gj - bj, 2)

    bc, gc = base["mean_judge_correctness"], gyro["mean_judge_correctness"]
    if bc is not None and gc is not None:
        deltas["judge_correctness_lift"] = round(gc - bc, 2)

    return {
        "config": {
            "model": cfg.model,
            "judge_model": cfg.judge_model if cfg.use_judge else None,
            "trials_per_cell": cfg.trials,
            "deterministic_grader": cfg.use_deterministic,
            "judge_grader": cfg.use_judge,
        },
        "per_arm": per_arm,
        "deltas": deltas,
    }


def print_summary_table(summary: dict[str, Any]) -> None:
    """Print a human-readable summary table to stdout."""
    pa = summary["per_arm"]
    deltas = summary["deltas"]

    def fmt(v: Any) -> str:
        if v is None:
            return "—"
        if isinstance(v, float):
            return f"{v:.2f}"
        return str(v)

    print("\n" + "=" * 64)
    print("CLASS B AGENT STUDY — RESULTS")
    print("=" * 64)
    cfg = summary["config"]
    print(
        f"model={cfg['model']}  trials/cell={cfg['trials_per_cell']}  "
        f"det={cfg['deterministic_grader']}  judge={cfg['judge_grader']}"
    )
    print("-" * 64)
    header = f"{'metric':<32}{'BASELINE':>15}{'GYRO':>15}"
    print(header)
    print("-" * 64)
    rows = [
        ("mean total tokens", "mean_total_tokens"),
        ("mean prompt tokens", "mean_prompt_tokens"),
        ("mean completion tokens", "mean_completion_tokens"),
        ("compliant rate (deterministic)", "deterministic_compliant_rate"),
        ("mean judge: constraint", "mean_judge_constraint"),
        ("mean judge: correctness", "mean_judge_correctness"),
        ("trials counted", "trials_counted"),
        ("trials errored", "trials_errored"),
    ]
    for label, key in rows:
        print(f"{label:<32}{fmt(pa['BASELINE'][key]):>15}{fmt(pa['GYRO'][key]):>15}")
    print("-" * 64)
    print("DELTAS (BASELINE -> GYRO)")
    if not deltas:
        print("  (no deltas computable — enable a grader and/or run trials)")
    for k, v in deltas.items():
        print(f"  {k}: {fmt(v)}")
    print("=" * 64 + "\n")


# ── Dry run ───────────────────────────────────────────────────────────────────────


def print_dry_run(tasks: list[TaskSpec], cfg: StudyConfig, gyro_source: str) -> None:
    """Print the planned study matrix without making any API calls."""
    n_tasks = len(tasks)
    n_arms = len(ARMS)
    agent_calls = n_tasks * n_arms * cfg.trials
    judge_calls = agent_calls if cfg.use_judge else 0
    total_calls = agent_calls + judge_calls

    print("\n" + "=" * 64)
    print("CLASS B AGENT STUDY — DRY RUN (no API calls made)")
    print("=" * 64)
    print(f"tasks file          : {TASKS_PATH}")
    print(f"repo root           : {REPO_ROOT}")
    print(f"GYRO context source : {gyro_source}")
    print(f"agent model         : {cfg.model}")
    print(f"judge model         : {cfg.judge_model if cfg.use_judge else '(judge disabled)'}")
    print(f"graders             : deterministic={cfg.use_deterministic}  judge={cfg.use_judge}")
    print("-" * 64)
    print(f"tasks               : {n_tasks}")
    print(f"arms                : {n_arms}  {ARMS}")
    print(f"trials per cell     : {cfg.trials}")
    print(f"cells (task x arm)  : {n_tasks * n_arms}")
    print("-" * 64)
    print(f"estimated agent API calls : {agent_calls}")
    print(f"estimated judge API calls : {judge_calls}")
    print(f"estimated TOTAL API calls : {total_calls}")
    print("-" * 64)
    print("tasks:")
    for t in tasks:
        print(f"  - {t.id:<24} [layer={t.layer}] {len(t.forbidden_imports)} forbidden pattern(s)")
    print("-" * 64)
    if n_tasks < 30:
        print(
            f"NOTE: {n_tasks} tasks x {cfg.trials} trials. For statistically "
            "meaningful per-arm numbers aim for N>=30 trials per arm "
            "(add tasks and/or raise --trials). See README.md."
        )
    print("To execute for real: set OPENAI_API_KEY and re-run with --run")
    print("=" * 64 + "\n")


# ── Real run ──────────────────────────────────────────────────────────────────────


def run_study(tasks: list[TaskSpec], gyro_context: str, cfg: StudyConfig) -> dict[str, Any]:
    """Execute the full study against the live API and return the summary.

    Precondition: ``OPENAI_API_KEY`` is set (checked by the caller). Writes
    ``results.json`` as a side effect.
    """
    agent_call = _build_openai_caller(cfg.model, cfg.temperature, cfg.max_output_tokens)
    judge_call = (
        _build_openai_caller(cfg.judge_model, 0.0, 200) if cfg.use_judge else None
    )

    results: list[TrialResult] = []
    total_cells = len(tasks) * len(ARMS)
    cell = 0
    for task in tasks:
        for arm in ARMS:
            cell += 1
            for trial in range(1, cfg.trials + 1):
                print(
                    f"[{cell}/{total_cells}] task={task.id} arm={arm} "
                    f"trial={trial}/{cfg.trials} ...",
                    flush=True,
                )
                results.append(
                    run_trial(task, arm, trial, gyro_context, cfg, agent_call, judge_call)
                )

    summary = aggregate(results, cfg)

    payload = {
        "summary": summary,
        "trials": [
            {
                "task_id": r.task_id,
                "arm": r.arm,
                "trial": r.trial,
                "prompt_tokens": r.prompt_tokens,
                "completion_tokens": r.completion_tokens,
                "total_tokens": r.total_tokens,
                "deterministic_compliant": r.deterministic_compliant,
                "judge_constraint_score": r.judge_constraint_score,
                "judge_correctness_score": r.judge_correctness_score,
                "error": r.error,
                # Raw output is kept for auditability/spot-checking.
                "output": r.output,
            }
            for r in results
        ],
    }
    RESULTS_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nWrote results to {RESULTS_PATH}")
    return summary


# ── CLI ───────────────────────────────────────────────────────────────────────────


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    p = argparse.ArgumentParser(
        prog="run_study.py",
        description="GyroCompass Class B agent token/quality study harness.",
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the planned matrix and exit. No API calls. This is the default.",
    )
    mode.add_argument(
        "--run",
        action="store_true",
        help="Execute the study for real (requires OPENAI_API_KEY).",
    )
    p.add_argument("--trials", type=int, default=3, help="Trials per (task, arm) cell. Default 3.")
    p.add_argument("--model", default=None, help="Agent model (default $STUDY_MODEL or gpt-4o-mini).")
    p.add_argument(
        "--judge-model",
        default=None,
        help="Judge model (default $STUDY_JUDGE_MODEL or gpt-4o-mini).",
    )
    grade = p.add_argument_group("graders")
    grade.add_argument(
        "--deterministic",
        dest="deterministic",
        action="store_true",
        default=None,
        help="Enable the deterministic regex grader (on by default unless only --judge given).",
    )
    grade.add_argument(
        "--no-deterministic",
        dest="deterministic",
        action="store_false",
        help="Disable the deterministic grader.",
    )
    grade.add_argument(
        "--judge",
        action="store_true",
        help="Enable the LLM-as-judge grader (extra API call per trial).",
    )
    return p.parse_args(argv)


def resolve_config(args: argparse.Namespace) -> StudyConfig:
    """Turn parsed args into a :class:`StudyConfig`."""
    cfg = StudyConfig(trials=args.trials)
    if args.model:
        cfg.model = args.model
    if args.judge_model:
        cfg.judge_model = args.judge_model
    cfg.use_judge = bool(args.judge)
    # Deterministic grader is on by default. Only off if explicitly disabled.
    cfg.use_deterministic = True if args.deterministic is None else args.deterministic
    if not cfg.use_deterministic and not cfg.use_judge:
        # Never run with zero graders — that produces no quality signal.
        print(
            "WARNING: both graders disabled; re-enabling the deterministic "
            "grader so the run produces a quality signal.",
            file=sys.stderr,
        )
        cfg.use_deterministic = True
    return cfg


def main(argv: list[str] | None = None) -> int:
    """Entry point. Default behavior (no flags) is a safe dry run."""
    args = parse_args(argv)
    cfg = resolve_config(args)

    tasks, yaml_summary = load_tasks()
    gyro_context, gyro_source = build_gyro_context(yaml_summary)

    # Default and explicit --dry-run both take the no-network path.
    if not args.run:
        print_dry_run(tasks, cfg, gyro_source)
        return 0

    # --run path: require a key, never call the network without one.
    if not os.environ.get("OPENAI_API_KEY"):
        print(
            "OPENAI_API_KEY is not set. The study makes real API calls and will "
            "not run without it.\n\n"
            "To run the study:\n"
            "  export OPENAI_API_KEY=sk-...\n"
            "  pip install -r requirements.txt\n"
            "  python run_study.py --run --trials 3 --judge\n\n"
            "To preview the matrix without a key:\n"
            "  python run_study.py --dry-run\n",
            file=sys.stderr,
        )
        return 2

    summary = run_study(tasks, gyro_context, cfg)
    print_summary_table(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
