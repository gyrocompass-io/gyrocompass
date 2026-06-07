"""Deep code-graph engine for GyroCompass (Phase 2).

Provides a Memgraph-backed knowledge graph of the codebase (functions, classes,
modules and their CALLS / IMPORTS / INHERITS edges), call-graph blast-radius
analysis, optional semantic code search (Qdrant + UniXcoder), and natural-
language → Cypher querying.

The heavy lifting (multi-language Tree-sitter parsing + graph construction) is
delegated to the embedded `code-graph-rag` engine when available; GyroCompass
adds the architecture-aware analysis on top.

All graph features are optional. When `GRAPH_BACKEND=lite` (the default) or the
graph dependencies are not installed, GyroCompass falls back to the in-process
Tree-sitter indexer + NetworkX. Import this package lazily so the lite path
never pays for the heavy deps.
"""

from gyrocompass.graph.backend import GraphAvailability, GraphBackend, graph_status

__all__ = ["GraphBackend", "GraphAvailability", "graph_status"]
