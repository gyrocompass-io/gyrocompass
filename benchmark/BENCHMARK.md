# GyroCompass — Benchmark Results (Class A: detection + performance)

_Reproducible, no external API. Run: `python benchmark/run_benchmark.py`._

## 1. Ship-readiness audit — detection accuracy

Across **27 labeled cases** (14 real violations + 13 realistic decoys):

| Metric | Score |
|---|---|
| Recall (violations caught) | **100%** |
| Precision | **100%** |
| F1 | **1.00** |
| False-positive rate (on decoys) | **0%** |
| Accuracy | **100%** |

### By category

| Category | Recall | Precision | FP rate |
|---|---|---|---|
| auth | 100% | 100% | 0% |
| dependencies | 100% | 100% | 0% |
| injection | 100% | 100% | 0% |
| pii | 100% | 100% | 0% |
| secrets | 100% | 100% | 0% |

## 2. Rule engine — detection + scope precision

3 cases incl. a scope-precision test (same forbidden import is flagged in a route but correctly allowed in the db layer):

- Recall **100%** · Precision **100%** · FP rate **0%**

## 3. Indexing performance

| Repo | Files | Time (s) | Files/sec | Components |
|---|---|---|---|---|
| gyrocompass (self) | 31 | 0.08 | 412.6 | 8 |
| meridian-demo | 33 | 0.02 | 1556.5 | 8 |
| code-graph-rag | 336 | 0.46 | 732.5 | 21 |
| beads (Go) | 1090 | 1.36 | 803.0 | 58 |

---
_Methodology: labeled corpus with realistic decoys (env-var usage, parameterized SQL, placeholder keys, prose, auth-guarded routes, patched deps) so precision and false-positive rate are meaningful. All fixtures in `benchmark/run_benchmark.py`._