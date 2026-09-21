#!/bin/bash
# Verify an Omni deploy actually worked. Reusable — source it or run it.
#
# Written after the 6 Aug 18:00 release emailed "Go-live FAILED" for a release
# that had in fact succeeded. The old check slept 30 seconds and then demanded the
# backend answer on 127.0.0.1:8000. The backend runs migrations on boot, so 30s
# after a recreate it was still starting and had not bound the port yet — the
# probe read that as a failed release. Public was already 200 and migrations were
# 0, i.e. every real signal said fine.
#
# A false alarm is not a harmless bug. The CFO and EXCO were told a release broke
# when it had not, and the next real failure is that much easier to wave away.
#
# Two changes: WAIT for readiness rather than guessing a duration, and judge on
# the signals that actually mean "the release worked".
#
# Exit 0 = healthy, 1 = genuinely broken.
set -uo pipefail

COMPOSE="docker compose --env-file /etc/alpha-finance/.env"
DEADLINE=$((SECONDS + 180))     # generous: migrations on a cold boot are not fast

echo "waiting for the backend to come up (up to 3 minutes)..."
BACKEND=000
while [ $SECONDS -lt $DEADLINE ]; do
    BACKEND=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 http://127.0.0.1:8000/ 2>/dev/null || echo 000)
    [ "$BACKEND" = "200" ] && break
    sleep 5
done

# The verdict-carrying signal gets retries too. Giving the backend 36 attempts and
# the deciding probe exactly one is how a single curl blip becomes a FAILED email
# to EXCO.
PUBLIC=000
for _ in 1 2 3; do
    PUBLIC=$(curl -s -o /dev/null -w '%{http_code}' --max-time 25 https://omni.alphadirect.co.bw/ 2>/dev/null || echo 000)
    [ "$PUBLIC" = "200" ] && break
    sleep 4
done
# grep -c EXITS 1 when the count is zero. A trailing `|| echo '?'` therefore fires
# on the healthy path and appends a second line, so "0" becomes "0\n?" and the
# equality test below fails on a perfectly good deploy. That is the same shape of
# bug as the one this script exists to fix, so: capture the plan first, prove we
# actually got one, and only then count.
# The public root is served by the FRONTEND container. On 11-Aug-2026 it returned a
# clean 200 while the backend was down and every data call 502'd — "the site is up"
# is not evidence the system works. So probe an API route through Caddy too. No token
# is needed: 401/403 proves the backend is ALIVE and answering, 502/504 proves it is
# not, and that is the whole question.
API=000
for _ in 1 2 3; do
    API=$(curl -s -o /dev/null -w '%{http_code}' --max-time 25 \
          https://omni.alphadirect.co.bw/api/v1/payment-requests/ 2>/dev/null || echo 000)
    case "$API" in 200|401|403) break ;; esac
    sleep 4
done

PLAN=$($COMPOSE exec -T backend python manage.py showmigrations --plan 2>/dev/null)
if [ -z "$PLAN" ]; then
    MIG='?'          # could not ask the app — that IS a problem
else
    MIG=$(printf '%s\n' "$PLAN" | grep -c '^\[ \]' || true)
fi
# Count sick containers POSITIVELY. Excluding lines that match 'healthy' also
# excludes '(unhealthy)' — the word contains it — so the obvious-looking negative
# filter could never see the very thing it was named after.
# Judge the services that MUST be running, by name — do not count "anything that
# looks sick".
#
# Two ways to get this wrong, and the first draft of this fix hit the second:
#   * the original filter matched (unhealthy)|Restarting|Exited on `ps` (running
#     containers only), so a backend stuck in `Created` was invisible — that is the
#     11-Aug-2026 incident, where every API call 502'd and this said zero;
#   * switching to `ps -a` and adding `Created` then counted `backend_green`, the
#     retired blue/green slot, which deploy-zero-downtime.sh STOPS on purpose. That
#     would have failed every future deploy. A false alarm is not a harmless bug —
#     it is how the next real failure gets waved through.
#
# So: an explicit list, and each one has to be Up. Anything not Up — Created,
# Exited, Restarting, unhealthy — fails, and the retired slot is not on the list.
REQUIRED_SERVICES="backend frontend db"
PS_ALL=$($COMPOSE ps -a --format '{{.Service}} {{.Status}}' 2>/dev/null)
UNHEALTHY=0
for svc in $REQUIRED_SERVICES; do
    st=$(printf '%s\n' "$PS_ALL" | awk -v s="$svc" '$1 == s { $1=""; sub(/^ /,""); print }')
    case "$st" in
        Up*\(unhealthy\)*) echo "  $svc: $st — UNHEALTHY"; UNHEALTHY=$((UNHEALTHY+1)) ;;
        Up*)                : ;;
        '')                 echo "  $svc: MISSING from compose ps"; UNHEALTHY=$((UNHEALTHY+1)) ;;
        *)                  echo "  $svc: $st — NOT RUNNING"; UNHEALTHY=$((UNHEALTHY+1)) ;;
    esac
done
# The bot is reported but never fails a release: it is not on the request path.
BOT=$(printf '%s\n' "$PS_ALL" | awk '$1 == "telegrambot" { $1=""; sub(/^ /,""); print }')
case "$BOT" in Up*|'') : ;; *) echo "  note: telegrambot is $BOT (not release-blocking)" ;; esac

echo "backend $BACKEND | public $PUBLIC | api $API | unapplied migrations $MIG | unhealthy containers $UNHEALTHY"

# Could not interrogate the app at all. That is the verifier being blind, which is
# NOT the same statement as "the release broke" — reporting it as a failure is the
# false-RED that started all this. Exit 2 = UNKNOWN, matching the /fabe post-deploy
# probe convention. It still alerts; it just tells the truth about what it knows.
if [ "$MIG" = "?" ]; then
    echo "RESULT: UNKNOWN (could not query the app — verifier is blind, not necessarily the release)"
    exit 2
fi

# The release is good when the site serves, the database is fully migrated, and
# nothing is sick. The backend port is a readiness detail, not the verdict —
# treating it as the verdict is what produced the false alarm.
case "$API" in
    200|401|403) : ;;                    # backend is answering — that is all we ask
    *)  echo "RESULT: FAILED (the API answered $API through Caddy — the front page"
        echo "         may serve fine while every data call is broken)"
        exit 1 ;;
esac

if [ "$PUBLIC" = "200" ] && [ "$MIG" = "0" ] && [ "$UNHEALTHY" = "0" ]; then
    if [ "$BACKEND" = "200" ]; then
        echo "RESULT: OK"
        exit 0
    fi
    # Only a backend that never ANSWERED may be called "slow". One that answers
    # 500 on every probe for three minutes is broken, and calling that OK because
    # the static frontend still serves would be a false green — far worse than the
    # false red this script was written to remove.
    if [ "$BACKEND" = "000" ]; then
        echo "RESULT: OK_SLOW_BACKEND (frontend serving; backend still booting)"
        exit 0
    fi
    echo "RESULT: FAILED (backend answering $BACKEND — the site serves but the API is broken)"
    exit 1
fi
echo "RESULT: FAILED"
exit 1
