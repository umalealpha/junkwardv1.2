#!/usr/bin/env bash
# check-release-drift.sh — say out loud when production is not running main.
#
# WHY
# ---
# On 2026-09-10 a fix was merged, CI went green, and production kept serving the
# previous build. A page in the menu stayed broken for hours and nothing
# anywhere said so: a deploy that silently never happened looks exactly like a
# deploy that did. Deploys here are manual and CFO-authorised, which is a
# deliberate choice — so the answer is not to automate the deploy, it is to make
# the gap VISIBLE and named.
#
# WHAT IT DOES
# Reads the commit production is actually serving (health endpoint, baked in at
# image build), compares it to origin/main, and reports the unshipped commits.
# Read-only. No keys beyond the Telegram one already on the box.
#
# EXIT CODES
#   0  production is current (or behind by less than GRACE_MINUTES)
#   1  production is behind and past the grace period — alert sent if configured
#   2  cannot tell (health endpoint unreachable, or it reports 'unknown')
#      — never reported as current, because not knowing is not the same as fine.
set -uo pipefail

# Target the BACKEND health endpoint on the live slot, NOT the public root —
# https://omni.alphadirect.co.bw/ serves the Next.js frontend and returns HTML,
# which this script would read as "no commit reported" and call UNKNOWN forever.
# This runs on the box (cron), so the loopback slot is both correct and cheap.
UPSTREAM=${UPSTREAM:-/etc/caddy/omni-backend-upstream.conf}
if [ -z "${DRIFT_BASE:-}" ] && [ -r "$UPSTREAM" ]; then
  _port=$(grep -oE 'reverse_proxy 127\.0\.0\.1:(8000|8001)' "$UPSTREAM" \
          | grep -oE '(8000|8001)' | head -1)
fi
BASE=${DRIFT_BASE:-http://127.0.0.1:${_port:-8000}}
REPO=${DRIFT_REPO:-/opt/alpha-finance}
GRACE_MINUTES=${GRACE_MINUTES:-120}
say() { echo "[$(date -u +%H:%M:%S)] drift: $*"; }

live=$(curl -s --max-time 20 "$BASE/" 2>/dev/null \
        | python3 -c 'import json,sys;print(json.load(sys.stdin).get("commit","unknown"))' 2>/dev/null || echo unknown)

if [ -z "$live" ] || [ "$live" = "unknown" ]; then
  say "production does not report its build (got '${live:-nothing}')."
  say "Cannot tell whether it is current. This is UNKNOWN, not OK."
  exit 2
fi

# Fetch as the repo's owner. On the prod box the deploy key belongs to `ubuntu`
# and root cannot read it — `git fetch` as root fails with "Permission denied
# (publickey)" and, because git still exits 0 there, the first version of this
# reported "cannot fetch origin" and gave up. This is the same ownership trap
# that makes the deploy pull with `sudo -u ubuntu`.
GIT_USER=${GIT_USER:-ubuntu}
if [ "$(id -un)" = "root" ] && id -u "$GIT_USER" >/dev/null 2>&1; then
  fetch_out=$(sudo -u "$GIT_USER" git -C "$REPO" fetch origin -q 2>&1)
else
  fetch_out=$(git -C "$REPO" fetch origin -q 2>&1)
fi
if printf '%s' "$fetch_out" | grep -qiE 'permission denied|could not read from remote|fatal'; then
  say "cannot fetch origin: $(printf '%s' "$fetch_out" | head -1)"
  exit 2
fi
head=$(git -C "$REPO" rev-parse origin/main 2>/dev/null) || { say "cannot read origin/main"; exit 2; }

if [ "$live" = "$head" ]; then
  say "production is current (${live:0:12})"
  exit 0
fi

if ! git -C "$REPO" cat-file -e "${live}^{commit}" 2>/dev/null; then
  say "production reports ${live:0:12}, which this checkout does not have."
  say "Either the box is ahead of this clone, or the build was made elsewhere. UNKNOWN."
  exit 2
fi

behind=$(git -C "$REPO" rev-list --count "$live..$head" 2>/dev/null || echo '?')
[ "$behind" = "0" ] && { say "production is current"; exit 0; }

# Age of the OLDEST unshipped commit — a fix merged two minutes ago is not an
# incident, one merged this morning is.
oldest=$(git -C "$REPO" log --format=%ct --reverse "$live..$head" 2>/dev/null | head -1)
now=$(date -u +%s)
age_min=$(( (now - ${oldest:-$now}) / 60 ))

printf -v list '%s' "$(git -C "$REPO" log --format='  - %s' "$live..$head" 2>/dev/null \
                        | grep -viE 'machine-talk|^  - log:' | head -8)"

if [ "$age_min" -lt "$GRACE_MINUTES" ]; then
  say "production is $behind commit(s) behind, oldest ${age_min}m — inside the ${GRACE_MINUTES}m grace period"
  exit 0
fi

say "production is $behind commit(s) BEHIND main. Oldest unshipped change is ${age_min} minutes old."
[ -n "$list" ] && printf '%s\n' "$list"

MSG="Omni: production is ${behind} commit(s) behind main.
Oldest unshipped change: ${age_min} minutes old.
Live build: ${live:0:12} · main: ${head:0:12}
${list}
Nothing is broken by this on its own — but a merged fix is not live until it is deployed."

if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
  if curl -s --max-time 20 -o /dev/null \
       -d "chat_id=${TELEGRAM_CHAT_ID}" --data-urlencode "text=${MSG}" \
       "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage"; then
    say "alert sent"
  else
    say "alert FAILED to send — the drift above is still real"
  fi
else
  say "no Telegram credentials in the environment — reporting to stdout only"
fi
exit 1
