"""Memgraph-backed graph backend + call-graph blast-radius analysis.

This is the heart of Phase 2. It connects to a Memgraph instance (Neo4j wire
compatible), runs Cypher, and exposes architecture-aware queries on top of the
code-graph-rag node/edge schema:

  Nodes:  Project, Package, Folder, File, Module, Class, Function, Method,
          Interface, Enum, ExternalPackage
  Edges:  CONTAINS_*, DEFINES, DEFINES_METHOD, IMPORTS, INHERITS, IMPLEMENTS,
          OVERRIDES, CALLS, DEPENDS_ON_EXTERNAL

The key value-add over code-graph-rag is `blast_radius()` — given a set of
changed files, it walks the CALLS/IMPORTS graph to find everything
transitively affected. That turns drift detection from "a new folder appeared"
into "this change severs a call path used by 14 downstream functions."

Connection is lazy and degrades gracefully: if mgclient isn't installed or
Memgraph isn't reachable, `GraphBackend.available()` returns False and callers
fall back to the lite (NetworkX) path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger


@dataclass
class GraphAvailability:
    """Result of probing whether the graph backend can be used."""

    driver_installed: bool
    server_reachable: bool
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.driver_installed and self.server_reachable


@dataclass
class BlastRadius:
    """Transitive impact of a change, computed from the call/import graph."""

    seed_files: list[str]
    seed_symbols: list[str] = field(default_factory=list)
    affected_functions: list[str] = field(default_factory=list)
    affected_modules: list[str] = field(default_factory=list)
    affected_callers: list[str] = field(default_factory=list)
    severed_calls: list[tuple[str, str]] = field(default_factory=list)
    depth_reached: int = 0

    @property
    def total_affected(self) -> int:
        return len(set(self.affected_functions) | set(self.affected_modules))

    def summary(self) -> str:
        return (
            f"{len(self.seed_files)} changed file(s) → "
            f"{len(self.affected_callers)} direct caller(s), "
            f"{self.total_affected} transitively affected symbol(s) "
            f"(depth {self.depth_reached})"
        )


def _import_mgclient():
    """Lazily import the Memgraph client. Returns the module or None."""
    try:
        import mgclient  # type: ignore

        return mgclient
    except ImportError:
        return None


def graph_status(host: str, port: int) -> GraphAvailability:
    """Probe driver + server reachability without raising."""
    mgclient = _import_mgclient()
    if mgclient is None:
        return GraphAvailability(
            False, False, "pymgclient not installed (pip install gyrocompass[graph])"
        )
    try:
        conn = mgclient.connect(host=host, port=port)
        conn.close()
        return GraphAvailability(True, True, "ok")
    except Exception as exc:  # connection refused, etc.
        return GraphAvailability(
            True, False, f"Memgraph not reachable at {host}:{port} ({exc})"
        )


class GraphBackend:
    """Thin Cypher client + architecture-aware graph queries over Memgraph."""

    def __init__(self, host: str | None = None, port: int | None = None) -> None:
        from gyrocompass.config import settings

        self.host = host or settings.MEMGRAPH_HOST
        self.port = port or settings.MEMGRAPH_PORT
        self._conn = None

    # ── Connection ───────────────────────────────────────────────────────────

    def available(self) -> GraphAvailability:
        return graph_status(self.host, self.port)

    def _connect(self):
        if self._conn is not None:
            return self._conn
        mgclient = _import_mgclient()
        if mgclient is None:
            raise RuntimeError(
                "pymgclient not installed. Install with: pip install gyrocompass[graph]"
            )
        self._conn = mgclient.connect(host=self.host, port=self.port)
        self._conn.autocommit = True
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    def __enter__(self) -> GraphBackend:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ── Raw query ────────────────────────────────────────────────────────────

    def query(self, cypher: str, params: dict | None = None) -> list[dict]:
        """Execute Cypher and return rows as dicts."""
        conn = self._connect()
        cur = conn.cursor()
        cur.execute(cypher, params or {})
        try:
            rows = cur.fetchall()
        except Exception:
            rows = []
        columns = [d.name for d in cur.description] if cur.description else []
        return [dict(zip(columns, row)) for row in rows]

    # ── Stats ────────────────────────────────────────────────────────────────

    def node_count(self) -> int:
        rows = self.query("MATCH (n) RETURN count(n) AS c")
        return rows[0]["c"] if rows else 0

    def edge_count(self) -> int:
        rows = self.query("MATCH ()-[r]->() RETURN count(r) AS c")
        return rows[0]["c"] if rows else 0

    def stats(self) -> dict:
        by_label = self.query(
            "MATCH (n) RETURN labels(n)[0] AS label, count(n) AS c ORDER BY c DESC"
        )
        by_rel = self.query(
            "MATCH ()-[r]->() RETURN type(r) AS rel, count(r) AS c ORDER BY c DESC"
        )
        return {
            "nodes": self.node_count(),
            "edges": self.edge_count(),
            "by_label": {r["label"]: r["c"] for r in by_label if r.get("label")},
            "by_relationship": {r["rel"]: r["c"] for r in by_rel},
        }

    # ── Blast radius (the core value-add) ─────────────────────────────────────

    def blast_radius(
        self, changed_files: list[str], max_depth: int = 5
    ) -> BlastRadius:
        """Compute the transitive impact of changing a set of files.

        Strategy:
          1. Find all Function/Method nodes DEFINED in modules whose file path
             matches a changed file (the "seed" symbols).
          2. Walk CALLS edges *backwards* (who calls the seeds, transitively) to
             find affected callers up to max_depth.
          3. Walk IMPORTS edges backwards to find affected modules.

        Returns a BlastRadius with the affected sets. Falls back to empty results
        if the graph is not populated.
        """
        result = BlastRadius(seed_files=list(changed_files))
        if not changed_files:
            return result

        # Normalise file paths for matching (Memgraph stores repo-relative paths)
        file_clauses = " OR ".join(
            f"m.path ENDS WITH '{self._escape(f)}'" for f in changed_files
        )

        # 1. Seed symbols defined in changed modules
        seed_rows = self.query(
            f"""
            MATCH (m:Module)-[:DEFINES|DEFINES_METHOD*1..2]->(s)
            WHERE ({file_clauses}) AND (s:Function OR s:Method)
            RETURN DISTINCT s.qualified_name AS qn
            """
        )
        result.seed_symbols = [r["qn"] for r in seed_rows if r.get("qn")]
        if not result.seed_symbols:
            logger.debug("blast_radius: no seed symbols found for {}", changed_files)
            return result

        # 2. Transitive callers (reverse CALLS) up to max_depth
        caller_rows = self.query(
            f"""
            MATCH (caller)-[:CALLS*1..{max_depth}]->(seed)
            WHERE seed.qualified_name IN $seeds
              AND (caller:Function OR caller:Method)
            RETURN DISTINCT caller.qualified_name AS qn
            """,
            {"seeds": result.seed_symbols},
        )
        result.affected_functions = [r["qn"] for r in caller_rows if r.get("qn")]

        # Direct callers (depth 1) — highest-signal subset
        direct_rows = self.query(
            """
            MATCH (caller)-[:CALLS]->(seed)
            WHERE seed.qualified_name IN $seeds
              AND (caller:Function OR caller:Method)
            RETURN DISTINCT caller.qualified_name AS qn
            """,
            {"seeds": result.seed_symbols},
        )
        result.affected_callers = [r["qn"] for r in direct_rows if r.get("qn")]

        # 3. Affected modules via reverse IMPORTS
        mod_rows = self.query(
            f"""
            MATCH (importer:Module)-[:IMPORTS*1..{max_depth}]->(m:Module)
            WHERE ({file_clauses})
            RETURN DISTINCT importer.path AS path
            """
        )
        result.affected_modules = [r["path"] for r in mod_rows if r.get("path")]
        result.depth_reached = max_depth
        return result

    # ── Architecture boundary queries ─────────────────────────────────────────

    def find_cross_boundary_calls(
        self, from_path_prefix: str, to_path_prefix: str
    ) -> list[dict]:
        """Find CALLS edges from one path subtree into another.

        Used to enforce layering invariants like "routes must not call db
        directly" against the *actual call graph* rather than text grep.
        """
        return self.query(
            """
            MATCH (a)-[:CALLS]->(b)
            MATCH (ma:Module)-[:DEFINES|DEFINES_METHOD*1..2]->(a)
            MATCH (mb:Module)-[:DEFINES|DEFINES_METHOD*1..2]->(b)
            WHERE ma.path STARTS WITH $from_prefix
              AND mb.path STARTS WITH $to_prefix
            RETURN a.qualified_name AS caller, b.qualified_name AS callee,
                   ma.path AS from_file, mb.path AS to_file
            """,
            {"from_prefix": from_path_prefix, "to_prefix": to_path_prefix},
        )

    def detect_cycles(self, max_depth: int = 10) -> list[list[str]]:
        """Detect dependency cycles in the module IMPORTS graph."""
        rows = self.query(
            f"""
            MATCH path = (m:Module)-[:IMPORTS*2..{max_depth}]->(m)
            RETURN [n IN nodes(path) | n.path] AS cycle
            LIMIT 50
            """
        )
        seen, cycles = set(), []
        for r in rows:
            cycle = r.get("cycle") or []
            key = frozenset(cycle)
            if key not in seen and len(cycle) > 1:
                seen.add(key)
                cycles.append(cycle)
        return cycles

    @staticmethod
    def _escape(value: str) -> str:
        return value.replace("\\", "\\\\").replace("'", "\\'")
