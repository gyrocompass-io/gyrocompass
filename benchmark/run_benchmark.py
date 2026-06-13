#!/usr/bin/env python3
"""GyroCompass — Class A benchmark (detection + performance).

Measures, with REAL labeled data and no external API calls:

  1. Audit detection — precision / recall / F1 / false-positive-rate across
     secrets, PII-in-logs, injection, missing-auth, and vulnerable-deps, using
     realistic positive cases AND realistic decoys (clean code that superficially
     resembles a violation — env-var usage, parameterized SQL, placeholders, …).
  2. Rule / scope precision — does the rules engine flag genuine layering
     violations while correctly NOT flagging the same pattern in an allowed layer?
  3. Indexing performance — files/sec and components extracted across real repos.

Every secret-like test string is assembled at runtime (string concatenation) so
this committed file contains no literal credential pattern (keeps it past secret
scanners) while the engine still sees the full string under test.

Run:  .venv/bin/python benchmark/run_benchmark.py
Outputs: benchmark/BENCHMARK.md  and  benchmark/results.json
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

# ── locate package ─────────────────────────────────────────────────────────
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gyrocompass.audit import scanners  # noqa: E402


# ── Case model ───────────────────────────────────────────────────────────────


@dataclass
class AuditCase:
    name: str
    category: str            # secrets | pii | injection | auth | dependencies
    files: dict              # {relpath: content}
    expect_flag: bool        # True = a violation that SHOULD be detected
    note: str = ""


# Map category → scanner function
_SCANNER = {
    "secrets": scanners.scan_secrets,
    "pii": scanners.scan_pii_logs,
    "injection": scanners.scan_injection,
    "auth": scanners.scan_auth,
    "dependencies": scanners.scan_dependencies,
}


# ── Build labeled audit corpus (secret-like strings assembled at runtime) ─────


def _audit_corpus() -> list[AuditCase]:
    AKIA = "AKIA" + "IOSFODNN7" + "EXAMPLE"          # AWS-shaped, not real
    STRIPE = "sk_li" + "ve_51H8xExampleKeyAbc123Def456Ghi"
    GH = "ghp_" + "Abc123Def456Ghi789Jkl012Mno345Pqr678"
    OPENAI = "sk-" + "proj-" + "Abc123Def456Ghi789Jkl012Mno345Pq"
    HIGH_ENTROPY = "8Fk2pLm9Qx7vRt3Wz6Yb1Nc4Hd0Sj5Ag"

    cases: list[AuditCase] = []

    # ── SECRETS — should flag ────────────────────────────────────────────────
    cases += [
        AuditCase("aws-key", "secrets",
                  {"cfg.py": f'AWS_ACCESS_KEY = "{AKIA}"\n'}, True),
        AuditCase("stripe-live", "secrets",
                  {"pay.py": f'STRIPE_KEY = "{STRIPE}"\n'}, True),
        AuditCase("github-token", "secrets",
                  {"ci.py": f'TOKEN = "{GH}"\n'}, True),
        AuditCase("openai-key", "secrets",
                  {"llm.py": f'client = OpenAI(api_key="{OPENAI}")\n'}, True),
        AuditCase("generic-entropy-secret", "secrets",
                  {"s.py": f'api_key = "{HIGH_ENTROPY}"\n'}, True),
    ]
    # ── SECRETS — decoys (should NOT flag) ───────────────────────────────────
    cases += [
        AuditCase("env-var-usage", "secrets",
                  {"a.py": 'STRIPE_KEY = os.environ["STRIPE_KEY"]\n'}, False,
                  "reads from env, not hardcoded"),
        AuditCase("placeholder-key", "secrets",
                  {"b.py": 'API_KEY = "your-api-key-here"\n'}, False,
                  "obvious placeholder"),
        AuditCase("getenv-default", "secrets",
                  {"c.py": 'token = os.getenv("TOKEN", "")\n'}, False),
        AuditCase("short-config-value", "secrets",
                  {"d.py": 'MODE = "production"\nTIMEOUT = "30"\n'}, False,
                  "low-entropy config strings"),
    ]

    # ── PII IN LOGS — should flag ────────────────────────────────────────────
    cases += [
        AuditCase("email-in-log", "pii",
                  {"n.py": 'logger.info(f"Sent receipt to {user.email}")\n'}, True),
        AuditCase("password-print", "pii",
                  {"o.py": 'print("password:", password)\n'}, True),
        AuditCase("card-in-log", "pii",
                  {"p.py": 'console.log("charging", user.card_number)\n'}, True),
    ]
    # ── PII — decoys ─────────────────────────────────────────────────────────
    cases += [
        AuditCase("log-id-only", "pii",
                  {"q.py": 'logger.info(f"processing order {order.id}")\n'}, False,
                  "non-PII identifier"),
        AuditCase("log-static", "pii",
                  {"r.py": 'logger.info("request received")\n'}, False),
    ]

    # ── INJECTION — should flag ──────────────────────────────────────────────
    cases += [
        AuditCase("sql-fstring", "injection",
                  {"db.py": 'q = f"SELECT * FROM users WHERE id={uid}"\ncur.execute(q)\n'}, True),
        AuditCase("sql-concat", "injection",
                  {"db2.py": 'cur.execute("DELETE FROM t WHERE n=" + name)\n'}, True),
        AuditCase("eval-input", "injection",
                  {"e.py": 'result = eval(request.args["expr"])\n'}, True),
        AuditCase("sql-percent-format", "injection",
                  {"db3.py": 'cur.execute("SELECT * FROM users WHERE id=%s" % uid)\n'}, True,
                  "old-style % format operator (dangerous)"),
    ]
    # ── INJECTION — decoys ───────────────────────────────────────────────────
    cases += [
        AuditCase("parameterized-sql", "injection",
                  {"safe.py": 'cur.execute("SELECT * FROM users WHERE id=%s", (uid,))\n'}, False,
                  "bound parameters"),
        AuditCase("fstring-not-sql", "injection",
                  {"g.py": 'msg = f"Hello {name}, welcome back"\n'}, False,
                  "f-string, not SQL"),
        AuditCase("prose-from", "injection",
                  {"h.py": '# select the right handler from the registry\nx = pick(handlers)\n'}, False,
                  "English prose with SQL-ish words"),
        AuditCase("literal-eval-safe", "injection",
                  {"i.py": 'import ast\nv = ast.literal_eval(s)\n'}, False,
                  "safe literal_eval"),
    ]

    # ── AUTH — should flag ───────────────────────────────────────────────────
    cases += [
        AuditCase("admin-no-auth", "auth",
                  {"r1.py": '@app.delete("/accounts/{id}/delete")\ndef wipe(id):\n    return db.delete(id)\n'}, True),
    ]
    # ── AUTH — decoys ────────────────────────────────────────────────────────
    cases += [
        AuditCase("admin-with-auth", "auth",
                  {"r2.py": '@app.get("/accounts/{id}")\ndef get_acct(id, current_user = Depends(get_current_user)):\n    if current_user.is_admin:\n        return db.get(id)\n'}, False,
                  "has auth dependency"),
        AuditCase("public-health", "auth",
                  {"r3.py": '@app.get("/health")\ndef health():\n    return {"ok": True}\n'}, False,
                  "non-sensitive public route"),
    ]

    # ── DEPENDENCIES — should flag ───────────────────────────────────────────
    cases += [
        AuditCase("vuln-requirements", "dependencies",
                  {"requirements.txt": "fastapi==0.115.0\nrequests==2.25.0\npyyaml==5.1\n"}, True),
    ]
    # ── DEPENDENCIES — decoys ────────────────────────────────────────────────
    cases += [
        AuditCase("safe-requirements", "dependencies",
                  {"requirements.txt": "fastapi==0.115.0\nrequests==2.31.0\npyyaml==6.0.1\n"}, False,
                  "patched versions"),
    ]
    return cases


# ── Run audit benchmark ──────────────────────────────────────────────────────


@dataclass
class Confusion:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0

    def add(self, expected: bool, flagged: bool) -> None:
        if expected and flagged:
            self.tp += 1
        elif expected and not flagged:
            self.fn += 1
        elif not expected and flagged:
            self.fp += 1
        else:
            self.tn += 1

    @property
    def total(self) -> int:
        return self.tp + self.fp + self.fn + self.tn

    def metrics(self) -> dict:
        p = self.tp / (self.tp + self.fp) if (self.tp + self.fp) else 1.0
        r = self.tp / (self.tp + self.fn) if (self.tp + self.fn) else 1.0
        f1 = 2 * p * r / (p + r) if (p + r) else 0.0
        fpr = self.fp / (self.fp + self.tn) if (self.fp + self.tn) else 0.0
        acc = (self.tp + self.tn) / self.total if self.total else 0.0
        return {"precision": round(p, 3), "recall": round(r, 3), "f1": round(f1, 3),
                "false_positive_rate": round(fpr, 3), "accuracy": round(acc, 3),
                "tp": self.tp, "fp": self.fp, "fn": self.fn, "tn": self.tn}


def run_audit_benchmark() -> dict:
    cases = _audit_corpus()
    overall = Confusion()
    by_cat: dict[str, Confusion] = {}
    misses: list[str] = []

    for case in cases:
        tmp = Path(tempfile.mkdtemp(prefix="gyrobench_"))
        try:
            for rel, content in case.files.items():
                (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
                (tmp / rel).write_text(content, encoding="utf-8")
            findings = _SCANNER[case.category](tmp)
            flagged = len(findings) > 0
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        overall.add(case.expect_flag, flagged)
        by_cat.setdefault(case.category, Confusion()).add(case.expect_flag, flagged)
        if case.expect_flag != flagged:
            kind = "MISSED" if case.expect_flag else "FALSE-POSITIVE"
            misses.append(f"{kind}: [{case.category}] {case.name}"
                          + (f" — {case.note}" if case.note else ""))

    return {
        "total_cases": len(cases),
        "positives": sum(1 for c in cases if c.expect_flag),
        "decoys": sum(1 for c in cases if not c.expect_flag),
        "overall": overall.metrics(),
        "by_category": {k: v.metrics() for k, v in sorted(by_cat.items())},
        "errors": misses,
    }


# ── Rule / scope-precision benchmark ─────────────────────────────────────────


def run_rules_benchmark() -> dict:
    """Layering rule: route handlers must not import the db layer. Tests both
    detection (violation in a route) AND scope precision (same import in the db
    layer must NOT be flagged)."""
    from gyrocompass.models import Rules
    from gyrocompass.rules import RulesEngine
    import yaml

    repo = Path(tempfile.mkdtemp(prefix="gyrobench_rules_"))
    try:
        (repo / "app/api/routes").mkdir(parents=True)
        (repo / "app/services").mkdir(parents=True)
        (repo / "app/db/repositories").mkdir(parents=True)
        (repo / ".gyro").mkdir()

        # baseline files
        (repo / "app/services/svc.py").write_text("def do(): return 1\n")
        (repo / "app/db/repositories/repo.py").write_text(
            "from app.db.session import session_scope\ndef get(): return session_scope()\n")

        gyro_map = {
            "metadata": {"version": "1.0"},
            "architecture.app/api/routes": [{"file": "app/api/routes/handler.py"}],
            "architecture.app/services": [{"file": "app/services/svc.py"}],
            "architecture.app/db/repositories": [{"file": "app/db/repositories/repo.py"}],
        }
        (repo / ".gyro/.gyromap.yaml").write_text(yaml.safe_dump(gyro_map))

        rules = Rules.model_validate({
            "invariants": {
                "no-direct-db-from-routes": {
                    "description": "Route handlers must not import the db layer directly.",
                    "status": "active", "enforcement": "block",
                    "scope": ["architecture.app/api/routes"],
                    "evidence": [{"pattern": "from app.db.repositories", "type": "code_pattern"}],
                }
            }, "principles": {}, "adrs": {},
        })

        from gyrocompass.models import ArchitectureState, StateMetadata, ArchitectureElement, ElementType
        state = ArchitectureState(metadata=StateMetadata(project="bench"), architecture={
            "app/api/routes": ArchitectureElement(type=ElementType.container, description="routes"),
            "app/services": ArchitectureElement(type=ElementType.service, description="services"),
            "app/db/repositories": ArchitectureElement(type=ElementType.database, description="repos"),
        })

        cases = [
            ("violation-route-imports-db", "app/api/routes/handler.py",
             "from app.db.repositories.order_repo import list_all\ndef ep(): return list_all()\n", True),
            ("clean-route-uses-service", "app/api/routes/handler.py",
             "from app.services.svc import do\ndef ep(): return do()\n", False),
            ("scope-precision-db-layer-allowed", "app/db/repositories/repo.py",
             "from app.db.repositories.order_repo import list_all\ndef get(): return list_all()\n", False),
        ]
        conf = Confusion()
        errors = []
        for name, relfile, content, expect in cases:
            (repo / relfile).write_text(content)
            engine = RulesEngine(rules, repo)
            violations = engine.check_all(state, [relfile])
            flagged = any(v.rule_id == "no-direct-db-from-routes" for v in violations)
            conf.add(expect, flagged)
            if expect != flagged:
                errors.append(("MISSED" if expect else "FALSE-POSITIVE") + f": {name}")
            # restore baseline for the db file so cases don't bleed
            if relfile == "app/db/repositories/repo.py":
                (repo / relfile).write_text(
                    "from app.db.session import session_scope\ndef get(): return session_scope()\n")
        return {"total_cases": len(cases), "metrics": conf.metrics(), "errors": errors}
    finally:
        shutil.rmtree(repo, ignore_errors=True)


# ── Indexing performance benchmark ───────────────────────────────────────────


def run_indexing_benchmark() -> dict:
    from gyrocompass.indexer import CodeIndexer

    candidates = {
        "gyrocompass (self)": ROOT / "gyrocompass",
        "meridian-demo": ROOT.parent / "meridian-demo",
        "code-graph-rag": ROOT.parent / "code-graph-rag-main",
        "beads (Go)": ROOT.parent / "beads-main",
    }
    results = []
    for label, path in candidates.items():
        if not path.exists():
            continue
        try:
            indexer = CodeIndexer(path)
            t0 = time.perf_counter()
            state = indexer.index()
            dt = time.perf_counter() - t0
            # count source files scanned
            n_files = sum(len(v) for v in indexer._detect_languages().values())
            results.append({
                "repo": label,
                "files": n_files,
                "seconds": round(dt, 2),
                "files_per_sec": round(n_files / dt, 1) if dt else 0,
                "components": len(state.architecture),
                "capabilities": len(state.capabilities),
                "endpoints": len(state.surface_area),
                "languages": sorted({t for t in state.tech_stack
                                     if state.tech_stack[t].type == "language"}) or
                             sorted(indexer._detect_languages().keys()),
            })
        except Exception as exc:  # noqa: BLE001
            results.append({"repo": label, "error": str(exc)[:80]})
    return {"repos": results}


# ── Report ───────────────────────────────────────────────────────────────────


def to_markdown(res: dict) -> str:
    a = res["audit"]; r = res["rules"]; idx = res["indexing"]
    o = a["overall"]
    L = []
    L += ["# GyroCompass — Benchmark Results (Class A: detection + performance)", "",
          "_Reproducible, no external API. Run: `python benchmark/run_benchmark.py`._", "",
          "## 1. Ship-readiness audit — detection accuracy", "",
          f"Across **{a['total_cases']} labeled cases** "
          f"({a['positives']} real violations + {a['decoys']} realistic decoys):", "",
          "| Metric | Score |", "|---|---|",
          f"| Recall (violations caught) | **{o['recall']:.0%}** |",
          f"| Precision | **{o['precision']:.0%}** |",
          f"| F1 | **{o['f1']:.2f}** |",
          f"| False-positive rate (on decoys) | **{o['false_positive_rate']:.0%}** |",
          f"| Accuracy | **{o['accuracy']:.0%}** |", "",
          "### By category", "",
          "| Category | Recall | Precision | FP rate |", "|---|---|---|---|"]
    for cat, m in a["by_category"].items():
        L.append(f"| {cat} | {m['recall']:.0%} | {m['precision']:.0%} | {m['false_positive_rate']:.0%} |")
    if a["errors"]:
        L += ["", "**Misclassifications:**"] + [f"- {e}" for e in a["errors"]]
    L += ["", "## 2. Rule engine — detection + scope precision", "",
          f"{r['total_cases']} cases incl. a scope-precision test "
          "(same forbidden import is flagged in a route but correctly allowed in the db layer):", "",
          f"- Recall **{r['metrics']['recall']:.0%}** · Precision **{r['metrics']['precision']:.0%}** · "
          f"FP rate **{r['metrics']['false_positive_rate']:.0%}**"]
    if r["errors"]:
        L += [""] + [f"- {e}" for e in r["errors"]]
    L += ["", "## 3. Indexing performance", "",
          "| Repo | Files | Time (s) | Files/sec | Components |", "|---|---|---|---|---|"]
    for repo in idx["repos"]:
        if "error" in repo:
            L.append(f"| {repo['repo']} | — | — | — | error: {repo['error']} |")
        else:
            L.append(f"| {repo['repo']} | {repo['files']} | {repo['seconds']} | "
                     f"{repo['files_per_sec']} | {repo['components']} |")
    L += ["", "---",
          "_Methodology: labeled corpus with realistic decoys (env-var usage, parameterized SQL, "
          "placeholder keys, prose, auth-guarded routes, patched deps) so precision and "
          "false-positive rate are meaningful. All fixtures in `benchmark/run_benchmark.py`._"]
    return "\n".join(L)


def main() -> None:
    print("Running GyroCompass Class A benchmark…\n")
    res = {
        "audit": run_audit_benchmark(),
        "rules": run_rules_benchmark(),
        "indexing": run_indexing_benchmark(),
    }
    out_json = Path(__file__).parent / "results.json"
    out_md = Path(__file__).parent / "BENCHMARK.md"
    out_json.write_text(json.dumps(res, indent=2), encoding="utf-8")
    out_md.write_text(to_markdown(res), encoding="utf-8")

    o = res["audit"]["overall"]
    print(f"Audit: recall {o['recall']:.0%} · precision {o['precision']:.0%} · "
          f"FP-rate {o['false_positive_rate']:.0%} ({res['audit']['total_cases']} cases)")
    print(f"Rules: recall {res['rules']['metrics']['recall']:.0%} · "
          f"FP-rate {res['rules']['metrics']['false_positive_rate']:.0%}")
    print(f"Indexing: {len([r for r in res['indexing']['repos'] if 'error' not in r])} repos indexed")
    print(f"\n→ {out_md}\n→ {out_json}")


if __name__ == "__main__":
    main()
