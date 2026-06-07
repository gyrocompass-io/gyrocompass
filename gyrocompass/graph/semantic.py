"""Semantic code search over the GyroCompass graph (Qdrant + UniXcoder).

Pairs the structural code graph with a vector index so questions like "where is
retry/backoff logic implemented?" resolve by *meaning*, not just by name. Every
Function/Method node in Memgraph is embedded with code-graph-rag's UniXcoder
embedder (768-dim) and stored in Qdrant with the Memgraph node id as the point
id, so search results hydrate straight back to graph nodes.

This is the most dependency-heavy feature in Phase 2 (torch + transformers +
qdrant-client), so it is strictly opt-in: gated behind `SEMANTIC_ENABLED`, all
heavy imports deferred and guarded, and `available()` returns False instead of
raising when anything is missing. The lite path never pays for these imports.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from loguru import logger

# Qdrant collection contract — kept identical to code-graph-rag's vector_store
# so the two engines can share an index if pointed at the same store.
_COLLECTION_NAME = "code_embeddings"
_VECTOR_DIM = 768
_PAYLOAD_NODE_ID = "node_id"
_PAYLOAD_QUALIFIED_NAME = "qualified_name"

# Cypher to pull all embeddable symbols (mirrors code-graph-rag's
# CYPHER_QUERY_EMBEDDINGS). Imported lazily from the engine when present,
# otherwise this local copy is used so search works even without the engine
# importable for *querying* (embedding still requires the engine).
_CYPHER_QUERY_EMBEDDINGS = """
MATCH (m:Module)-[:DEFINES]->(n)
WHERE n:Function OR n:Method
RETURN id(n) AS node_id, n.qualified_name AS qualified_name,
       n.start_line AS start_line, n.end_line AS end_line,
       m.path AS path
