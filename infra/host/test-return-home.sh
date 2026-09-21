#!/usr/bin/env bash
# Proves the "return home" block in deploy-zero-downtime.sh, with docker/caddy/
# systemctl/curl stubbed. Asserts three things the real deploy must guarantee:
#   A. deploying INTO green ends with traffic back on :8000 and green stopped
#   B. deploying INTO backend (coming from green) just stops green
#   C. the final guard trips loudly if `backend` is not running
# Run: bash infra/host/test-return-home.sh
set -uo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)/deploy-zero-downtime.sh"
WORK=$(mktemp -d); trap 'rm -rf "$WORK"' EXIT
BIN="$WORK/bin"; mkdir -p "$BIN"

# ---- extract the block under test (from "6. RETURN HOME" to end of file) -----------
START=$(grep -n '^# ---- 6\. RETURN HOME' "$SRC" | cut -d: -f1)
[ -n "$START" ] || { echo "FAIL: could not find the RETURN HOME block"; exit 1; }
tail -n +"$START" "$SRC" > "$WORK/block.sh"

# ---- stubs -------------------------------------------------------------------------
cat > "$BIN/docker" <<'EOF'
#!/usr/bin/env bash
case "$1 $2" in
  "inspect -f")   echo "${STUB_HEALTH:-healthy}" ;;
  "ps --format")  [ "${STUB_BACKEND_RUNNING:-1}" = 1 ] && echo alpha-finance-backend ;;
  *) : ;;
esac
EOF
cat > "$BIN/systemctl" <<'EOF'
#!/usr/bin/env bash
echo "systemctl $*" >> "$STUB_LOG"; exit ${STUB_CADDY_RELOAD_RC:-0}
EOF
cat > "$BIN/caddy" <<'EOF'
#!/usr/bin/env bash
exit ${STUB_CADDY_VALIDATE_RC:-0}
EOF
cat > "$BIN/curl" <<'EOF'
#!/usr/bin/env bash
echo "${STUB_HTTP:-401}"
EOF
# Portable `sed -i` shim. The real script uses GNU syntax (`sed -i "s/…/"`), which is
# correct for the Ubuntu prod host but errors on macOS/BSD sed, where -i needs an
# explicit suffix argument. Shim it so this test asserts the SCRIPT's logic rather
# than the developer machine's sed dialect. Do NOT "fix" the script for BSD — prod is Linux.
cat > "$BIN/sed" <<'EOF'
#!/usr/bin/env bash
if [ "$1" = "-i" ] && [ $# -eq 3 ]; then
  f="$3"; /usr/bin/sed "$2" "$f" > "$f.tmp$$" && mv "$f.tmp$$" "$f"; exit $?
fi
exec /usr/bin/sed "$@"
EOF
chmod +x "$BIN"/*
export PATH="$BIN:$PATH"

run_case() {
  local target_svc="$1"
  STUB_LOG="$WORK/log"; : > "$STUB_LOG"; export STUB_LOG
  UPSTREAM="$WORK/upstream.conf"
  echo "reverse_proxy 127.0.0.1:${2}" > "$UPSTREAM"
  TARGET_SVC="$target_svc" TARGET_PORT="$3" OLD_SVC="$4" OLD_CT="c-$4" \
  HEALTH_WAIT=1 UPSTREAM="$UPSTREAM" \
  bash -c '
    say() { echo "[t] $*"; }
    DC=(compose_stub)
    compose_stub() { echo "compose $*" >> "$STUB_LOG"; }
    # the array call in the block is "${DC[@]}" ... -> route it to a logging shim
    docker_compose_shim() { :; }
    TARGET_SVC="'"$target_svc"'"; TARGET_PORT="'"$3"'"; OLD_SVC="'"$4"'"; OLD_CT="c-'"$4"'"
    HEALTH_WAIT=1; UPSTREAM="'"$UPSTREAM"'"
    DC=(compose_log)
    compose_log() { echo "compose $*" >> "'"$WORK/log"'"; }
    source "'"$WORK/block.sh"'"
  ' 2>&1
}

PASS=0; FAIL=0
check() { if [ "$2" = "$3" ]; then echo "  ok   $1"; PASS=$((PASS+1)); else echo "  FAIL $1 (got '$3', want '$2')"; FAIL=$((FAIL+1)); fi; }

echo "CASE A — deployed into green (:8001). Must return traffic to :8000 and stop green."
OUT=$(run_case backend_green 8001 8001 backend)
check "traffic returned to :8000" "reverse_proxy 127.0.0.1:8000" "$(cat "$WORK/upstream.conf")"
check "green was stopped"        "yes" "$(grep -q 'compose stop backend_green' "$WORK/log" && echo yes || echo no)"
check "backend was recreated"    "yes" "$(grep -q 'compose up -d --force-recreate backend' "$WORK/log" && echo yes || echo no)"
check "reports cron is safe"     "yes" "$(echo "$OUT" | grep -q 'cron is safe' && echo yes || echo no)"

echo
echo "CASE B — deployed into backend, coming from green. Must just stop green."
OUT=$(run_case backend 8000 8000 backend_green)
check "green was stopped"        "yes" "$(grep -q 'compose stop backend_green' "$WORK/log" && echo yes || echo no)"
check "did NOT recreate backend" "no"  "$(grep -q 'compose up -d --force-recreate backend' "$WORK/log" && echo yes || echo no)"
check "reports cron is safe"     "yes" "$(echo "$OUT" | grep -q 'cron is safe' && echo yes || echo no)"

echo
echo "CASE C — backend not running at the end. Final guard must fail loudly."
export STUB_BACKEND_RUNNING=0
OUT=$(run_case backend 8000 8000 backend_green); RC=$?
check "warns cron jobs will fail" "yes" "$(echo "$OUT" | grep -q 'will fail silently' && echo yes || echo no)"
unset STUB_BACKEND_RUNNING

echo
echo "CASE D — backend never goes healthy. Must NOT stop green (site must stay up)."
export STUB_HEALTH=starting
OUT=$(run_case backend_green 8001 8001 backend)
check "green left running"        "no"  "$(grep -q 'compose stop backend_green' "$WORK/log" && echo yes || echo no)"
check "warns cron will fail"      "yes" "$(echo "$OUT" | grep -q 'CRON JOBS WILL FAIL' && echo yes || echo no)"
unset STUB_HEALTH

echo
echo "==== $PASS passed, $FAIL failed ===="
[ "$FAIL" -eq 0 ]
