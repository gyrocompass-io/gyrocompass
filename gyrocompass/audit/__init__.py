"""Ship-readiness audit for AI-built apps.

`gyro audit` scans a finished codebase for the issues coding agents commonly
leave behind — exposed secrets, PII in logs, vulnerable dependencies, injection
patterns, unguarded sensitive routes, and missing tests/CI/observability — and
emits an agent-ready punch list you can paste straight into Claude Code/Cursor.
"""

from gyrocompass.audit.engine import AuditEngine

__all__ = ["AuditEngine"]
