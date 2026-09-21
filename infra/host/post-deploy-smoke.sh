#!/usr/bin/env bash
# post-deploy-smoke.sh — after a deploy, open every page the menu offers and
# refuse to call the deploy finished if any of them is not actually reachable.
#
# WHY THIS EXISTS
# ---------------
# On 2026-09-10 the Market Benchmark report shipped to production with its menu
# entry live and its data route missing: the frontend calls /api/v1/reports/...
# but the route was registered only under /api/reports/... Every user who
# clicked Reports -> Market Benchmark got a red "Could not load the benchmark.
# HTTP 404" box. The identical omission shipped in August 2026 for the cash-flow
# report. Both times: CI green, type-check green, review passed, deploy "successful".
#
# Nothing in the pipeline opened a page. That is the whole gap this closes.
#
# WHAT IT CHECKS
# --------------
# Each route is requested with the read-only QA identity. The ONLY question is
# "did this route resolve to a view", because that is the failure this exists to
# catch. So:
#   200          reachable
#   400          reachable — the view ran and rejected the request. Most reports
#                need from/to dates, and this check deliberately sends none; a
#                first draft that treated 400 as failure would have failed every
#                single deploy.
#   401 / 403    reachable — the permission gate did its job, which is exactly
#                what a read-only identity should meet on a finance-gated report.
#   404          FAILURE — no route. This is the bug: the page is in the menu and
#                its data address does not exist.
#   5xx / none   FAILURE — reachable but broken.
#
# It is deliberately a dumb reachability check, not a content check: content is
# the QC harness's job (skills/prat-skill/e2e/qc.mjs) and needs a browser. This
# runs on the box in seconds, with curl, right after the traffic flip.
#
# EXIT CODES
#   0  every route reachable
#   1  at least one route 404/5xx — the deploy is NOT clean, say so loudly
#   2  could not run the check at all (no token, host unreachable)
set -uo pipefail

# Resolve the LIVE slot from the file Caddy actually reads, rather than assuming
# 8000. The blue/green deploy ends on either slot depending on the path it took,
# and smoke-testing the idle one would pass while users hit a broken slot.
UPSTREAM=${UPSTREAM:-/etc/caddy/omni-backend-upstream.conf}
if [ -z "${SMOKE_BASE:-}" ] && [ -r "$UPSTREAM" ]; then
  port=$(grep -oE 'reverse_proxy 127\.0\.0\.1:(8000|8001)' "$UPSTREAM" \
         | grep -oE '(8000|8001)' | head -1)
fi
BASE=${SMOKE_BASE:-http://127.0.0.1:${port:-8000}}
TOKEN_FILE=${SMOKE_TOKEN_FILE:-/etc/alpha-finance/qa-token}
say() { echo "[$(date -u +%H:%M:%S)] smoke: $*"; }

TOKEN=${SMOKE_TOKEN:-}
if [ -z "$TOKEN" ] && [ -r "$TOKEN_FILE" ]; then TOKEN=$(tr -d '\n' < "$TOKEN_FILE"); fi
if [ -z "$TOKEN" ]; then
  say "NO TOKEN ($TOKEN_FILE) — cannot check. Treating as UNKNOWN, not as a pass."
  exit 2
fi

# Every data route behind a menu entry. Add a line when you add a report — the
# CI test reporting/test_report_routes_mounted.py already fails if a report is
# registered in only one of the two URL trees, and this is the runtime half.
ROUTES=(
  /api/v1/reports/trial-balance/
  /api/v1/reports/profit-loss/
  /api/v1/reports/ma-profit-loss/
  /api/v1/reports/balance-sheet/
  /api/v1/reports/cash-flow/
  /api/v1/reports/ar-aging/
  /api/v1/reports/ap-aging/
  /api/v1/reports/general-ledger/
  /api/v1/reports/cash-position/
  /api/v1/reports/peer-benchmark/
)

fails=0; checked=0
for r in "${ROUTES[@]}"; do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 \
           -H "Authorization: Token $TOKEN" "$BASE$r" 2>/dev/null || echo 000)
  checked=$((checked + 1))
  case "$code" in
    200|400|401|403) ;;                               # route resolved; a refusal or a bad-request is fine here
    404)  say "FAIL $r -> 404  (route not mounted — the frontend page will show an error)"; fails=$((fails+1)) ;;
    5*)   say "FAIL $r -> $code (server error)";       fails=$((fails+1)) ;;
    000)  say "FAIL $r -> no response";                fails=$((fails+1)) ;;
    *)    say "FAIL $r -> $code (unexpected)";         fails=$((fails+1)) ;;
  esac
done

if [ "$fails" -gt 0 ]; then
  say "$fails of $checked routes are NOT reachable. The deploy is not clean."
  say "A 404 here means a page in the menu is live and broken for every user."
  exit 1
fi
say "all $checked menu routes reachable"
exit 0
