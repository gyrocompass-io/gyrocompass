"""Architectural memory — a beads-inspired SQLite knowledge graph.

Turns GyroCompass from "produces a drift report and forgets it" into a system
with memory: drift events, architecture decisions (ADRs), and remediation work
items become typed *nodes*, linked by typed *edges* (caused-by, violates,
remediated-by, supersedes, discovered-from). An append-only event log records
every state change, and a "ready work" query surfaces unblocked, unresolved
architectural debt.

Design principles:
  • One node table + one typed-edge table (everything-is-a-typed-edge).
  • Hash-based content IDs → conflict-free creation across agents/branches/CI.
  • Append-only events table → drift-over-time audit trail.
  • "ready work" computed as a query over the graph (unblocked + open).

Plain SQLite (stdlib) — zero infra, single file at .gyro/memory.db. This is the
data behind the dashboard's "state evolution" and "activity feed".
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from loguru import logger


# ── Vocabulary ────────────────────────────────────────────────────────────────


class NodeType(str, Enum):
    drift_event = "drift_event"
    decision = "decision"          # ADR
    remediation = "remediation"    # work item to fix drift
    rule = "rule"                  # a principle/invariant snapshot
    component = "component"        # architecture element


class NodeStatus(str, Enum):
    open = "open"
    in_progress = "in_progress"
    resolved = "resolved"
    accepted = "accepted"          # for decisions
    superseded = "superseded"
    wont_fix = "wont_fix"


class EdgeType(str, Enum):
    caused_by = "caused-by"            # remediation caused-by drift_event (provenance)
    violates = "violates"              # drift_event violates rule/decision
    remediated_by = "remediated-by"    # drift_event remediated-by remediation (provenance)
    supersedes = "supersedes"          # decision supersedes decision
    discovered_from = "discovered-from"  # work discovered while doing other work (provenance)
    depends_on = "depends-on"          # work item BLOCKED until target is resolved
    relates_to = "relates-to"
    affects = "affects"                # change affects component


# Edge types that block "ready" status: a node with an unresolved `depends-on`
# target isn't actionable yet. Provenance edges (caused-by/remediated-by/
# discovered-from) are NOT blocking — an open drift is the *reason* a fix is
# ready, not a blocker on it.
_BLOCKING_EDGES = (EdgeType.depends_on.value,)


@dataclass
class Node:
    id: str
    type: str
    title: str
    body: str = ""
    status: str = NodeStatus.open.value
    severity: str | None = None
    priority: int = 2  # 0=highest .. 4=lowest
    metadata: dict = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""


@dataclass
class Edge:
    src: str
    dst: str
    type: str
    metadata: dict = field(default_factory=dict)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_id(prefix: str, *parts: str) -> str:
    """Collision-resistant content ID: prefix + first 8 hex of SHA-256.

    Mirrors beads' approach so concurrent creators (parallel agents, CI runs on
    different branches) don't collide on sequential IDs and merge cleanly.
    """
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}-{digest[:8]}"


# ── Schema ────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    id          TEXT PRIMARY KEY,
    type        TEXT NOT NULL,
    title       TEXT NOT NULL,
    body        TEXT DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'open',
    severity    TEXT,
    priority    INTEGER NOT NULL DEFAULT 2,
    metadata    TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS edges (
    src       TEXT NOT NULL,
    dst       TEXT NOT NULL,
    type      TEXT NOT NULL,
    metadata  TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (src, dst, type),
    FOREIGN KEY (src) REFERENCES nodes(id) ON DELETE CASCADE,
    FOREIGN KEY (dst) REFERENCES nodes(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id     TEXT,
    event_type  TEXT NOT NULL,
    actor       TEXT,
    old_value   TEXT,
    new_value   TEXT,
    comment     TEXT,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(type);
CREATE INDEX IF NOT EXISTS idx_nodes_status ON nodes(status);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges(dst, type);
CREATE INDEX IF NOT EXISTS idx_events_node ON events(node_id);
"""


