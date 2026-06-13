"""Ship-readiness scanners — the checks behind `gyro audit`.

Each scanner inspects a repository and yields AuditFinding objects written so a
coding agent can act on them directly (file, line, and a concrete fix). Tuned
for LOW false-positives: patterns are specific, test/example/vendor paths are
skipped, and obvious placeholders are ignored.

Scanners:
  • SecretsScanner       — hardcoded API keys / tokens (known vendor patterns)
  • PiiLogScanner        — PII (email/password/token) flowing into log/print
  • InjectionScanner     — SQL built by string-concat/f-string, eval/exec
  • AuthScanner          — admin/internal routes with no visible auth guard
  • TestCoverageScanner  — source dirs with zero test files
  • CiScanner            — repo has no CI workflow
  • ObservabilityScanner — service code with no logging at all
  • DependencyScanner    — known-vulnerable dependency versions (static list)
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from pathlib import Path

from loguru import logger

from gyrocompass.models import AuditCategory, AuditFinding, AuditSeverity

# ── Shared file walking ──────────────────────────────────────────────────────

_SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", ".venv", "venv", "dist", "build",
    ".next", "target", ".gyro", "vendor", ".mypy_cache", ".pytest_cache",
    "site-packages", ".tox", "coverage", ".idea", ".vscode",
}
_SOURCE_EXT = {".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rb", ".java", ".php", ".mjs", ".cjs"}
_TEST_HINTS = ("test", "spec", "__tests__", ".test.", ".spec.")


def _is_test_path(rel: str) -> bool:
    low = rel.lower()
    return any(h in low for h in _TEST_HINTS)


def iter_source_files(repo: Path) -> list[Path]:
    out: list[Path] = []
    for p in repo.rglob("*"):
        if not p.is_file():
            continue
        parts = p.relative_to(repo).parts
        if any(part in _SKIP_DIRS for part in parts):
            continue
        if p.suffix.lower() in _SOURCE_EXT:
            if p.stat().st_size <= 1_000_000:
                out.append(p)
    return out


def _read(p: Path) -> str | None:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _rel(p: Path, repo: Path) -> str:
    try:
        return str(p.relative_to(repo))
    except ValueError:
        return str(p)


def _shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    counts = defaultdict(int)
    for ch in s:
        counts[ch] += 1
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


# ── 1. Secrets ────────────────────────────────────────────────────────────────

# Specific, high-confidence vendor patterns (low false-positive).
_SECRET_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("AWS secret key", re.compile(r"(?i)aws_secret_access_key\s*[=:]\s*['\"][A-Za-z0-9/+=]{40}['\"]")),
    ("Stripe secret key", re.compile(r"\bsk_live_[0-9a-zA-Z]{24,}\b")),
    ("Stripe restricted key", re.compile(r"\brk_live_[0-9a-zA-Z]{24,}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b")),
    ("OpenAI key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{32,}\b")),
    ("Private key block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
]
# Generic "assigned secret" pattern — value checked for entropy to avoid noise.
_GENERIC_SECRET = re.compile(
    r"(?i)(api[_-]?key|secret|passwd|password|token|access[_-]?key)\s*[=:]\s*['\"]([^'\"]{12,})['\"]"
)
_PLACEHOLDER = re.compile(
    r"(?i)(your|example|changeme|placeholder|xxx+|<.*>|\$\{|process\.env|os\.environ|getenv|dummy|test|sample|redacted|\*{3,})"
)


def scan_secrets(repo: Path) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    for p in iter_source_files(repo):
        rel = _rel(p, repo)
        if _is_test_path(rel):
            continue
        content = _read(p)
        if content is None:
            continue
        for i, line in enumerate(content.splitlines(), 1):
            if len(line) > 500:
                continue
            for label, pat in _SECRET_PATTERNS:
                if pat.search(line):
                    findings.append(AuditFinding(
                        id="secret_in_code",
                        category=AuditCategory.security,
                        severity=AuditSeverity.critical,
                        title=f"{label} hardcoded",
                        message=f"{label} appears hardcoded in source",
                        file=rel, line=i, snippet=line.strip()[:120],
                        fix="Move the secret to an environment variable and rotate the exposed key.",
                    ))
                    break
            else:
                m = _GENERIC_SECRET.search(line)
                if m:
                    value = m.group(2)
                    if _PLACEHOLDER.search(line):
                        continue
                    if _shannon_entropy(value) >= 3.5:  # looks random, not a word
                        findings.append(AuditFinding(
                            id="possible_secret",
                            category=AuditCategory.security,
                            severity=AuditSeverity.high,
                            title="Possible hardcoded secret",
                            message=f"High-entropy value assigned to '{m.group(1)}' looks like a real secret",
                            file=rel, line=i, snippet=line.strip()[:120],
                            fix="If this is a credential, move it to an env var and rotate it.",
                        ))
    return findings


# ── 2. PII in logs ────────────────────────────────────────────────────────────

_LOG_CALL = re.compile(r"(?i)\b(console\.(log|info|warn|error|debug)|print|printf|fmt\.Print\w*|logger?\.\w+|logging\.\w+|log\.\w+)\s*\(")
_PII_TOKENS = re.compile(r"(?i)\b(\w*\.)?(email|e_mail|password|passwd|pwd|ssn|credit_?card|card_?number|cvv|secret|token|api_?key|phone|address|dob|date_of_birth)\b")


def scan_pii_logs(repo: Path) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    for p in iter_source_files(repo):
        rel = _rel(p, repo)
        if _is_test_path(rel):
            continue
        content = _read(p)
        if content is None:
            continue
        for i, line in enumerate(content.splitlines(), 1):
            if not _LOG_CALL.search(line):
                continue
            m = _PII_TOKENS.search(line)
            if not m:
                continue
            # Skip if the token is clearly a field name being set, not logged value
            token = m.group(2)
            findings.append(AuditFinding(
                id="pii_in_logs",
                category=AuditCategory.security,
                severity=AuditSeverity.high,
                title=f"PII ('{token}') in log output",
                message=f"Sensitive field '{token}' is written to a log/print call",
                file=rel, line=i, snippet=line.strip()[:120],
                fix=f"Remove or redact '{token}' before logging (log an anonymized id instead).",
            ))
    return findings


# ── 3. Injection / dangerous eval ─────────────────────────────────────────────

# A quoted string (optionally an f-string) that STARTS with a real SQL command.
# Anchoring to the start of the string is what avoids false positives on prose
# like "relationship from X" or "removed from capability".
_SQL_STRING_START = re.compile(
    r"""(?ix)
    (?:f|rf|fr)?            # optional f-string prefix
    (['"])                  # opening quote
    \s*
    (SELECT|INSERT\s+INTO|UPDATE\s+\w|DELETE\s+FROM|CREATE\s+TABLE|DROP\s+TABLE|ALTER\s+TABLE)
    \b
    """
)
_DANGEROUS_EVAL = re.compile(r"(?<![\w.])(eval|exec)\s*\(")
_SHELL_INJECT = re.compile(r"(?i)(os\.system|subprocess\.\w+\([^)]*shell\s*=\s*True|child_process\.exec)\s*\(")


def scan_injection(repo: Path) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    for p in iter_source_files(repo):
        rel = _rel(p, repo)
        if _is_test_path(rel):
            continue
        content = _read(p)
        if content is None:
            continue
        for i, line in enumerate(content.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("//"):
                continue
            if _looks_sql_dynamic(line):
                findings.append(AuditFinding(
                    id="sql_injection",
                    category=AuditCategory.security,
                    severity=AuditSeverity.critical,
                    title="Possible SQL injection",
                    message="SQL query appears to be built from dynamic string interpolation/concatenation",
                    file=rel, line=i, snippet=stripped[:120],
                    fix="Use parameterized queries / bound parameters instead of string building.",
                ))
            elif _DANGEROUS_EVAL.search(line) and "JSON" not in line and "ast.literal_eval" not in line:
                findings.append(AuditFinding(
                    id="dangerous_eval",
                    category=AuditCategory.security,
                    severity=AuditSeverity.high,
                    title="Use of eval/exec",
                    message="Dynamic eval/exec can execute arbitrary code if fed untrusted input",
                    file=rel, line=i, snippet=stripped[:120],
                    fix="Replace eval/exec with a safe parser (e.g. ast.literal_eval / JSON.parse).",
                ))
            elif _SHELL_INJECT.search(line):
                findings.append(AuditFinding(
                    id="shell_injection",
                    category=AuditCategory.security,
                    severity=AuditSeverity.high,
                    title="Shell execution risk",
                    message="Shell command execution may allow command injection with untrusted input",
                    file=rel, line=i, snippet=stripped[:120],
                    fix="Avoid shell=True / os.system; pass args as a list and validate input.",
                ))
    return findings


def _looks_sql_dynamic(line: str) -> bool:
    """True only if the line builds a real SQL statement dynamically.

    Requires a quoted string that *starts* with a SQL command keyword (so prose
    containing "from"/"update" doesn't match), AND dynamic interpolation —
    either f-string braces inside that string or string concatenation.
    """
    m = _SQL_STRING_START.search(line)
    if not m:
        return False
    # Must be dynamic: f-string with a {placeholder}, or string concatenation,
    # or old-style % formatting APPLIED to the string. Crucially, a `%s`
    # placeholder *inside* a string passed to exec(sql, params) is the SAFE
    # parameterized form — we only flag the `%` *operator* (closing-quote then %),
    # e.g.  "... id=%s" % uid  — not  execute("... id=%s", (uid,)).
    is_fstring = bool(re.match(r"(?i)(f|rf|fr)['\"]", line[m.start():]))
    has_brace = "{" in line
    has_str_concat = bool(re.search(r"['\"]\s*\+\s*\w", line))
    has_pct_format = bool(re.search(r"['\"]\s*%\s*[\w(]", line))  # "..." % var / % (
    if is_fstring and has_brace:
        return True
    if has_str_concat or has_pct_format:
        return True
    return False


# ── 4. Auth on sensitive routes ───────────────────────────────────────────────

# A real HTTP route declaration: a framework decorator or a router method call
# that carries a path string literal. Requiring this (vs. any line with a path)
# eliminates false positives on config strings, MCP URIs, doc examples, etc.
_ROUTE_DECL = re.compile(
    r"""(?ix)
    (?:
        @\s*\w+\.(?:route|get|post|put|delete|patch)\b      # @app.get(...) / @router.post(...)
      | \b(?:app|router|api|r)\.(?:route|get|post|put|delete|patch)\s*\(  # app.get("...")
      | \b(?:get|post|put|delete|patch)\s*\(\s*['"]/        # express-style get("/...")
    )
    """
)
_PATH_LITERAL = re.compile(r"""['"](/[A-Za-z0-9_/:{}-]*)['"]""")
_SENSITIVE_SEG = re.compile(r"(?i)/(admin|internal|debug|accounts?|payments?|billing|config|settings|delete|users?)\b")
_AUTH_HINT = re.compile(r"(?i)(auth|login_required|requires?_auth|authenticate|permission|is_admin|verify_token|@protected|protect|guard|current_user|jwt|session|depends\(|middleware)")


def scan_auth(repo: Path) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    for p in iter_source_files(repo):
        rel = _rel(p, repo)
        if _is_test_path(rel):
            continue
        content = _read(p)
        if content is None:
            continue
        lines = content.splitlines()
        for i, line in enumerate(lines, 1):
            # Must be an actual route declaration…
            if not _ROUTE_DECL.search(line):
                continue
            # …carrying a path literal that hits a sensitive segment.
            path_m = _PATH_LITERAL.search(line)
            if not path_m or not _SENSITIVE_SEG.search(path_m.group(1)):
                continue
            # Scope the auth check to THIS route's own body: scan forward from
            # the route line until the next route/decorator, so an adjacent
            # route's auth check can't mask this one. Include 2 lines above for
            # a decorator-above-def auth guard.
            window_parts = [line]
            for k in range(i, min(len(lines), i + 9)):  # k is 0-based = line k+1
                nxt = lines[k]
                if _ROUTE_DECL.search(nxt) or nxt.lstrip().startswith("@"):
                    break
                window_parts.append(nxt)
            above = lines[max(0, i - 3): i - 1]
            window = "\n".join(above + window_parts)
            if not _AUTH_HINT.search(window):
                findings.append(AuditFinding(
                    id="missing_auth",
                    category=AuditCategory.security,
                    severity=AuditSeverity.high,
                    title="Sensitive route without visible auth",
                    message=f"Route touching '{path_m.group(1)}' has no nearby authentication/authorization check",
                    file=rel, line=i, snippet=line.strip()[:120],
                    fix="Add an auth guard / permission check to this route, or confirm it is intentionally public.",
                ))
    return findings


# ── 5. Test coverage ──────────────────────────────────────────────────────────


def scan_tests(repo: Path) -> list[AuditFinding]:
    files = iter_source_files(repo)
    test_files = [p for p in files if _is_test_path(_rel(p, repo))]
    if not files:
        return []
    # No tests at all in the whole repo → one strong finding
    if not test_files:
        return [AuditFinding(
            id="no_tests",
            category=AuditCategory.tech_debt,
            severity=AuditSeverity.high,
            title="No tests in the project",
            message="The project has zero test files — critical paths are unverified",
            fix="Add tests for the most important flows (auth, payments, data writes) first.",
        )]
    # Per-top-level-dir: flag source dirs with code but no tests
    findings: list[AuditFinding] = []
    by_dir: dict[str, dict] = defaultdict(lambda: {"src": 0, "test": 0})
    for p in files:
        rel = _rel(p, repo)
        top = rel.split("/")[0] if "/" in rel else "."
        if _is_test_path(rel):
            by_dir[top]["test"] += 1
        else:
            by_dir[top]["src"] += 1
    SENSITIVE = ("payment", "auth", "billing", "checkout", "account", "user")
    for top, c in by_dir.items():
        if c["src"] >= 2 and c["test"] == 0 and any(s in top.lower() for s in SENSITIVE):
            findings.append(AuditFinding(
                id="no_tests_on_critical_path",
                category=AuditCategory.tech_debt,
                severity=AuditSeverity.high,
                title=f"No tests for '{top}'",
                message=f"Sensitive area '{top}/' has {c['src']} source files and 0 tests",
                fix=f"Add tests covering the main flows in {top}/.",
            ))
    return findings


# ── 6. CI ─────────────────────────────────────────────────────────────────────

_CI_MARKERS = [
    ".github/workflows", ".gitlab-ci.yml", ".circleci", "azure-pipelines.yml",
    "Jenkinsfile", ".travis.yml", "bitbucket-pipelines.yml", ".drone.yml",
]


def scan_ci(repo: Path) -> list[AuditFinding]:
    for marker in _CI_MARKERS:
        path = repo / marker
        if path.exists():
            if path.is_dir() and not any(path.iterdir()):
                continue
            return []
    return [AuditFinding(
        id="no_ci",
        category=AuditCategory.tech_debt,
        severity=AuditSeverity.medium,
        title="No CI pipeline",
        message="No CI configuration found — changes aren't automatically checked before merge",
        fix="Add a CI workflow (e.g. .github/workflows/ci.yml) that runs tests and linting on every PR.",
    )]


# ── 7. Observability ──────────────────────────────────────────────────────────

_LOGGING_IMPORT = re.compile(r"(?i)(import logging|from loguru|require\(['\"](winston|pino|bunyan)|console\.|logger|log\.|slog\.|zap\.|logrus)")


def scan_observability(repo: Path) -> list[AuditFinding]:
    files = [p for p in iter_source_files(repo) if not _is_test_path(_rel(p, repo))]
    if len(files) < 3:
        return []
    has_logging = False
    for p in files:
        content = _read(p)
        if content and _LOGGING_IMPORT.search(content):
            has_logging = True
            break
    if has_logging:
        return []
    return [AuditFinding(
        id="no_observability",
        category=AuditCategory.tech_debt,
        severity=AuditSeverity.medium,
        title="No logging / observability",
        message="No logging found anywhere in the codebase — you won't know when something breaks in production",
        fix="Add structured logging at key boundaries (requests, errors, external calls).",
    )]


# ── 8. Vulnerable dependencies (static known-bad list) ────────────────────────

# Small curated set of well-known vulnerable versions — offline, demo-reliable.
# (id, ecosystem, package, vulnerable_spec, advisory, fixed_in)
_KNOWN_VULNS: list[tuple[str, str, str, str, str]] = [
    ("lodash", "<4.17.21", "prototype pollution (CVE-2020-8203/2021-23337)", ">=4.17.21", "npm"),
    ("minimist", "<1.2.6", "prototype pollution (CVE-2021-44906)", ">=1.2.6", "npm"),
    ("axios", "<0.21.2", "SSRF / ReDoS (CVE-2021-3749)", ">=0.21.2", "npm"),
    ("node-fetch", "<2.6.7", "exposure of sensitive info (CVE-2022-0235)", ">=2.6.7", "npm"),
    ("express", "<4.17.3", "open redirect / ReDoS", ">=4.17.3", "npm"),
    ("jsonwebtoken", "<9.0.0", "weak verification (CVE-2022-23529)", ">=9.0.0", "npm"),
    ("flask", "<2.2.5", "cookie parsing / DoS", ">=2.2.5", "pip"),
    ("django", "<3.2.20", "multiple CVEs", ">=3.2.20", "pip"),
    ("requests", "<2.31.0", "leak of Proxy-Authorization (CVE-2023-32681)", ">=2.31.0", "pip"),
    ("pyyaml", "<5.4", "arbitrary code execution via full_load (CVE-2020-14343)", ">=5.4", "pip"),
    ("cryptography", "<41.0.0", "multiple OpenSSL CVEs", ">=41.0.0", "pip"),
    ("urllib3", "<1.26.18", "request smuggling / redirect leak", ">=1.26.18", "pip"),
]


def _ver_tuple(v: str) -> tuple:
    parts = re.findall(r"\d+", v)
    return tuple(int(x) for x in parts[:4]) if parts else (0,)


def _is_vulnerable(version: str, spec: str) -> bool:
    # spec is always "<X.Y.Z" in our table
    bound = spec.lstrip("<").strip()
    return _ver_tuple(version) < _ver_tuple(bound)


def scan_dependencies(repo: Path) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    findings += _scan_package_json(repo)
    findings += _scan_requirements(repo)
    # Dedupe: the same vulnerable package often appears in multiple manifests
    # (e.g. requirements.txt AND pyproject.toml). Report each once, but keep the
    # first file as the location.
    seen: set[str] = set()
    deduped: list[AuditFinding] = []
    for f in findings:
        key = f.title  # "Vulnerable dependency: <name>"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(f)
    return deduped


def _scan_package_json(repo: Path) -> list[AuditFinding]:
    import json

    out: list[AuditFinding] = []
    for pj in repo.rglob("package.json"):
        if any(part in _SKIP_DIRS for part in pj.relative_to(repo).parts):
            continue
        content = _read(pj)
        if not content:
            continue
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            continue
        deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
        for name, ver in deps.items():
            clean = ver.lstrip("^~>=< ")
            for pkg, spec, advisory, fixed, eco in _KNOWN_VULNS:
                if eco == "npm" and name == pkg and _is_vulnerable(clean, spec):
                    out.append(AuditFinding(
                        id="vulnerable_dependency",
                        category=AuditCategory.security,
                        severity=AuditSeverity.high,
                        title=f"Vulnerable dependency: {name}",
                        message=f"{name}@{clean} has a known vulnerability: {advisory}",
                        file=_rel(pj, repo),
                        fix=f"Upgrade {name} to {fixed}.",
                    ))
    return out


def _scan_requirements(repo: Path) -> list[AuditFinding]:
    out: list[AuditFinding] = []
    for req in list(repo.rglob("requirements*.txt")) + list(repo.rglob("pyproject.toml")):
        if any(part in _SKIP_DIRS for part in req.relative_to(repo).parts):
            continue
        content = _read(req)
        if not content:
            continue
        for line in content.splitlines():
            m = re.match(r"\s*['\"]?([A-Za-z0-9_.-]+)['\"]?\s*[=>~ ]=\s*['\"]?([0-9][0-9.]*)", line)
            if not m:
                continue
            name, ver = m.group(1).lower(), m.group(2)
            for pkg, spec, advisory, fixed, eco in _KNOWN_VULNS:
                if eco == "pip" and name == pkg and _is_vulnerable(ver, spec):
                    out.append(AuditFinding(
                        id="vulnerable_dependency",
                        category=AuditCategory.security,
                        severity=AuditSeverity.high,
                        title=f"Vulnerable dependency: {name}",
                        message=f"{name}=={ver} has a known vulnerability: {advisory}",
                        file=_rel(req, repo),
                        fix=f"Upgrade {name} to {fixed}.",
                    ))
    return out


# ── Registry ──────────────────────────────────────────────────────────────────

ALL_SCANNERS = {
    "secrets": scan_secrets,
    "pii": scan_pii_logs,
    "injection": scan_injection,
    "auth": scan_auth,
    "tests": scan_tests,
    "ci": scan_ci,
    "observability": scan_observability,
    "dependencies": scan_dependencies,
}
