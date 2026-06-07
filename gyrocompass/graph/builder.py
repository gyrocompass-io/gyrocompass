"""Memgraph graph construction from a repository (Phase 2 build pipeline).

This module is GyroCompass's thin orchestration layer over the embedded
`code-graph-rag` engine. It does NOT re-implement Tree-sitter parsing or graph
construction — that heavy lifting is delegated to code-graph-rag's
`GraphUpdater` + `MemgraphIngestor`. GyroCompass owns the lifecycle (build,
incremental rebuild, export) and the graceful-degradation contract.

The vendored engine lives next to the GyroCompass repo (distribution name
`graph-code`, import package `codebase_rag`). It is an *optional* dependency:
all heavy imports are deferred to inside the methods that need them, and
`available()` returns False rather than raising when the engine or Memgraph is
absent. Callers (the CLI / dashboard) fall back to the lite NetworkX path.

Build passes mirror code-graph-rag's multi-pass design:
  1. Structure (Project/Package/Folder/File/Module)
  2. Definitions (Class/Function/Method/...) per file
  3. CALLS resolution across the whole codebase
followed by method-override processing and optional semantic embeddings — all
driven by a single `GraphUpdater.run()` call.
"""

from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

from loguru import logger

# (H) The vendored code-graph-rag checkout sits three parents up from this file:
#     .../gyrocompass/gyrocompass/graph/builder.py
#       parents[0] = graph/  parents[1] = gyrocompass/  parents[2] = gyrocompass(repo)/
#       parents[3] = gyrocompass_ai_agent_guardrails/  ->  code-graph-rag-main
_VENDORED_PATH = Path(__file__).resolve().parents[3] / "code-graph-rag-main"

# (H) Make the vendored engine importable without polluting installs that ship
#     `codebase_rag` as a real wheel. Only insert the path if the package isn't
#     already importable and the vendored tree actually exists on disk.
if importlib.util.find_spec("codebase_rag") is None and _VENDORED_PATH.exists():
    sys.path.insert(0, str(_VENDORED_PATH))


_INSTALL_HINT = (
    "code-graph-rag engine is not available. Install the graph extra "
    "(pip install gyrocompass[graph]) and ensure the 'codebase_rag' package "
    f"is importable, or place the vendored checkout at {_VENDORED_PATH}."
)


def _import_engine():
    """Lazily import the code-graph-rag building blocks.

    Returns a tuple ``(MemgraphIngestor, GraphUpdater, load_parsers,
    get_language_spec, cypher_module)``. Raises ``RuntimeError`` with install
    guidance when the engine (or its native Tree-sitter deps) can't be loaded.
    """
    try:
        from codebase_rag.constants import (  # noqa: F401
            CYPHER_DELETE_CALLS,
            CYPHER_DELETE_MODULE,
            KEY_PATH,
            EventType,
            SupportedLanguage,
        )
        from codebase_rag.graph_updater import GraphUpdater
        from codebase_rag.language_spec import get_language_spec
        from codebase_rag.parser_loader import load_parsers
        from codebase_rag.services.graph_service import MemgraphIngestor
    except ImportError as exc:  # missing package or native grammar deps
        raise RuntimeError(f"{_INSTALL_HINT} (import error: {exc})") from exc

    return {
        "MemgraphIngestor": MemgraphIngestor,
        "GraphUpdater": GraphUpdater,
        "load_parsers": load_parsers,
        "get_language_spec": get_language_spec,
        "CYPHER_DELETE_MODULE": CYPHER_DELETE_MODULE,
        "CYPHER_DELETE_CALLS": CYPHER_DELETE_CALLS,
        "KEY_PATH": KEY_PATH,
        "EventType": EventType,
        "SupportedLanguage": SupportedLanguage,
    }