class MemoryStore:
    """SQLite-backed architectural memory graph."""

    def __init__(self, repo_path: Path | str | None = None, db_path: Path | None = None) -> None:
        if db_path is not None:
            self.db_path = Path(db_path)
        else:
            from gyrocompass.config import get_gyro_dir

            self.db_path = get_gyro_dir(repo_path) / "memory.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> MemoryStore:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ── Node CRUD ────────────────────────────────────────────────────────────

    def add_node(
        self,
        type: str,
        title: str,
        body: str = "",
        *,
        status: str = NodeStatus.open.value,
        severity: str | None = None,
        priority: int = 2,
        metadata: dict | None = None,
        node_id: str | None = None,
        actor: str = "gyrocompass",
        created_at: str | None = None,
    ) -> Node:
        """Insert a node (idempotent by hash id). Returns the node.

        `created_at` (ISO string) lets callers backfill historical events with a
        specific timestamp (used by importers and demo seeding); defaults to now.
        """
        nid = node_id or _hash_id(_prefix_for(type), type, title, body)
        now = created_at or _now()
        existing = self.get_node(nid)
        if existing:
            return existing
        node = Node(
            id=nid, type=type, title=title, body=body, status=status,
            severity=severity, priority=priority, metadata=metadata or {},
            created_at=now, updated_at=now,
        )
        self._conn.execute(
            "INSERT INTO nodes (id, type, title, body, status, severity, priority, metadata, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (node.id, node.type, node.title, node.body, node.status, node.severity,
             node.priority, json.dumps(node.metadata), node.created_at, node.updated_at),
        )
        self._log(nid, "created", actor, None, status)
        self._conn.commit()
        logger.debug("memory: added {} node {}", type, nid)
        return node

    def get_node(self, node_id: str) -> Node | None:
        row = self._conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
        return _row_to_node(row) if row else None

    def update_status(
        self, node_id: str, status: str, *, actor: str = "gyrocompass", comment: str | None = None
    ) -> None:
        node = self.get_node(node_id)
        if not node:
            raise KeyError(f"No such node: {node_id}")
        self._conn.execute(
            "UPDATE nodes SET status = ?, updated_at = ? WHERE id = ?",
            (status, _now(), node_id),
        )
        self._log(node_id, "status_changed", actor, node.status, status, comment)
        self._conn.commit()

    def list_nodes(
        self, *, type: str | None = None, status: str | None = None, limit: int = 200
    ) -> list[Node]:
        q = "SELECT * FROM nodes WHERE 1=1"
        params: list = []
        if type:
            q += " AND type = ?"
            params.append(type)
        if status:
            q += " AND status = ?"
            params.append(status)
        q += " ORDER BY priority ASC, created_at DESC LIMIT ?"
        params.append(limit)
        return [_row_to_node(r) for r in self._conn.execute(q, params).fetchall()]

    # ── Edges ────────────────────────────────────────────────────────────────

    def link(self, src: str, dst: str, type: str, metadata: dict | None = None, actor: str = "gyrocompass") -> None:
        """Create a typed edge src -[type]-> dst (idempotent)."""
        self._conn.execute(
            "INSERT OR IGNORE INTO edges (src, dst, type, metadata) VALUES (?,?,?,?)",
            (src, dst, type, json.dumps(metadata or {})),
        )
        self._log(src, "linked", actor, None, f"{type}->{dst}")
        self._conn.commit()

    def edges_from(self, node_id: str) -> list[Edge]:
        rows = self._conn.execute("SELECT * FROM edges WHERE src = ?", (node_id,)).fetchall()
        return [Edge(r["src"], r["dst"], r["type"], json.loads(r["metadata"])) for r in rows]

    def edges_to(self, node_id: str) -> list[Edge]:
        rows = self._conn.execute("SELECT * FROM edges WHERE dst = ?", (node_id,)).fetchall()
        return [Edge(r["src"], r["dst"], r["type"], json.loads(r["metadata"])) for r in rows]

    def neighbors(self, node_id: str) -> dict:
        """Full local graph around a node — for the dashboard detail view."""
        return {
            "node": self.get_node(node_id),
            "outgoing": [e.__dict__ for e in self.edges_from(node_id)],
            "incoming": [e.__dict__ for e in self.edges_to(node_id)],
        }

    # ── Ready work (the beads-style query) ─────────────────────────────────────

    def ready_remediation(self, limit: int = 50) -> list[Node]:
        """Remediation items that are open AND not blocked by an unresolved blocker.

        A remediation is "ready" when every node it depends on (via a blocking
        edge) is resolved/accepted/superseded — i.e. nothing open stands in the
        way. Ordered by priority then severity of the drift it fixes.
        """
        candidates = self.list_nodes(type=NodeType.remediation.value, status=NodeStatus.open.value, limit=500)
        ready: list[Node] = []
        for node in candidates:
            if self._is_ready(node.id):
                ready.append(node)
            if len(ready) >= limit:
                break
        ready.sort(key=lambda n: (n.priority, _severity_rank(n.severity)))
        return ready

    def _is_ready(self, node_id: str) -> bool:
        """True if no outgoing blocking edge points to an unresolved node."""
        terminal = {NodeStatus.resolved.value, NodeStatus.accepted.value,
                    NodeStatus.superseded.value, NodeStatus.wont_fix.value}
        for edge in self.edges_from(node_id):
            if edge.type in _BLOCKING_EDGES:
                target = self.get_node(edge.dst)
                if target and target.status not in terminal:
                    return False
        return True

    # ── Drift integration ──────────────────────────────────────────────────────

    def record_drift_report(self, report, *, pr: int | None = None, actor: str = "ci") -> list[str]:
        """Persist a DriftReport's events as drift_event nodes, link rule violations.

        Returns the list of created/updated node ids. Idempotent — re-recording
        the same event (same title+file) updates rather than duplicates.
        """
        created: list[str] = []
        for ev in report.events:
            body = ev.description
            meta = {
                "drift_type": getattr(ev.type, "value", str(ev.type)),
                "file": ev.file,
                "line": ev.line,
                "element": ev.element,
                "pr": pr,
            }
            node = self.add_node(
                NodeType.drift_event.value,
                title=ev.title,
                body=body,
                severity=getattr(ev.severity, "value", str(ev.severity)),
                metadata=meta,
                actor=actor,
            )
            created.append(node.id)
            # Link to violated rule if present
            rule_id = getattr(ev, "rule_id", None)
            if rule_id:
                rule_node = self.add_node(
                    NodeType.rule.value, title=rule_id, node_id=f"rule-{rule_id}", actor=actor
                )
                self.link(node.id, rule_node.id, EdgeType.violates.value, actor=actor)
        return created

    def open_remediation_for_drift(
        self, drift_node_id: str, title: str, *, priority: int = 1, actor: str = "gyrocompass"
    ) -> Node:
        """Create a remediation work item linked to a drift event (caused-by)."""
        drift = self.get_node(drift_node_id)
        rem = self.add_node(
            NodeType.remediation.value,
            title=title,
            severity=drift.severity if drift else None,
            priority=priority,
            actor=actor,
        )
        self.link(rem.id, drift_node_id, EdgeType.caused_by.value, actor=actor)
        self.link(drift_node_id, rem.id, EdgeType.remediated_by.value, actor=actor)
        return rem

    # ── State evolution / activity feed ──────────────────────────────────────

    def activity(self, limit: int = 50) -> list[dict]:
        """Recent events for the dashboard activity feed."""
        rows = self._conn.execute(
            "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> dict:
        def _count(q, *p):
            return self._conn.execute(q, p).fetchone()[0]

        return {
            "nodes": _count("SELECT count(*) FROM nodes"),
            "edges": _count("SELECT count(*) FROM edges"),
            "open_drift": _count(
                "SELECT count(*) FROM nodes WHERE type=? AND status=?",
                NodeType.drift_event.value, NodeStatus.open.value,
            ),
            "open_remediation": _count(
                "SELECT count(*) FROM nodes WHERE type=? AND status=?",
                NodeType.remediation.value, NodeStatus.open.value,
            ),
            "decisions": _count("SELECT count(*) FROM nodes WHERE type=?", NodeType.decision.value),
            "events": _count("SELECT count(*) FROM events"),
        }

    # ── internals ──────────────────────────────────────────────────────────────

    def _log(self, node_id, event_type, actor, old, new, comment=None) -> None:
        self._conn.execute(
            "INSERT INTO events (node_id, event_type, actor, old_value, new_value, comment, created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (node_id, event_type, actor, old, new, comment, _now()),
        )


# ── Helpers ───────────────────────────────────────────────────────────────────


def _prefix_for(node_type: str) -> str:
    return {
        NodeType.drift_event.value: "drift",
        NodeType.decision.value: "adr",
        NodeType.remediation.value: "fix",
        NodeType.rule.value: "rule",
        NodeType.component.value: "comp",
    }.get(node_type, "node")


def _severity_rank(severity: str | None) -> int:
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    return order.get(severity or "", 5)


def _row_to_node(row: sqlite3.Row) -> Node:
    return Node(
        id=row["id"], type=row["type"], title=row["title"], body=row["body"],
        status=row["status"], severity=row["severity"], priority=row["priority"],
        metadata=json.loads(row["metadata"]), created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
