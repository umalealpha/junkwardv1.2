#!/usr/bin/env bash
# Make Caddy ride out a backend/frontend restart instead of returning 502.
#
# WHY (bug f33a411f, Kakale Botana, 2026-07-30): omni is deployed many times a
# day while people are working — 30 `git pull` + container recreates in office
# hours on 07-30 alone. Each recreate takes the backend ~60s to come back
# (entrypoint runs migrations + the CoA seed before gunicorn even boots), and
# Caddy proxied straight to 127.0.0.1:8000 with no retry, so every click in that
# window was a hard 502. Measured over three days of Caddy logs: 3,028 502s
# across 89 separate minutes, hitting 35 different office IPs — i.e. most of the
# company, repeatedly. It was misdiagnosed for weeks as "gunicorn worker
# cycling"; it was never that.
#
# lb_try_duration makes Caddy hold the request and keep retrying the upstream for
# up to 75s (comfortably over the measured 62s boot) instead of failing. Proven
# on prod during a real restart: a GET that would have 502'd returned 200 after
# 35.9s.
#
# NOT set: request_buffers / lb_retry_match. Caddy will not replay a POST without
# them, and that is the behaviour we want — silently re-sending a POST could post
# a payment or a journal entry twice. Submits are handled in the app instead:
# frontend/src/lib/api.ts turns a 502/503/504 into a plain-English "Omni was
# updating, press the button again" message and the form keeps what was typed.
#
# Idempotent — safe to re-run. Kept in the repo so the fix survives an instance
# rebuild (the Caddyfile itself lives on the host, not in this repo).
set -uo pipefail
CADDYFILE=${CADDYFILE:-/etc/caddy/Caddyfile}

if [ ! -f "$CADDYFILE" ]; then echo "no $CADDYFILE — nothing to do"; exit 0; fi
if grep -q 'lb_try_duration' "$CADDYFILE"; then echo "already applied"; exit 0; fi

# Fail loudly if python3 is missing. Without this the heredoc below would fail,
# `caddy validate` would still pass (on the UNPATCHED file), and the script would
# reload caddy and report success while having changed nothing (DeepSeek review,
# 2026-07-30). A patch script that silently no-ops is worse than one that errors.
command -v python3 >/dev/null 2>&1 || {
  echo "ERROR: python3 not found — cannot patch $CADDYFILE. Nothing changed." >&2; exit 1; }

# Name the backup ONCE and restore that exact path. A glob (`.bak-lbretry-*`) breaks
# on the second failed run: two backups exist, `cp` gets two sources and a non-directory
# target, errors, and leaves a PATCHED-BUT-INVALID Caddyfile in place — which works until
# the next caddy restart, then caddy will not start (Fable review 2026-07-30).
BAK="$CADDYFILE.bak-lbretry-$(date -u +%Y%m%d-%H%M%S)"
cp -a "$CADDYFILE" "$BAK" || {
  echo "ERROR: could not write the backup $BAK — refusing to patch." >&2; exit 1; }

python3 - "$CADDYFILE" <<'PY' || { echo "ERROR: patch step failed, Caddyfile unchanged" >&2; exit 1; }
import re, sys
path = sys.argv[1]
src = open(path).read()
block = (
    '{i}reverse_proxy {addr} {{\n'
    '{i}    # A deploy recreates the container (~60s); without this every request\n'
    '{i}    # in that window is a hard 502 to whoever is clicking (bug f33a411f).\n'
    '{i}    lb_try_duration 75s\n'
    '{i}    lb_try_interval 300ms\n'
    '{i}}}'
)
new, n = re.subn(
    r'^([ \t]*)reverse_proxy (127\.0\.0\.1:(?:8000|3000))[ \t]*$',
    lambda m: block.format(i=m.group(1), addr=m.group(2)),
    src, flags=re.M)
if n == 0:
    sys.exit('no bare reverse_proxy lines found — check the Caddyfile by hand')
open(path, 'w').write(new)
print(f'patched {n} reverse_proxy block(s)')
PY

caddy validate --config "$CADDYFILE" >/dev/null 2>&1 || {
  echo "VALIDATION FAILED — restoring $BAK" >&2
  cp -a "$BAK" "$CADDYFILE" || echo "ERROR: RESTORE FAILED — $CADDYFILE is invalid, fix by hand" >&2
  exit 1; }

# Check the reload. `systemctl is-active` stays "active" on the OLD config even when a
# reload was rejected, so reporting it alone can claim success on a reload that never
# took effect (Fable review 2026-07-30).
systemctl reload caddy || {
  echo "ERROR: caddy reload FAILED — config is patched but NOT live. Restoring $BAK." >&2
  cp -a "$BAK" "$CADDYFILE" || echo "ERROR: RESTORE FAILED — $CADDYFILE is invalid, fix by hand" >&2
  # Say it out loud if the recovery reload ALSO fails — this is the state an operator
  # most needs to hear about, so it must never be swallowed (DeepSeek H6, 2026-07-30).
  systemctl reload caddy || echo "ERROR: reload of the RESTORED config also failed — caddy may be serving stale config; check 'systemctl status caddy'" >&2
  exit 1; }
echo "caddy reloaded, active=$(systemctl is-active caddy)"
