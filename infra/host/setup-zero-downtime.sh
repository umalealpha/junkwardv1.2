#!/usr/bin/env bash
# ONE-TIME setup for zero-downtime backend deploys. Idempotent — safe to re-run.
#
# Moves the two hardcoded backend upstreams in the host Caddyfile (the omni site and
# the nexus site) into a single shared include, so deploy-zero-downtime.sh can move
# traffic between the two backend slots by rewriting one line and reloading.
#
# A Caddy reload is graceful — in-flight requests finish, new ones go to the new
# target — so running this on a live system does not interrupt anybody. Proven on
# prod 2026-07-31: 70 requests across two live cutovers, zero failures.
#
# Kept in the repo because /etc/caddy lives on the host and would otherwise be lost
# on an instance rebuild (the lesson from the cron files that vanished).
set -uo pipefail
CADDYFILE=${CADDYFILE:-/etc/caddy/Caddyfile}
UPSTREAM=${UPSTREAM:-/etc/caddy/omni-backend-upstream.conf}

[ -f "$CADDYFILE" ] || { echo "no $CADDYFILE — nothing to do"; exit 0; }
command -v python3 >/dev/null 2>&1 || { echo "ERROR: python3 required" >&2; exit 1; }

# 1. The include, defaulting to the blue slot.
if [ ! -f "$UPSTREAM" ]; then
  cat > "$UPSTREAM" <<'CONF'
# ACTIVE BACKEND SLOT — written by deploy-zero-downtime.sh. Do not hand-edit.
# 8000 = blue, 8001 = green.
reverse_proxy 127.0.0.1:8000 {
	# From the 502 fix (bug f33a411f): ride out a restart rather than erroring at
	# whoever is clicking. Kept as belt-and-braces even with blue/green.
	lb_try_duration 75s
	lb_try_interval 300ms
}
CONF
  chmod 644 "$UPSTREAM"
  echo "created $UPSTREAM"
else
  echo "$UPSTREAM already present — left as is (it holds the live slot)"
fi

# 2. Point both site blocks at the include.
if grep -q 'import .*omni-backend-upstream.conf' "$CADDYFILE"; then
  echo "Caddyfile already uses the include — nothing to change"; exit 0
fi
BAK="$CADDYFILE.bak-preimport-$(date -u +%Y%m%d-%H%M%S)"
cp -a "$CADDYFILE" "$BAK" || { echo "ERROR: could not back up the Caddyfile" >&2; exit 1; }

python3 - "$CADDYFILE" <<'PY' || { echo "ERROR: rewrite failed, Caddyfile unchanged" >&2; exit 1; }
import re, sys
p = sys.argv[1]; src = open(p).read()
pat = re.compile(r"[ \t]*reverse_proxy 127\.0\.0\.1:8000 \{.*?\n[ \t]*\}\n", re.S)
new, n = pat.subn("        import /etc/caddy/omni-backend-upstream.conf\n", src)
if n != 2:
    sys.exit(f"expected 2 backend upstream blocks, found {n} — refusing to guess")
open(p, "w").write(new)
print(f"replaced {n} backend upstream block(s) with the include")
PY

if ! caddy validate --config "$CADDYFILE" >/dev/null 2>&1; then
  echo "VALIDATION FAILED — restoring $BAK" >&2
  cp -a "$BAK" "$CADDYFILE" || echo "ERROR: RESTORE FAILED, fix by hand" >&2
  exit 1
fi
systemctl reload caddy || {
  echo "ERROR: reload failed — restoring $BAK" >&2
  cp -a "$BAK" "$CADDYFILE"
  systemctl reload caddy || echo "ERROR: restore reload ALSO failed — check systemctl status caddy" >&2
  exit 1; }
echo "done — caddy reloaded, active=$(systemctl is-active caddy)"
