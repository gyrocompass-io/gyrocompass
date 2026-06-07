#!/usr/bin/env python3
"""Seed a repo's architectural-memory with a realistic drift history.

For demos only — paints a believable "since we installed in June" evolution so
the `gyro docs` activity heatmap + timeline look alive. Real usage accumulates
this naturally as PRs land.

Usage:
    python scripts/demo_seed_history.py /path/to/repo
"""

from __future__ import annotations

import sys
from pathlib import Path

from gyrocompass.memory import MemoryStore, NodeType, NodeStatus

# (date, pr, title, severity, drift_type, file)
_HISTORY = [
    ("2026-06-02T09:10:00", None, "🧭 GyroCompass installed — baseline captured", "info", "baseline", None),
    ("2026-06-04T14:22:00", 401, "Split AnalysisOrchestrator into PRAnalysisOrchestrator", "low", "component_modified", "meridian/services/order_service.py"),
    ("2026-06-06T11:05:00", 404, "Add token-bucket rate limiting at the gateway", "medium", "component_added", "meridian/api/main.py"),
    ("2026-06-09T10:40:00", 407, "Tighten ADR-012 enforcement on edge handlers", "info", "rule_violation", "meridian/api/routes/accounts.py"),
    ("2026-06-09T16:18:00", 408, "New receipt notification flow", "medium", "relationship_added", "meridian/services/notification_service.py"),
    ("2026-06-12T13:30:00", 410, "Migrate rate limiter counters to Redis", "medium", "tech_stack_change", "meridian/clients/sqs_client.py"),
    ("2026-06-15T09:55:00", 412, "Bulk export reads DB directly from edge handler", "high", "rule_violation", "meridian/api/routes/orders.py"),
    ("2026-06-15T09:55:30", 412, "Undocumented relationship: api/routes → db/repositories", "medium", "relationship_added", "meridian/api/routes/orders.py"),
    ("2026-06-15T10:02:00", 412, "Order entity gained an export_status field", "low", "data_model_change", "meridian/db/models.py"),
    ("2026-06-18T15:12:00", 415, "Refactor payment service retries", "low", "component_modified", "meridian/services/payment_service.py"),
    ("2026-06-21T11:20:00", 418, "Undocumented analytics component added", "medium", "component_added", "meridian/analytics/tracker.py"),
    ("2026-06-21T14:47:00", 419, "PII (email) written to logs in notifications", "high", "rule_violation", "meridian/services/notification_service.py"),
    ("2026-06-25T10:08:00", 421, "New bulk-refund API endpoint", "medium", "api_surface_change", "meridian/api/routes/payments.py"),
    ("2026-06-28T16:33:00", 424, "Session token expiry validation removed", "critical", "capability_regression", "meridian/core/security.py"),
    ("2026-06-28T18:10:00", 424, "Remediation: restore token expiry check", "high", "remediation", "meridian/core/security.py"),
]


def seed(repo_path: str) -> int:
    store = MemoryStore(repo_path)
    n = 0
    try:
        for date, pr, title, severity, dtype, file in _HISTORY:
            if dtype == "remediation":
                store.add_node(
                    NodeType.remediation.value, title, severity=severity, priority=0,
                    status=NodeStatus.resolved.value, created_at=date,
                    metadata={"pr": pr, "file": file},
                )
            else:
                store.add_node(
                    NodeType.drift_event.value, title,
                    body=title, severity=severity, created_at=date,
                    metadata={"pr": pr, "file": file, "drift_type": dtype},
                )
            n += 1
    finally:
        store.close()
    return n


if __name__ == "__main__":
    repo = sys.argv[1] if len(sys.argv) > 1 else "."
    if not Path(repo).exists():
        print(f"repo not found: {repo}", file=sys.stderr)
        raise SystemExit(1)
    count = seed(repo)
    print(f"✓ Seeded {count} historical drift events into {repo}/.gyro/memory.db")
    print("  Run `gyro docs` to see the activity heatmap + timeline come alive.")