class GraphBuilder:
    """Build / rebuild / export a Memgraph code graph for a single repository."""

    def __init__(
        self, repo_path: str | Path, host: str | None = None, port: int | None = None
    ) -> None:
        from gyrocompass.config import settings

        self.repo_path = Path(repo_path).resolve()
        self.host = host or settings.MEMGRAPH_HOST
        self.port = port or settings.MEMGRAPH_PORT
        self.batch_size = settings.MEMGRAPH_BATCH_SIZE
        self.project_name = self.repo_path.name
        # (H) Parsers/queries are expensive to build (native grammar load); cache
        #     them on first use and reuse across build()/rebuild_files() calls.
        self._engine: dict | None = None
        self._parsers = None
        self._queries = None

    # ── Availability ─────────────────────────────────────────────────────────

    def available(self) -> bool:
        """True if `codebase_rag` is importable AND Memgraph is reachable.

        Never raises — used by callers to decide whether to take the deep-graph
        path or fall back to the lite indexer.
        """
        if importlib.util.find_spec("codebase_rag") is None:
            logger.debug("GraphBuilder unavailable: codebase_rag not importable")
            return False
        from gyrocompass.graph.backend import graph_status

        status = graph_status(self.host, self.port)
        if not status.ok:
            logger.debug("GraphBuilder unavailable: {}", status.reason)
        return status.ok

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _engine_mod(self) -> dict:
        if self._engine is None:
            self._engine = _import_engine()
        return self._engine

    def _load_parsers(self):
        if self._parsers is None or self._queries is None:
            eng = self._engine_mod()
            logger.info("Loading Tree-sitter parsers for graph build")
            self._parsers, self._queries = eng["load_parsers"]()
        return self._parsers, self._queries

    def _ingestor(self):
        eng = self._engine_mod()
        return eng["MemgraphIngestor"](
            host=self.host, port=self.port, batch_size=self.batch_size
        )

    def _new_updater(self, ingestor):
        eng = self._engine_mod()
        parsers, queries = self._load_parsers()
        return eng["GraphUpdater"](
            ingestor=ingestor,
            repo_path=self.repo_path,
            parsers=parsers,
            queries=queries,
        )

    # ── Full build ─────────────────────────────────────────────────────────────

    def build(self, clean: bool = True, progress_callback=None) -> dict:
        """Build (or rebuild) the full graph for ``repo_path``.

        Args:
            clean: When True, wipe this project's existing nodes before
                ingesting so the build is idempotent. Uses ``delete_project``
                (scoped to this project) rather than nuking unrelated projects
                that may share the Memgraph instance.
            progress_callback: Optional ``callable(stage: str)`` invoked at each
                lifecycle stage ("connect", "clean", "ingest", "done").

        Returns:
            ``{"nodes": int, "edges": int, "duration_s": float}``.

        Raises:
            RuntimeError: If the engine or Memgraph is unavailable.
        """
        eng = self._engine_mod()
        if not self.repo_path.exists():
            raise RuntimeError(f"repo_path does not exist: {self.repo_path}")

        def _notify(stage: str) -> None:
            if progress_callback is not None:
                try:
                    progress_callback(stage)
                except Exception as exc:  # never let a UI callback break a build
                    logger.warning("progress_callback raised: {}", exc)

        start = time.perf_counter()
        _notify("connect")
        with self._ingestor() as ingestor:
            try:
                ingestor.ensure_constraints()
            except Exception as exc:  # constraints are best-effort
                logger.debug("ensure_constraints skipped: {}", exc)

            if clean:
                _notify("clean")
                logger.info("Cleaning existing graph for project '{}'", self.project_name)
                try:
                    ingestor.delete_project(self.project_name)
                except Exception as exc:
                    logger.warning(
                        "delete_project failed (continuing with MERGE): {}", exc
                    )

            _notify("ingest")
            logger.info("Building graph for repo: {}", self.repo_path)
            updater = self._new_updater(ingestor)
            updater.run()  # multi-pass; flushes internally
            ingestor.flush_all()

            stats = self._collect_stats(ingestor)

        duration = time.perf_counter() - start
        _notify("done")
        result = {
            "nodes": stats["nodes"],
            "edges": stats["edges"],
            "duration_s": round(duration, 3),
        }
        logger.success(
            "Graph build complete: {} nodes, {} edges in {}s",
            result["nodes"],
            result["edges"],
            result["duration_s"],
        )
        return result

    # ── Incremental rebuild ─────────────────────────────────────────────────────

    def rebuild_files(self, changed_files: list[str]) -> dict:
        """Incrementally reconcile the graph for a set of changed files.

        Mirrors code-graph-rag's realtime_updater 5-step reconcile, but driven
        by an explicit file list rather than a filesystem watcher:

          1. Delete each changed file's Module subtree from the graph.
          2. Clear that file's in-memory state in the updater.
          3. Re-parse + re-ingest each file that still exists & is supported.
          4. Recompute CALLS globally (delete all CALLS, re-resolve) to fix the
             "island" problem where cross-file edges go stale.
          5. Flush all collected changes.

        Returns ``{"nodes", "edges", "duration_s", "files": int}``.

        Raises:
            RuntimeError: If the engine or Memgraph is unavailable.
        """
        eng = self._engine_mod()
        if not changed_files:
            logger.debug("rebuild_files: no files supplied; nothing to do")
            return {"nodes": 0, "edges": 0, "duration_s": 0.0, "files": 0}

        get_language_spec = eng["get_language_spec"]
        SupportedLanguage = eng["SupportedLanguage"]
        cypher_delete_module = eng["CYPHER_DELETE_MODULE"]
        cypher_delete_calls = eng["CYPHER_DELETE_CALLS"]
        key_path = eng["KEY_PATH"]

        start = time.perf_counter()
        with self._ingestor() as ingestor:
            updater = self._new_updater(ingestor)

            for raw in changed_files:
                path = Path(raw)
                if not path.is_absolute():
                    path = (self.repo_path / path).resolve()
                # (H) Memgraph stores repo-relative paths on Module.path.
                try:
                    relative = str(path.relative_to(self.repo_path))
                except ValueError:
                    logger.warning(
                        "Skipping {} — outside repo {}", path, self.repo_path
                    )
                    continue

                # (H) Step 1: delete the file's Module subtree.
                ingestor.execute_write(cypher_delete_module, {key_path: relative})

                # (H) Step 2: clear in-memory state for the file.
                updater.remove_file_from_state(path)

                # (H) Step 3: re-parse if the file still exists & is supported.
                if not path.exists():
                    logger.debug("{} deleted; left removed from graph", relative)
                    continue
                lang_config = get_language_spec(path.suffix)
                if (
                    lang_config
                    and isinstance(lang_config.language, SupportedLanguage)
                    and lang_config.language in updater.parsers
                ):
                    result = updater.factory.definition_processor.process_file(
                        path,
                        lang_config.language,
                        updater.queries,
                        updater.factory.structure_processor.structural_elements,
                    )
                    if result:
                        root_node, language = result
                        updater.ast_cache[path] = (root_node, language)
                else:
                    logger.debug("{} is not a supported source file; skipped", relative)

            # (H) Step 4: recompute CALLS across the whole graph.
            logger.info("Recomputing CALLS edges after incremental update")
            ingestor.execute_write(cypher_delete_calls)
            updater._process_function_calls()

            # (H) Step 5: flush everything.
            ingestor.flush_all()

            stats = self._collect_stats(ingestor)

        duration = time.perf_counter() - start
        result = {
            "nodes": stats["nodes"],
            "edges": stats["edges"],
            "duration_s": round(duration, 3),
            "files": len(changed_files),
        }
        logger.success(
            "Incremental rebuild complete for {} file(s): {} nodes, {} edges in {}s",
            result["files"],
            result["nodes"],
            result["edges"],
            result["duration_s"],
        )
        return result

    # ── Export ───────────────────────────────────────────────────────────────

    def export_to_dict(self) -> dict:
        """Export the entire graph as a plain dict (nodes + relationships).

        Used by the lite/offline path and the web dashboard. Delegates to
        ``MemgraphIngestor.export_graph_to_dict()`` and normalises the result to
        a JSON-serialisable ``dict`` regardless of the engine's return type
        (which may be a TypedDict or dataclass-like object).

        Raises:
            RuntimeError: If the engine or Memgraph is unavailable.
        """
        self._engine_mod()  # ensure import / raise clear error
        with self._ingestor() as ingestor:
            exported = ingestor.export_graph_to_dict()
        return self._normalise_export(exported)

    # ── Stats / normalisation ────────────────────────────────────────────────

    @staticmethod
    def _collect_stats(ingestor) -> dict:
        """Count nodes and edges via the ingestor's read path."""
        try:
            node_rows = ingestor.fetch_all("MATCH (n) RETURN count(n) AS c")
            edge_rows = ingestor.fetch_all("MATCH ()-[r]->() RETURN count(r) AS c")
            nodes = int(node_rows[0]["c"]) if node_rows else 0
            edges = int(edge_rows[0]["c"]) if edge_rows else 0
        except Exception as exc:
            logger.warning("Failed to collect graph stats: {}", exc)
            nodes = edges = 0
        return {"nodes": nodes, "edges": edges}

    @staticmethod
    def _normalise_export(exported) -> dict:
        """Coerce the engine's export into a plain JSON-safe dict."""
        if isinstance(exported, dict):
            return dict(exported)
        # TypedDict/NamedTuple/dataclass-ish objects
        for attr in ("_asdict", "model_dump", "dict"):
            fn = getattr(exported, attr, None)
            if callable(fn):
                try:
                    return dict(fn())
                except Exception:
                    pass
        nodes = getattr(exported, "nodes", None)
        rels = getattr(exported, "relationships", None)
        metadata = getattr(exported, "metadata", None)
        if nodes is not None or rels is not None:
            return {
                "nodes": list(nodes or []),
                "relationships": list(rels or []),
                "metadata": metadata,
            }
        # Last resort: best-effort dict() conversion.
        try:
            return dict(exported)
        except Exception:
            return {"nodes": [], "relationships": [], "metadata": None}
