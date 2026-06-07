"""Natural-language → Cypher querying over the GyroCompass code graph.

Lets a developer (or an agent) ask "what calls the auth middleware?" and get a
real answer from the Memgraph graph, without hand-writing Cypher. The LLM does
the translation; this module owns the schema prompt, response sanitisation, a
safety LIMIT, and a single error-feedback retry.

Crucially, it reuses GyroCompass's own LLM abstraction (`gyrocompass.llm`) so
querying respects the user's configured provider/model and never bolts on a
second LLM stack. It is intentionally lightweight — no embeddings, no engine —
so it works whenever `GraphBackend` is reachable.
"""

from __future__ import annotations

import re

from loguru import logger

# Concise description of the code-graph-rag node/edge schema. Embedded verbatim
# into the Cypher-specialist system prompt so the model writes valid queries
# against the actual labels and unique keys.
GRAPH_SCHEMA = """\
GRAPH SCHEMA (Memgraph / openCypher)

Node labels and their UNIQUE identifying property:
  Project(name)              - the repository root
  Package(qualified_name)    - an importable package (dir with __init__ etc.)
  Folder(path)               - a non-package directory (repo-relative path)
  File(path)                 - a source file (repo-relative path)
  Module(qualified_name)     - a parsed module; also has `path` (repo-relative)
  Class(qualified_name)      - a class definition
  Function(qualified_name)   - a free function
  Method(qualified_name)     - a method defined on a class
  Interface(qualified_name)  - an interface / trait / protocol
  Enum(qualified_name)       - an enum type
  ExternalPackage(name)      - a third-party dependency

Common node properties:
  qualified_name (dotted, e.g. "myproj.auth.login"), name, path,
  start_line, end_line.

Relationship types (direction is (from)-[REL]->(to)):
  CONTAINS_PACKAGE / CONTAINS_FOLDER / CONTAINS_FILE / CONTAINS_MODULE
      structural containment (Project/Package/Folder contain children)
  DEFINES               Module/Class -> Class/Function/Enum/Interface
  DEFINES_METHOD        Class -> Method
  IMPORTS               Module -> Module        (import graph)
  INHERITS              Class -> Class          (subclass -> superclass)
  IMPLEMENTS            Class -> Interface
  OVERRIDES             Method -> Method         (override -> overridden)
  CALLS                 Function/Method -> Function/Method   (call graph)
  DEPENDS_ON_EXTERNAL   Module -> ExternalPackage

Notes:
  - Code nodes (Class/Function/Method/Module/Interface/Enum) are keyed by
    `qualified_name`. File/Folder are keyed by `path`. Project/ExternalPackage
    by `name`.
  - To find who calls X: MATCH (caller)-[:CALLS]->(callee) WHERE callee...
  - To find a symbol's file: MATCH (m:Module)-[:DEFINES|DEFINES_METHOD*1..2]->(s)
    and read m.path.
"""

_SYSTEM_PROMPT = f"""You are an expert at translating natural-language questions \
into a SINGLE read-only openCypher query for a Memgraph code graph.

{GRAPH_SCHEMA}

RULES:
- Output ONLY the Cypher query. No prose, no explanation, no markdown fences.
- The query MUST be read-only: use MATCH/OPTIONAL MATCH/WHERE/RETURN/WITH/ORDER
  BY/LIMIT only. NEVER emit CREATE, MERGE, SET, DELETE, REMOVE, DETACH, or CALL
  to write procedures.
- Always RETURN named columns (use AS aliases) so results are tabular.
- Prefer matching on qualified_name with CONTAINS for fuzzy symbol questions.
- Keep result sets small; do not RETURN whole nodes when a few properties suffice.
"""

# Clauses that indicate a mutating / unsafe query we must reject.
_FORBIDDEN = re.compile(
    r"\b(CREATE|MERGE|SET|DELETE|REMOVE|DETACH|DROP|LOAD\s+CSV)\b", re.IGNORECASE
)
_FENCE = re.compile(r"^```(?:cypher)?\s*|\s*```$", re.IGNORECASE)


class NLQueryEngine:
    """Translate NL questions to Cypher and run them against a GraphBackend."""

    def __init__(self, backend) -> None:
        """Args:
        backend: a ``gyrocompass.graph.backend.GraphBackend`` instance used to
            execute the generated Cypher.
        """
        self.backend = backend
        self._provider = None

    def _get_provider(self):
        if self._provider is None:
            from gyrocompass.config import settings
            from gyrocompass.llm import get_provider

            self._provider = get_provider(settings)
        return self._provider

    # ── Translation ────────────────────────────────────────────────────────────

    def to_cypher(self, question: str) -> str:
        """Translate ``question`` into a single, validated Cypher query.

        Strips markdown fences and a trailing semicolon, validates the result is
        a read-only MATCH query, and appends ``LIMIT 50`` if no LIMIT is present.

        Raises:
            ValueError: If the model returns something that isn't a usable
                read-only Cypher query.
        """
        provider = self._get_provider()
        raw = provider.complete(question.strip(), system=_SYSTEM_PROMPT)
        return self._sanitise(raw)

    def _sanitise(self, raw: str) -> str:
        cypher = (raw or "").strip()
        # Strip ``` / ```cypher fences (possibly multi-line).
        if cypher.startswith("```"):
            lines = [ln for ln in cypher.splitlines() if not ln.strip().startswith("```")]
            cypher = "\n".join(lines).strip()
        cypher = _FENCE.sub("", cypher).strip()
        cypher = cypher.rstrip(";").strip()

        if not cypher:
            raise ValueError("LLM returned an empty Cypher query")
        if "MATCH" not in cypher.upper():
            raise ValueError(
                f"LLM did not return a Cypher query (no MATCH clause): {cypher!r}"
            )
        if _FORBIDDEN.search(cypher):
            raise ValueError(
                f"Refusing to run a mutating/unsafe Cypher query: {cypher!r}"
            )
        if not re.search(r"\bLIMIT\b", cypher, re.IGNORECASE):
            cypher = f"{cypher}\nLIMIT 50"
        return cypher

    # ── Full ask flow ───────────────────────────────────────────────────────────

    def ask(self, question: str) -> dict:
        """Translate, execute, and (on error) retry once with the error fed back.

        Returns ``{"question", "cypher", "rows", "row_count"}``.

        Raises:
            ValueError: If translation fails irrecoverably.
            RuntimeError: If execution still fails after the retry.
        """
        cypher = self.to_cypher(question)
        try:
            rows = self.backend.query(cypher)
            return {
                "question": question,
                "cypher": cypher,
                "rows": rows,
                "row_count": len(rows),
            }
        except Exception as exc:
            logger.warning("Cypher execution failed; retrying with error feedback: {}", exc)
            corrected = self._retry(question, cypher, str(exc))
            try:
                rows = self.backend.query(corrected)
            except Exception as exc2:
                raise RuntimeError(
                    f"Cypher failed after one correction attempt. "
                    f"Last query: {corrected!r}; error: {exc2}"
                ) from exc2
            return {
                "question": question,
                "cypher": corrected,
                "rows": rows,
                "row_count": len(rows),
            }

    def _retry(self, question: str, bad_cypher: str, error: str) -> str:
        provider = self._get_provider()
        prompt = (
            f"Original question:\n{question}\n\n"
            f"The Cypher query you produced failed:\n{bad_cypher}\n\n"
            f"Memgraph error:\n{error}\n\n"
            "Return a corrected, read-only Cypher query. Output ONLY the query."
        )
        raw = provider.complete(prompt, system=_SYSTEM_PROMPT)
        return self._sanitise(raw)
