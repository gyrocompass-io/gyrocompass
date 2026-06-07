#!/bin/sh
# Creates a small "AI-built app with issues" to demo `gyro audit`.
# Usage:  bash scripts/demo_vuln_app.sh [target_dir]   then:  cd <dir> && gyro audit

set -e
TARGET="${1:-/tmp/gyro-vuln-app}"
rm -rf "$TARGET"
mkdir -p "$TARGET/src/payments" "$TARGET/src/api"
cd "$TARGET"

# Hardcoded secret + SQL injection + PII in logs.
# The fake Stripe key is assembled at runtime so THIS script doesn't itself trip
# secret scanners — the GENERATED file still contains the full key, which is the
# whole point: `gyro audit` must detect it. (Unquoted heredoc expands ${_SK}.)
_SK="sk_li""ve_51H8xExampleKeyAbc123Def456Ghi"
cat > src/payments/checkout.py <<PY
STRIPE_KEY = "${_SK}"

def create_charge(user, amount):
    q = f"INSERT INTO charges (uid, amt) VALUES ({user.id}, {amount})"
    db.execute(q)
    print(f"Charged {user.email} \${amount}")
    return True
PY

# Sensitive route with no auth
cat > src/api/admin.py <<'PY'
@app.get("/admin/delete-all")
def delete_everything():
    return db.wipe()
PY

# Known-vulnerable dependencies, no tests, no CI
printf 'requests==2.25.0\npyyaml==5.1\nflask==2.0.1\n' > requirements.txt

echo "Vulnerable demo app created at: $TARGET"
echo "Run:  cd $TARGET && gyro audit          (or: gyro audit -o markdown)"
