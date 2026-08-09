#!/bin/bash
# No-hardware smoke test for tools/pi/izzy-orders-update. Builds a local bare
# "origin" with an order-server/ tree, a blobless sparse clone of it (the same
# shape izzy-orders-setup produces), and drives the updater through its four
# paths via the IZZY_* overrides — no Pi, no systemd, no izzy user, no network.
#
#   1. no-change      -> exits 0 without fetching
#   2. new commit     -> resets to it, restarts, health-checks
#   3. requirements.txt change -> reinstalls deps (stub pip records the call)
#   4. health failure -> rolls back to the previous commit, exits 1

set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
UPDATER="$HERE/../tools/pi/izzy-orders-update"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
cd "$TMP"

say()  { printf '  %s\n' "$*"; }
fail() { echo "FAIL: $*" >&2; exit 1; }

# --- a local "origin" with order-server/ ------------------------------------
git init -q --bare origin.git
# local transport needs this opt-in for --filter=blob:none to actually filter
git -C origin.git config uploadpack.allowfilter true
git clone -q origin.git seed 2>/dev/null
(
  cd seed
  git config user.email t@t; git config user.name t
  mkdir order-server
  echo 'print("v1")' > order-server/main.py
  echo 'fastapi>=0.115' > order-server/requirements.txt
  git add -A && git commit -qm v1 && git push -q origin HEAD:master
)

# --- the sparse clone izzy-orders-setup would make ---------------------------
ROOT="$TMP/izzy-root"
mkdir -p "$ROOT"
git clone -q --filter=blob:none --no-checkout --single-branch --branch master \
  "file://$TMP/origin.git" "$ROOT/repo"
git -C "$ROOT/repo" sparse-checkout set order-server
git -C "$ROOT/repo" checkout -q master

# venv stub: just a pip that records its invocations
mkdir -p "$ROOT/venv/bin"
cat > "$ROOT/venv/bin/pip" <<EOF
#!/bin/bash
echo "pip \$*" >> "$TMP/pip.log"
EOF
chmod +x "$ROOT/venv/bin/pip"

CONF="$TMP/order-server.conf"
printf 'ORDER_REPO=local\nORDER_BRANCH=master\n' > "$CONF"

# systemctl stub records restarts; health is a flag file the stub curl checks
cat > "$TMP/systemctl" <<EOF
#!/bin/bash
echo "systemctl \$*" >> "$TMP/systemctl.log"
EOF
chmod +x "$TMP/systemctl"

run_updater() {  # \$1 = health mode: ok | dead
  IZZY_ROOT="$ROOT" IZZY_CONF="$CONF" IZZY_SYSTEMCTL="$TMP/systemctl" \
  IZZY_RUNUSER=" " IZZY_HEALTH_URL="$1" PATH="$TMP/stubbin:$PATH" \
    "$UPDATER"
}
# stub curl: succeeds iff the "URL" is the literal string ok
mkdir -p "$TMP/stubbin"
cat > "$TMP/stubbin/curl" <<'EOF'
#!/bin/bash
for a in "$@"; do [ "$a" = ok ] && exit 0; done
exit 22
EOF
chmod +x "$TMP/stubbin/curl"
# the health-fail path sleeps 2s x30 tries; stub sleep keeps the test fast
cat > "$TMP/stubbin/sleep" <<'EOF'
#!/bin/bash
exit 0
EOF
chmod +x "$TMP/stubbin/sleep"

echo "==> izzy-orders-update smoke"

# 1. no-change: exits 0, no restart
run_updater ok
[ ! -f "$TMP/systemctl.log" ] || fail "no-change run restarted the service"
say "no-change exits clean without touching the service"

# 2. new commit deploys
( cd seed && echo 'print("v2")' > order-server/main.py \
  && git commit -qam v2 && git push -q origin HEAD:master )
v2=$(git -C seed rev-parse HEAD)
run_updater ok
[ "$(git -C "$ROOT/repo" rev-parse HEAD)" = "$v2" ] || fail "did not advance to v2"
grep -q "restart izzy-orders" "$TMP/systemctl.log" || fail "no restart after deploy"
[ ! -f "$TMP/pip.log" ] || fail "pip ran though requirements were unchanged"
say "new commit deploys and restarts, no needless pip"

# 3. requirements change triggers pip
( cd seed && echo 'uvicorn[standard]>=0.30' >> order-server/requirements.txt \
  && git commit -qam reqs && git push -q origin HEAD:master )
run_updater ok
grep -q "install" "$TMP/pip.log" || fail "requirements change did not reinstall deps"
say "requirements change reinstalls deps"

# 4. health failure rolls back
good=$(git -C "$ROOT/repo" rev-parse HEAD)
( cd seed && echo 'print("broken")' > order-server/main.py \
  && git commit -qam broken && git push -q origin HEAD:master )
rc=0; run_updater dead || rc=$?
[ "$rc" -ne 0 ] || fail "health failure exited 0"
[ "$(git -C "$ROOT/repo" rev-parse HEAD)" = "$good" ] || fail "did not roll back"
say "unhealthy deploy rolls back to the previous commit"

echo "==> izzy-orders-update smoke: green"
