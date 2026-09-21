#!/usr/bin/env bash
# Record a release in the CFO Build Log (/cfo/build-log).
#
# `devlog_deploy` is the ONLY writer of `live_at` — a skill may claim it built
# something, but only a release proves it reached prod. It shipped on 9-Sep-2026
# with a docstring claiming the deploy called it; nothing did, so "Finished
# today" could never fill. #780 wired it into infra/host/deploy-zero-downtime.sh.
#
# THAT FIXED ONLY HALF OF IT. The Windows deploy path (the CFO's own machine,
# the master seat) does not run deploy-zero-downtime.sh at all — it does its own
# git reset + docker compose build + up over SSM. So the release recording lived
# inside a script most of his deploys never touch, and the band still sat empty.
# This file is that logic pulled out so EVERY deploy path can call the same one
# implementation rather than each growing its own copy.
#
# The previous sha cannot be read from git here: every caller has already reset
# the repo to the new sha by the time this runs, so the old one is gone. It is
# kept in a marker written at the end of the last successful deploy instead. A
# first run simply records the release with no commit range rather than guessing.
#
# THIS NEVER FAILS A DEPLOY. Traffic is already served by the time it runs;
# losing a bookkeeping row must not make a caller believe prod is broken and
# start rolling back. Every path here exits 0.
#
# Environment (all optional):
#   COMPOSE_DIR    repo checkout on the box        (default /opt/alpha-finance)
#   DEVLOG_MARKER  where the last deployed sha is  (default /var/lib/alpha-finance/last-deployed-sha)
#   RR_DC          how to invoke docker compose here — blue/green passes its
#                  extra -f files (default: docker compose --env-file /etc/alpha-finance/.env)
set -u

COMPOSE_DIR=${COMPOSE_DIR:-/opt/alpha-finance}
DEVLOG_MARKER=${DEVLOG_MARKER:-/var/lib/alpha-finance/last-deployed-sha}
read -r -a DC <<< "${RR_DC:-docker compose --env-file /etc/alpha-finance/.env}"

say() { echo "[$(date -u +%H:%M:%S)] build log: $*"; }

new=$(git -C "$COMPOSE_DIR" rev-parse HEAD 2>/dev/null) || new=''
if [ -z "$new" ]; then
  say "no sha to record — skipped"
  exit 0
fi

prev=$(cat "$DEVLOG_MARKER" 2>/dev/null || true)
log=''
if [ -n "$prev" ] && git -C "$COMPOSE_DIR" cat-file -e "${prev}^{commit}" 2>/dev/null; then
  # -n 500 caps the argv. A single argument is limited to 128KB on Linux, and if
  # recording fails repeatedly the unreported range only grows — without a cap it
  # would eventually exceed that and never be able to succeed again, wedging the
  # marker silently and for ever. Beyond 500 commits the oldest Dev-Item trailers
  # go unlinked, which is a far smaller loss than a log that can never recover.
  # %b (the body) is included because a `Dev-Item:` trailer lives at the END of a
  # commit message, never in the subject — and a squash merge puts the whole PR
  # body there. Sending only %s meant the trailer could never be found and NOTHING
  # could ever flip to live, however well the recording itself worked.
  # The body contains newlines, so records are separated by \x1e (RS), not \n.
  log=$(git -C "$COMPOSE_DIR" log -n 500 --format=$'%H\x1f%an\x1f%ct\x1f%s\x1f%b\x1e' "$prev..$new" 2>/dev/null || true)
else
  prev=''
fi

# The container can be RUNNING while its entrypoint is still migrating: `up -d`
# returns as soon as it starts, so on the SSM path this `exec` can land mid-migrate
# and fail. The failure is deliberately swallowed, so without a retry a release
# carrying a devlog migration would quietly never be recorded at all.
ok=''
for attempt in 1 2 3 4; do
  # The log goes over STDIN, not argv: a single argument is capped at 128KB on
  # Linux, and now that commit BODIES are included a few dozen squash merges blow
  # past that. That failure is deterministic, so the retry below could never
  # recover from it and the marker would never advance again — the permanent
  # silent wedge. `exec -T` keeps stdin attached.
  if (cd "$COMPOSE_DIR" && printf '%s' "$log" | "${DC[@]}" exec -T backend \
        python manage.py devlog_deploy --sha "$new" --prev "$prev" --log-stdin) \
        >/dev/null 2>&1; then
    ok=1
    break
  fi
  if [ "$attempt" -lt 4 ]; then sleep 20; fi
done

if [ -n "$ok" ]; then
  say "release ${new:0:12} recorded"
  mkdir -p "$(dirname "$DEVLOG_MARKER")" 2>/dev/null || true
  printf '%s\n' "$new" > "$DEVLOG_MARKER" 2>/dev/null \
    || say "could not update $DEVLOG_MARKER — the next deploy will re-report this range"
else
  say "could not record release ${new:0:12} (the deploy itself is fine)"
fi

exit 0
