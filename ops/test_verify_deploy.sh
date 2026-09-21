#!/bin/bash
# Does the deploy verifier actually see a broken stack?
#
# Written 11-Aug-2026 after a deploy left the backend Created-but-never-started.
# Every API call 502'd for minutes, and two separate checks looked straight past it:
#
#   * the sick-container filter matched (unhealthy)|Restarting|Exited, and `Created`
#     is none of those;
#   * the only public probe was the site root, which is served by the FRONTEND
#     container and answered a clean 200 the whole time.
#
# Shell is awkward to unit-test, so this exercises the two decisions that were wrong
# against fixed sample input — the same greps verify_deploy.sh runs.
#
#   bash ops/test_verify_deploy.sh        # exit 0 = all pass
set -uo pipefail

PASS=0
FAIL=0

ok()   { PASS=$((PASS+1)); echo "  ok   — $1"; }
bad()  { FAIL=$((FAIL+1)); echo "  FAIL — $1"; }

# The decision as verify_deploy.sh makes it: the services that MUST be running are
# named, and each one has to be Up. Keep this in step with REQUIRED_SERVICES there.
sick_required() {
    local ps_all="$1" n=0 st
    for svc in backend frontend db; do
        st=$(printf '%s\n' "$ps_all" | awk -v s="$svc" '$1 == s { $1=""; sub(/^ /,""); print }')
        case "$st" in
            Up*\(unhealthy\)*) n=$((n+1)) ;;
            Up*)                : ;;
            '')                 n=$((n+1)) ;;
            *)                  n=$((n+1)) ;;
        esac
    done
    echo "$n"
}

echo "required-service check:"

# The 11-Aug-2026 incident: the backend Created and nothing else wrong. The old
# filter counted zero here, which is how it shipped.
n=$(sick_required 'backend Created
frontend Up 2 minutes (healthy)
db Up 13 days (healthy)')
[ "$n" = "1" ] \
  && ok "a Created backend fails the release, on its own" \
  || bad "a Created backend was NOT caught ($n) — this is the 11-Aug incident"

# The false alarm the first version of this fix introduced: the retired blue/green
# slot is stopped BY DESIGN and must never fail a deploy.
n=$(sick_required 'backend Up 8 hours (healthy)
backend_green Exited (0) 13 hours ago
db Up 2 weeks (healthy)
frontend Up 9 hours (healthy)
telegrambot Up 4 seconds')
[ "$n" = "0" ] \
  && ok "the retired backend_green slot does NOT fail a healthy release" \
  || bad "backend_green counted as a fault ($n) — that would fail every deploy for ever"

n=$(sick_required 'backend Up 5 minutes (healthy)
frontend Up 5 minutes (healthy)
db Up 13 days (healthy)')
[ "$n" = "0" ] && ok "a healthy stack counts zero" || bad "healthy stack reported sick ($n)"

n=$(sick_required 'backend Up 2 minutes (unhealthy)
frontend Up 5 minutes (healthy)
db Up 13 days (healthy)')
[ "$n" = "1" ] && ok "(unhealthy) backend fails" || bad "(unhealthy) was missed ($n)"

n=$(sick_required 'backend Restarting (1) 3 seconds ago
frontend Up 5 minutes (healthy)
db Up 13 days (healthy)')
[ "$n" = "1" ] && ok "a Restarting backend fails" || bad "Restarting was missed ($n)"

n=$(sick_required 'frontend Up 5 minutes (healthy)
db Up 13 days (healthy)')
[ "$n" = "1" ] \
  && ok "a backend missing from compose ps entirely fails" \
  || bad "a missing backend was not caught ($n)"

n=$(sick_required 'backend Up 8 hours (healthy)
frontend Up 9 hours (healthy)
db Up 2 weeks (healthy)
telegrambot Exited (1) 2 minutes ago')
[ "$n" = "0" ] \
  && ok "the telegram bot alone does not block a release (reported, not fatal)" \
  || bad "the bot blocked a release it should only warn about ($n)"

echo "orphan sweep pattern:"

# The rename compose leaves behind when a recreate is interrupted.
orphans() { grep -E '^[0-9a-f]{12}_alpha-finance-' || true; }

SAMPLE='892cfed30e94_alpha-finance-telegrambot
fc6a163a2adc_alpha-finance-frontend
alpha-finance-backend
alpha-finance-frontend
alpha-finance-db
alpha-finance-backend-green'
got=$(printf '%s\n' "$SAMPLE" | orphans | wc -l | tr -d ' ')
[ "$got" = "2" ] \
  && ok "both hash-prefixed orphans are found" \
  || bad "expected 2 orphans, found $got"

kept=$(printf '%s\n' "$SAMPLE" | orphans | grep -c 'alpha-finance-backend$' || true)
[ "$kept" = "0" ] \
  && ok "the real containers are NOT swept" \
  || bad "the sweep would have removed a live container — far worse than the bug"

kept_green=$(printf '%s\n' "$SAMPLE" | orphans | grep -c 'backend-green' || true)
[ "$kept_green" = "0" ] \
  && ok "the blue/green slot is left alone" \
  || bad "the sweep would have removed the zero-downtime slot"

echo "API verdict (the front page is NOT the health check):"

# The same case pattern verify_deploy.sh uses. 200/401/403 all prove the backend is
# alive and answering; anything else is Caddy failing to reach it.
api_alive() { case "$1" in 200|401|403) return 0 ;; *) return 1 ;; esac; }

api_alive 200 && ok "200 reads as alive" || bad "200 should read as alive"
api_alive 401 && ok "401 reads as alive — no token, but the backend answered" \
               || bad "401 should read as alive; requiring 200 would need a credential"
api_alive 403 && ok "403 reads as alive" || bad "403 should read as alive"
api_alive 502 && bad "502 must NOT read as alive — this is the 11-Aug trap" \
               || ok "502 reads as BROKEN even though the front page serves 200"
api_alive 504 && bad "504 must NOT read as alive" || ok "504 reads as broken"
api_alive 000 && bad "000 (no answer) must NOT read as alive" || ok "000 reads as broken"

echo
echo "passed $PASS, failed $FAIL"
[ "$FAIL" = "0" ] || exit 1
