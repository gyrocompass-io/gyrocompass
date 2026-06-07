#!/bin/sh
# GyroCompass demo seed — creates a fresh, demo-ready sample repo.
#
# Usage:  bash scripts/demo_seed.sh [target_dir]
#
# Produces a 3-layer app (api -> services -> db) initialized with a baseline
# architecture state and a layering invariant, with enforcement hooks installed.
# Everything runs on the lite path (no Docker, no API key).

set -e

TARGET="${1:-/tmp/gyro-demo}"

# Resolve the gyro command: prefer an activated `gyro`, else the repo venv.
if command -v gyro >/dev/null 2>&1; then
    GYRO="gyro"
else
    SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
    GYRO="$SCRIPT_DIR/.venv/bin/gyro"
fi

echo "Seeding GyroCompass demo at: $TARGET"
rm -rf "$TARGET"
mkdir -p "$TARGET/src/api" "$TARGET/src/services" "$TARGET/src/db"
cd "$TARGET"

git init -q
git config user.email demo@gyrocompass.io
git config user.name "GyroCompass Demo"

# ── A clean 3-layer app: api -> services -> db ────────────────────────────────
printf 'def connect():\n    return "db-connection"\n' > src/db/conn.py
printf 'from src.db.conn import connect\n\ndef get_user(uid):\n    conn = connect()\n    return {"id": uid, "conn": conn}\n' > src/services/users.py
printf 'from src.services.users import get_user\n\ndef user_endpoint(uid):\n    return get_user(uid)\n' > src/api/routes.py

# ── Initialize + baseline ─────────────────────────────────────────────────────
"$GYRO" init -r . -n demo-saas >/dev/null 2>&1
"$GYRO" analyze -r . --save >/dev/null 2>&1

# ── A layering invariant: API must not import the DB layer directly ───────────
cat > .gyro/.gyrorules.yaml <<'RULES'
invariants:
  api-no-direct-db:
    description: API handlers must not import the db layer directly; go through services.
    status: active
    enforcement: block
    scope:
      - architecture.src/api
    evidence:
      - pattern: "from src.db"
        type: code_pattern
        description: Route data access through a service, not the db layer directly.
principles: {}
adrs: {}
RULES

# ── Turn on enforcement ───────────────────────────────────────────────────────
cat >> .gyro/config.yaml <<'CFG'

enforcement:
  rules_mode: block
  drift_mode: warn
CFG

"$GYRO" install-hooks -r . >/dev/null 2>&1

# ── Commit the clean baseline so the working tree is clean ────────────────────
git add -A
git commit -q -m "baseline: clean 3-layer app + gyrocompass" || true

echo ""
echo "✓ Demo repo ready at $TARGET"
echo "  Next:  cd $TARGET   then follow DEMO.md"
echo "  Try:   introduce 'from src.db' into src/api/routes.py and commit — it will block."