ORDER BY n.qualified_name
"""

_SEMANTIC_INSTALL_HINT = (
    "Semantic search requires the 'semantic' extra "
    "(pip install gyrocompass[semantic]) which provides qdrant-client, torch, "
    "and transformers, plus the code-graph-rag embedder."
)


def _has(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


class SemanticSearch:
    """UniXcoder + Qdrant semantic search bound to a GraphBackend."""

    def __init__(
        self,
        backend,
        qdrant_url: str | None = None,
        qdrant_path: str | None = None,
    ) -> None:
        """Args:
        backend: a ``gyrocompass.graph.backend.GraphBackend`` used both to
            enumerate symbols for indexing and to hydrate search hits.
        qdrant_url: optional Qdrant server URL (e.g. http://localhost:6333).
            Takes precedence over ``qdrant_path``.
        qdrant_path: optional embedded-mode storage directory.

        When neither is supplied, falls back to ``settings.QDRANT_URL`` /
        ``settings.QDRANT_PATH``.
        """
        from gyrocompass.config import settings

        self.backend = backend
        self.qdrant_url = qdrant_url or settings.QDRANT_URL
        self.qdrant_path = qdrant_path or settings.QDRANT_PATH
        self._semantic_enabled = settings.SEMANTIC_ENABLED
        self._client = None

    # ── Availability ─────────────────────────────────────────────────────────

    def available(self) -> bool:
        """True only if semantic search is enabled AND all deps are importable.

        Checks ``SEMANTIC_ENABLED`` plus qdrant_client and torch+transformers.
        Never raises.
        """
        if not self._semantic_enabled:
            logger.debug("SemanticSearch disabled: SEMANTIC_ENABLED is False")
            return False
        if not _has("qdrant_client"):
            logger.debug("SemanticSearch unavailable: qdrant_client not installed")
            return False
        if not (_has("torch") and _has("transformers")):
            logger.debug("SemanticSearch unavailable: torch/transformers not installed")
            return False
        return True

    def _require_available(self) -> None:
        if not self._semantic_enabled:
            raise RuntimeError(
                "Semantic search is disabled. Set GYRO_SEMANTIC_ENABLED=true to enable it."
            )
        if not (_has("qdrant_client") and _has("torch") and _has("transformers")):
            raise RuntimeError(_SEMANTIC_INSTALL_HINT)

    # ── Lazy clients ───────────────────────────────────────────────────────────

    def _get_client(self):
        """Return a connected Qdrant client, creating the collection if needed."""
        if self._client is not None:
            return self._client
        self._require_available()
        from qdrant_client import QdrantClient
        from qdrant_client.models import Distance, VectorParams

        if self.qdrant_url:
            client = QdrantClient(url=self.qdrant_url)
        else:
            # Embedded on-disk store.
            Path(self.qdrant_path).parent.mkdir(parents=True, exist_ok=True)
            client = QdrantClient(path=self.qdrant_path)

        if not client.collection_exists(_COLLECTION_NAME):
            client.create_collection(
                collection_name=_COLLECTION_NAME,
                vectors_config=VectorParams(
                    size=_VECTOR_DIM, distance=Distance.COSINE
                ),
            )
        self._client = client
        return client

    @staticmethod
    def _embed(code: str) -> list[float]:
        """Embed code via code-graph-rag's UniXcoder embedder."""
        try:
            from codebase_rag.embedder import embed_code
        except ImportError as exc:
            raise RuntimeError(f"{_SEMANTIC_INSTALL_HINT} (import error: {exc})") from exc
        return embed_code(code)

    @staticmethod
    def _embeddings_query() -> str:
        """Prefer the engine's canonical query; fall back to the local copy."""
        try:
            from codebase_rag.constants import CYPHER_QUERY_EMBEDDINGS

            return CYPHER_QUERY_EMBEDDINGS
        except ImportError:
            return _CYPHER_QUERY_EMBEDDINGS

    # ── Indexing ───────────────────────────────────────────────────────────────

    def index_from_graph(self, backend=None) -> int:
        """Embed every Function/Method node and upsert into Qdrant.

        Pulls (node_id, qualified_name, start_line, end_line, module path) for
        all callable symbols, reads each symbol's source slice from disk, embeds
        it, and upserts a point keyed by the Memgraph node id with payload
        ``{node_id, qualified_name}``.

        Args:
            backend: optional override; defaults to the bound backend.

        Returns:
            The number of symbols successfully embedded and stored.

        Raises:
            RuntimeError: If semantic deps / engine are unavailable.
        """
        self._require_available()
        backend = backend or self.backend
        from qdrant_client.models import PointStruct

        client = self._get_client()
        rows = backend.query(self._embeddings_query())
        logger.info("Embedding {} callable symbols from the graph", len(rows))

        points: list = []
        repo_cache: dict[str, list[str]] = {}
        embedded = 0
        for row in rows:
            node_id = row.get("node_id")
            qualified_name = row.get("qualified_name")
            path = row.get("path")
            start = row.get("start_line")
            end = row.get("end_line")
            if node_id is None or not qualified_name or not path:
                continue

            source = self._read_source(path, start, end, repo_cache)
            if not source.strip():
                continue
            try:
                vector = self._embed(source)
            except Exception as exc:
                logger.warning("Failed to embed {}: {}", qualified_name, exc)
                continue

            points.append(
                PointStruct(
                    id=int(node_id),
                    vector=vector,
                    payload={
                        _PAYLOAD_NODE_ID: int(node_id),
                        _PAYLOAD_QUALIFIED_NAME: qualified_name,
                    },
                )
            )
            embedded += 1

            # Upsert in batches to bound memory.
            if len(points) >= 128:
                client.upsert(collection_name=_COLLECTION_NAME, points=points)
                points = []

        if points:
            client.upsert(collection_name=_COLLECTION_NAME, points=points)

        logger.success("Semantic index built: {} symbols embedded", embedded)
        return embedded

    @staticmethod
    def _read_source(
        path: str, start, end, cache: dict[str, list[str]]
    ) -> str:
        """Read the source slice [start, end] (1-based, inclusive) for a symbol."""
        try:
            lines = cache.get(path)
            if lines is None:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    lines = fh.readlines()
                cache[path] = lines
        except OSError as exc:
            logger.debug("Could not read {}: {}", path, exc)
            return ""

        if not isinstance(start, int) or not isinstance(end, int):
            return "".join(lines)
        # Tree-sitter lines are 1-based; clamp defensively.
        lo = max(start - 1, 0)
        hi = min(end, len(lines))
        if lo >= hi:
            return "".join(lines[lo : lo + 1])
        return "".join(lines[lo:hi])

    # ── Search ───────────────────────────────────────────────────────────────

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        """Semantic top-k search for a natural-language ``query``.

        Embeds the query, runs a cosine top-k search in Qdrant, and returns
        hits as ``[{"qualified_name", "score", "node_id"}]`` ordered by score
        descending. Qualified names come from the Qdrant payload and fall back
        to a graph lookup by node id if absent.

        Raises:
            RuntimeError: If semantic deps / engine are unavailable.
        """
        self._require_available()
        client = self._get_client()
        vector = self._embed(query)

        response = client.query_points(
            collection_name=_COLLECTION_NAME,
            query=vector,
            limit=top_k,
            with_payload=True,
        )
        hits = getattr(response, "points", response)

        results: list[dict] = []
        for hit in hits:
            payload = getattr(hit, "payload", None) or {}
            node_id = payload.get(_PAYLOAD_NODE_ID, getattr(hit, "id", None))
            qualified_name = payload.get(_PAYLOAD_QUALIFIED_NAME)
            if not qualified_name and node_id is not None:
                qualified_name = self._hydrate_name(node_id)
            results.append(
                {
                    "qualified_name": qualified_name,
                    "score": float(getattr(hit, "score", 0.0)),
                    "node_id": node_id,
                }
            )
        return results

    def _hydrate_name(self, node_id) -> str | None:
        """Look up a node's qualified_name from the graph by internal id."""
        try:
            rows = self.backend.query(
                "MATCH (n) WHERE id(n) = $nid RETURN n.qualified_name AS qn",
                {"nid": int(node_id)},
            )
        except Exception as exc:
            logger.debug("Could not hydrate node {}: {}", node_id, exc)
            return None
        return rows[0]["qn"] if rows and rows[0].get("qn") else None
