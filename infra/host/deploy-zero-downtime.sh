#!/usr/bin/env bash
# Zero-downtime backend deploy for omni (CFO decision 2026-07-31).
#
# THE PROBLEM THIS EXISTS FOR: a backend restart takes ~66s, because the entrypoint
# runs migrations and the CoA seed before gunicorn boots. Until now that was 66s in
# which every click got a hard 502 — 2,717 of them across 85 minutes in July, on 34
# different office computers. Rebuilding on top of working staff was the whole cause.
#
# HOW THIS REMOVES IT: two backend slots, 8000 (blue) and 8001 (green). Only one is
# ever live. We boot the IDLE slot with the new image and let it take its full 66s
# while the live slot keeps serving. Only once the new slot is proven healthy do we
# move traffic — by rewriting one Caddy include and reloading. A Caddy reload is
# graceful: in-flight requests finish on the old slot, new ones go to the new one.
# Then the old slot is stopped.
#
# THE SAFETY PROPERTY: every check that can fail happens BEFORE traffic moves. If the
# build fails, or the new slot never goes healthy, or its smoke test fails, we stop the
# new slot and leave the live one untouched. A failed deploy is a no-op, not an outage.
#
# NOT COVERED, deliberately:
#   * Destructive migrations. For the ~66s of overlap both versions talk to the same
#     database. A migration that DROPS or RENAMES something the old version still
#     reads will break the old version for those seconds. Write migrations additively:
#     add in one deploy, remove in a later one.
#   * The frontend. It restarts in ~8s and Caddy's lb_try_duration already rides over
#     that invisibly, so a second frontend slot would be complexity for nothing.
#
# Usage:  sudo bash deploy-zero-downtime.sh [--frontend] [--dry-run]
set -uo pipefail

COMPOSE_DIR=${COMPOSE_DIR:-/opt/alpha-finance}
ENV_FILE=${ENV_FILE:-/etc/alpha-finance/.env}
UPSTREAM=${UPSTREAM:-/etc/caddy/omni-backend-upstream.conf}
LOG=${LOG:-/var/log/alpha-finance/zero-downtime-deploy.log}
HEALTH_WAIT=${HEALTH_WAIT:-180}      # seconds to allow the new slot to become healthy
DO_FRONTEND=0
DRY_RUN=0
for a in "$@"; do
  case "$a" in
    --frontend) DO_FRONTEND=1 ;;
    --dry-run)  DRY_RUN=1 ;;
    *) echo "unknown argument: $a" >&2; exit 2 ;;
  esac
done

say() { echo "[$(date -u +%H:%M:%S)] $*"; }
DC=(docker compose --env-file "$ENV_FILE" -f docker-compose.yml -f docker-compose.bluegreen.yml)

# Single-runner lock (2026-09-07). The SSM wrapper can retry after a client-side
# timeout and launch a SECOND deploy; two blue/green runs then interleave on the
# same 8000/8001 slots and the one Caddy include — one stops green while the other
# still expects it. Take an exclusive, non-blocking lock so a second run aborts
# cleanly instead of racing the first. FD 9 is held for the life of the process.
LOCK_FILE=${LOCK_FILE:-/var/lock/alpha-finance-deploy.lock}
exec 9>"$LOCK_FILE" 2>/dev/null || exec 9>/tmp/alpha-finance-deploy.lock
if ! flock -n 9; then
  say "another deploy is already running (lock $LOCK_FILE held) — aborting this run"
  exit 1
fi

cd "$COMPOSE_DIR" || { echo "cannot cd $COMPOSE_DIR" >&2; exit 1; }
[ -f "$UPSTREAM" ] || { echo "missing $UPSTREAM — run the one-time Caddy include setup first" >&2; exit 1; }

# ---- work out which slot is live, and which one we are deploying into -------------
ACTIVE_PORT=$(grep -oE 'reverse_proxy 127\.0\.0\.1:(8000|8001)' "$UPSTREAM" | grep -oE '(8000|8001)' | head -1)
case "$ACTIVE_PORT" in
  8000) TARGET_PORT=8001; TARGET_SVC=backend_green; TARGET_CT=alpha-finance-backend-green
        OLD_SVC=backend;       OLD_CT=alpha-finance-backend ;;
  8001) TARGET_PORT=8000; TARGET_SVC=backend;       TARGET_CT=alpha-finance-backend
        OLD_SVC=backend_green; OLD_CT=alpha-finance-backend-green ;;
  *) echo "cannot read the active slot from $UPSTREAM (found '${ACTIVE_PORT:-nothing}')" >&2; exit 1 ;;
esac
say "live slot :$ACTIVE_PORT ($OLD_CT) -> deploying into :$TARGET_PORT ($TARGET_CT)"
if [ "$DRY_RUN" = 1 ]; then say "--dry-run: stopping here, nothing changed"; exit 0; fi

# ---- 0a. DISK GUARD + BACKUP RETENTION -------------------------------------------
# 9-Sep-2026: this deploy half-landed. The backend cut over, then the frontend build
# died on "no space left on device" and the old backend slot could not be rebuilt —
# so the site served new server code behind an OLD frontend, and nothing warned us.
# The disk was 98% full. The cause was NOT the images: /var/backups held 47 GB of
# 349 pre-deploy dumps, one per deploy since 1 August, and nothing ever removed them.
#
# Two changes, in this order:
#   1. Refuse to start a deploy without room to finish it. Reclaim first, then abort
#      if still short — an abort before the build costs nothing, a half-deploy costs
#      an evening.
#   2. After the new backup is VERIFIED in S3, delete local dumps older than
#      RETAIN_DAYS — and only ones whose S3 copy matches byte-for-byte. A dump that
#      is not provably off the box is never deleted, whatever its age.
DISK_MIN_GB=${DISK_MIN_GB:-12}          # a no-cache frontend build needs ~8 GB of scratch
RETAIN_DAYS=${RETAIN_DAYS:-7}
BK_DIR=${BK_DIR:-/var/backups/alpha-finance}
BK_BUCKET=${BK_BUCKET:-alphadirect-db-backups-capetown}
BK_PREFIX=${BK_PREFIX:-alpha-finance/pre-deploy}

free_gb() { df -P --block-size=1G / 2>/dev/null | awk 'NR==2{print $4}'; }

# Delete local pre-deploy dumps older than $1 days whose S3 copy is the same size.
# Echoes how many went and how much came back. Never touches anything else in the
# directory (the TEST dump and the vendor-merge records stay).
prune_verified_backups() {
  local days="$1" n=0 bytes=0 kept=0 f base local_size remote_size
  for f in $(find "$BK_DIR" -maxdepth 1 -name 'alpha_finance_predeploy_2*.sql.gz' -mtime +"$days" 2>/dev/null); do
    base=$(basename "$f"); local_size=$(stat -c %s "$f" 2>/dev/null || echo 0)
    remote_size=$(AWS_DEFAULT_REGION=af-south-1 aws s3api head-object \
                    --bucket "$BK_BUCKET" --key "$BK_PREFIX/$base" \
                    --query ContentLength --output text 2>/dev/null || echo MISSING)
    if [ "$remote_size" = "$local_size" ] && [ "$local_size" -gt 0 ]; then
      rm -f "$f" && n=$((n+1)) && bytes=$((bytes+local_size))
    else
      kept=$((kept+1))
    fi
  done
  say "backup retention: removed $n dump(s) older than ${days}d ($(awk -v b=$bytes 'BEGIN{printf "%.1f", b/1073741824}') GB); kept $kept that are not verified in S3"
}

FREE=$(free_gb)
if [ -n "$FREE" ] && [ "$FREE" -lt "$DISK_MIN_GB" ]; then
  say "only ${FREE} GB free and a deploy needs ${DISK_MIN_GB} GB — reclaiming before we touch anything"
  docker builder prune -af >/dev/null 2>&1 || true
  docker image prune -f    >/dev/null 2>&1 || true
  prune_verified_backups "$RETAIN_DAYS"
  FREE=$(free_gb)
  if [ -n "$FREE" ] && [ "$FREE" -lt "$DISK_MIN_GB" ]; then
    say "ABORT: still only ${FREE} GB free (need ${DISK_MIN_GB}). Nothing has changed — the site is untouched."
    say "       look at: du -x -h -d1 /var | sort -hr | head"
    exit 1
  fi
  say "reclaimed — ${FREE} GB free, carrying on"
fi

# ---- 0. BACKUP FIRST, and prove it (CFO instruction 2026-07-31: "backup before
# doing so"). Blocking on purpose: if we cannot take a verified backup, we do not
# deploy. The hourly backup is not enough on its own — it can be up to an hour old,
# and it is never checked. This one is taken now, integrity-tested, size-checked,
# confirmed present in S3, and the local copy is KEPT for the deploy window so a
# restore does not have to wait on a download.
say "taking a verified pre-deploy backup"
BK_TS=$(date -u +%Y-%m-%dT%H-%M-%SZ)
BK_NAME="alpha_finance_predeploy_${BK_TS}.sql.gz"
BK_LOCAL="/var/backups/alpha-finance/${BK_NAME}"
mkdir -p /var/backups/alpha-finance
DB_PW=$(grep -E '^DB_PASSWORD=' "$ENV_FILE" | head -1 | cut -d= -f2-)
DB_USER=$(grep -E '^DB_USER=' "$ENV_FILE" | head -1 | cut -d= -f2-); DB_USER=${DB_USER:-alpha_admin}
DB_NAME=$(grep -E '^DB_NAME=' "$ENV_FILE" | head -1 | cut -d= -f2-); DB_NAME=${DB_NAME:-alpha_finance}
[ -n "$DB_PW" ] || { say "ABORT: no DB_PASSWORD in $ENV_FILE — refusing to deploy without a backup"; exit 1; }

set -o pipefail
if ! docker compose --env-file "$ENV_FILE" exec -T -e PGPASSWORD="$DB_PW" db \
      pg_dump -U "$DB_USER" "$DB_NAME" --no-owner --clean --if-exists | gzip -9 > "$BK_LOCAL"; then
  say "ABORT: pg_dump failed — NOT deploying. Nothing has changed."; rm -f "$BK_LOCAL"; exit 1
fi

# A truncated dump is more dangerous than no dump, because it looks like safety.
if ! gzip -t "$BK_LOCAL" 2>/dev/null; then
  say "ABORT: the backup is corrupt (gzip -t failed) — NOT deploying."; rm -f "$BK_LOCAL"; exit 1
fi
BK_SIZE=$(stat -c %s "$BK_LOCAL" 2>/dev/null || echo 0)
BK_MIN=${BK_MIN:-50000000}       # the real dump is ~128 MB; anything under 50 MB is wrong
if [ "$BK_SIZE" -lt "$BK_MIN" ]; then
  say "ABORT: backup is only ${BK_SIZE} bytes (expected >= ${BK_MIN}) — NOT deploying."; exit 1
fi

# Off the box as well — a backup that only exists on the machine we are about to
# change is not a backup.
if AWS_DEFAULT_REGION=af-south-1 aws s3 cp "$BK_LOCAL" \
     "s3://alphadirect-db-backups-capetown/alpha-finance/pre-deploy/${BK_NAME}" \
     --metadata "instance=i-02a5d76a61f4f09a5,size_bytes=${BK_SIZE}" --storage-class STANDARD >/dev/null 2>&1; then
  S3_SIZE=$(AWS_DEFAULT_REGION=af-south-1 aws s3api head-object \
            --bucket alphadirect-db-backups-capetown \
            --key "alpha-finance/pre-deploy/${BK_NAME}" \
            --query ContentLength --output text 2>/dev/null || echo 0)
  if [ "$S3_SIZE" = "$BK_SIZE" ]; then
    say "backup OK: ${BK_SIZE} bytes, gzip verified, in S3 at pre-deploy/${BK_NAME}"
  else
    say "ABORT: S3 copy is ${S3_SIZE} bytes but the local backup is ${BK_SIZE} — NOT deploying."; exit 1
  fi
else
  say "ABORT: could not upload the backup to S3 — NOT deploying. Local copy kept at $BK_LOCAL"; exit 1
fi
say "to restore this backup: gunzip -c $BK_LOCAL | docker compose --env-file $ENV_FILE exec -T db psql -U $DB_USER -d $DB_NAME"

# The new dump is verified off the box, so last week's may go. Runs AFTER the
# upload on purpose: retention never runs unless today's backup is safe.
prune_verified_backups "$RETAIN_DAYS"

# ---- 1. build. Nothing is live yet, so a build failure costs nothing --------------
# Keep host cron in step with the repo, exactly as golive.sh does — otherwise a
# deploy that adds or changes a scheduled job silently ships without it.
if [ -f infra/install-crons.sh ]; then
  say "syncing host cron from the repo"
  bash infra/install-crons.sh >/tmp/zdd-cron.log 2>&1 || say "cron sync reported a problem (see /tmp/zdd-cron.log) — continuing"
fi

# Stamp the commit into the image so the running build can be identified later.
# Without this a stale production is indistinguishable from a current one, which
# is how a merged fix sat unshipped for hours on 2026-09-10 with nobody noticing.
GIT_SHA=$(git -C "$COMPOSE_DIR" rev-parse HEAD 2>/dev/null || echo unknown)
BUILD_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
export GIT_SHA BUILD_AT
say "building backend image (commit ${GIT_SHA:0:12})"
if ! "${DC[@]}" build --build-arg GIT_SHA="$GIT_SHA" --build-arg BUILD_AT="$BUILD_AT" backend >/tmp/zdd-build.log 2>&1; then
  say "BUILD FAILED — nothing deployed, live slot untouched. Tail:"; tail -15 /tmp/zdd-build.log; exit 1
fi
say "build ok"

# ---- 2. boot the idle slot. The live slot is serving throughout -------------------
say "starting $TARGET_CT (the live slot keeps serving for this whole step)"
if ! "${DC[@]}" up -d --force-recreate "$TARGET_SVC" >/tmp/zdd-up.log 2>&1; then
  say "COULD NOT START $TARGET_CT — live slot untouched. Tail:"; tail -15 /tmp/zdd-up.log; exit 1
fi

say "waiting for $TARGET_CT to report healthy (allowing ${HEALTH_WAIT}s)"
H=""; T0=$(date -u +%s)
while [ $(( $(date -u +%s) - T0 )) -lt "$HEALTH_WAIT" ]; do
  H=$(docker inspect -f '{{.State.Health.Status}}' "$TARGET_CT" 2>/dev/null)
  [ "$H" = "healthy" ] && break
  sleep 3
done
if [ "$H" != "healthy" ]; then
  say "NEW SLOT NEVER WENT HEALTHY (status='$H') — rolling back by stopping it. Live slot untouched."
  "${DC[@]}" stop "$TARGET_SVC" >/dev/null 2>&1
  exit 1
fi
say "$TARGET_CT healthy after $(( $(date -u +%s) - T0 ))s"

# ---- 3. smoke-test the new slot BEFORE any traffic goes near it -------------------
say "smoke-testing :$TARGET_PORT directly"
CODE=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$TARGET_PORT/" --max-time 20)
if [ "$CODE" != "200" ]; then
  say "SMOKE TEST FAILED (/ returned $CODE) — stopping the new slot, live slot untouched."
  "${DC[@]}" stop "$TARGET_SVC" >/dev/null 2>&1
  exit 1
fi
# Django itself must be answering, not just the port. An unauthenticated API call
# should be refused (401/403), never 5xx — a 5xx here means the app booted broken.
ACODE=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$TARGET_PORT/api/v1/user-profiles/me/" --max-time 20)
case "$ACODE" in
  401|403) say "smoke test ok (/ 200, API $ACODE = auth refused, app is alive)" ;;
  *) say "SMOKE TEST FAILED (API returned $ACODE, expected 401/403) — stopping new slot, live slot untouched."
     "${DC[@]}" stop "$TARGET_SVC" >/dev/null 2>&1; exit 1 ;;
esac

# ---- 4. THE CUTOVER. One file, one graceful reload --------------------------------
say "cutting traffic over to :$TARGET_PORT"
cp -a "$UPSTREAM" "$UPSTREAM.bak-$(date -u +%Y%m%d-%H%M%S)" || { say "could not back up $UPSTREAM"; exit 1; }
sed -i "s/reverse_proxy 127\.0\.0\.1:$ACTIVE_PORT/reverse_proxy 127.0.0.1:$TARGET_PORT/" "$UPSTREAM"
if ! caddy validate --config /etc/caddy/Caddyfile >/dev/null 2>&1; then
  say "CADDY CONFIG INVALID after the swap — restoring and aborting. Traffic never moved."
  cp -a "$UPSTREAM".bak-* "$UPSTREAM" 2>/dev/null
  "${DC[@]}" stop "$TARGET_SVC" >/dev/null 2>&1
  exit 1
fi
if ! systemctl reload caddy; then
  say "CADDY RELOAD FAILED — restoring the old upstream and reloading back."
  cp -a "$UPSTREAM".bak-* "$UPSTREAM" 2>/dev/null
  systemctl reload caddy || say "the restore reload ALSO failed — check 'systemctl status caddy' NOW"
  exit 1
fi
say "traffic is now on :$TARGET_PORT"

# ---- 5. confirm through the real front door, then retire the old slot -------------
sleep 3
LCODE=$(curl -s -o /dev/null -w '%{http_code}' \
        --resolve omni.alphadirect.co.bw:443:127.0.0.1 \
        https://omni.alphadirect.co.bw/api/v1/user-profiles/me/ --max-time 25)
case "$LCODE" in
  401|403|200) say "live check through Caddy: $LCODE — serving" ;;
  *) say "WARNING: live check returned $LCODE. Old slot is still running; investigate before stopping it."; exit 1 ;;
esac

if [ "$DO_FRONTEND" = 1 ]; then
  say "rebuilding frontend (--no-cache; ~8s restart, absorbed by lb_try_duration)"
  "${DC[@]}" build --no-cache frontend >/tmp/zdd-fe.log 2>&1 || { say "frontend build failed; backend cutover stands"; tail -8 /tmp/zdd-fe.log; }
  "${DC[@]}" up -d frontend >/dev/null 2>&1
fi

# ---- 6. RETURN HOME to the `backend` slot -----------------------------------------
# WHY THIS EXISTS (CFO-approved 2026-08-01): 27 files in /etc/cron.d (31 call sites)
# and ops/backup_daily.sh all run `docker compose exec -T backend …`. They address the
# compose SERVICE by name, which a network alias does NOT satisfy — proven on prod:
#   exec -T backend_green …  ->  service "backend_green" is not running
# So if a deploy ended with traffic on green and `backend` stopped, the nightly BACKUP,
# the morning brief, bug triage, FNB sync, m365 sync, staff-loan and exceptions jobs
# would all fail — and fail SILENTLY, which is the worst kind.
# Fix: green is only ever a temporary stand-in to carry traffic during the swap. Every
# deploy finishes with `backend` live on :8000 and green stopped.
if [ "$TARGET_SVC" = "backend_green" ]; then
  say "returning home: rebuilding the backend slot on the new image (traffic stays on green)"
  if ! "${DC[@]}" up -d --force-recreate backend >/tmp/zdd-home.log 2>&1; then
    say "COULD NOT RESTART backend — traffic STAYS on green and green STAYS up, so the site is fine."
    say "!! CRON JOBS WILL FAIL until backend is running (backup, brief, triage, FNB/m365 sync). Fix now. Tail:"
    tail -15 /tmp/zdd-home.log
    exit 1
  fi

  say "waiting for alpha-finance-backend to report healthy (allowing ${HEALTH_WAIT}s)"
  HH=""; TH=$(date -u +%s)
  while [ $(( $(date -u +%s) - TH )) -lt "$HEALTH_WAIT" ]; do
    HH=$(docker inspect -f '{{.State.Health.Status}}' alpha-finance-backend 2>/dev/null)
    [ "$HH" = "healthy" ] && break
    sleep 3
  done
  if [ "$HH" != "healthy" ]; then
    say "backend never went healthy (status='$HH') — leaving traffic on green, green STAYS up."
    say "!! CRON JOBS WILL FAIL until backend is healthy. Investigate before the next nightly run."
    exit 1
  fi

  HCODE=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:8000/api/v1/user-profiles/me/" --max-time 20)
  case "$HCODE" in
    401|403) say "backend smoke ok (API $HCODE = auth refused, app is alive)" ;;
    *) say "backend smoke FAILED (API returned $HCODE) — leaving traffic on green, green STAYS up."
       say "!! CRON JOBS WILL FAIL until backend serves. Investigate."; exit 1 ;;
  esac

  say "cutting traffic back to :8000"
  cp -a "$UPSTREAM" "$UPSTREAM.bak-$(date -u +%Y%m%d-%H%M%S)" || { say "could not back up $UPSTREAM"; exit 1; }
  sed -i "s/reverse_proxy 127\.0\.0\.1:8001/reverse_proxy 127.0.0.1:8000/" "$UPSTREAM"
  if ! caddy validate --config /etc/caddy/Caddyfile >/dev/null 2>&1; then
    say "CADDY CONFIG INVALID on the way home — restoring. Traffic stays on green (site fine, crons broken)."
    cp -a "$UPSTREAM".bak-* "$UPSTREAM" 2>/dev/null; exit 1
  fi
  if ! systemctl reload caddy; then
    say "CADDY RELOAD FAILED on the way home — restoring and reloading back."
    cp -a "$UPSTREAM".bak-* "$UPSTREAM" 2>/dev/null
    systemctl reload caddy || say "the restore reload ALSO failed — check 'systemctl status caddy' NOW"
    exit 1
  fi

  sleep 3
  FCODE=$(curl -s -o /dev/null -w '%{http_code}' \
          --resolve omni.alphadirect.co.bw:443:127.0.0.1 \
          https://omni.alphadirect.co.bw/api/v1/user-profiles/me/ --max-time 25)
  case "$FCODE" in
    401|403|200) say "live check through Caddy on :8000: $FCODE — serving" ;;
    *) say "WARNING: live check on :8000 returned $FCODE — green is still up, investigate before stopping it."; exit 1 ;;
  esac

  say "stopping the green stand-in"
  "${DC[@]}" stop backend_green >/dev/null 2>&1
  say "DONE — live on :8000 (backend), green stopped. Cron jobs can reach 'backend'."
else
  say "stopping the retired slot $OLD_CT"
  "${DC[@]}" stop "$OLD_SVC" >/dev/null 2>&1
  say "DONE — live on :$TARGET_PORT (backend), $OLD_CT stopped. Cron jobs can reach 'backend'."
fi

# Final guard: never leave this script having stopped the service cron depends on.
if ! docker ps --format '{{.Names}}' | grep -qx alpha-finance-backend; then
  say "!! POST-DEPLOY CHECK FAILED: alpha-finance-backend is NOT running. Cron jobs (backup, brief,"
  say "   triage, FNB/m365 sync, staff loans, exceptions) will fail silently. Start it NOW."
  exit 1
fi
say "post-deploy check: alpha-finance-backend is running — cron is safe"

# ---------------------------------------------------------------------------
# Record the release in the CFO build log (2026-09-09).
#
# devlog_deploy is the ONLY writer of live_at — a skill may claim it built
# something, but only a release proves it reached prod. Its docstring said it
# was "called at the end of infra/host/deploy-zero-downtime.sh"; it never was.
# Nothing anywhere called it, so the dashboard's "Finished today" band could
# never fill no matter how much shipped. This is that call.
#
# The logic now lives in record-release.sh beside this file, because THIS SCRIPT
# IS NOT THE ONLY DEPLOY PATH — the Windows seat deploys over SSM with its own
# git reset + compose build + up and never runs this file, so while the call
# lived only in here, most of the CFO's own releases still went unrecorded.
# One implementation, every caller. This never fails the deploy.
# ---------------------------------------------------------------------------
DEVLOG_MARKER=${DEVLOG_MARKER:-/var/lib/alpha-finance/last-deployed-sha}

record_release() {
  COMPOSE_DIR="$COMPOSE_DIR" DEVLOG_MARKER="$DEVLOG_MARKER" RR_DC="${DC[*]}" \
    bash "$COMPOSE_DIR/infra/host/record-release.sh" || true
  return 0
}

record_release || true

# ---- 5. post-deploy smoke: open every menu route before calling this done ----
# Added 2026-09-11 after the Market Benchmark report went live with its menu
# entry working and its data route missing — a red "HTTP 404" box for every
# user, through a green CI and a "successful" deploy. See post-deploy-smoke.sh.
#
# This runs AFTER the flip, so it cannot prevent the bad code going live; what
# it prevents is a broken deploy being REPORTED as clean. A non-zero exit here
# means someone must look now, not next week.
# Guard on EXISTENCE, not the executable bit. Every infra/host script in this
# repo is mode 644 and invoked with `bash` — including this one. The first
# version of this block tested -x, so on the very first real deploy the check
# silently did not run: the file was there, unexecutable, and the branch was
# skipped without a word. A check that quietly does not run is worse than no
# check, because it reports the same silence as a pass.
if [ -f "$COMPOSE_DIR/infra/host/post-deploy-smoke.sh" ]; then
  if bash "$COMPOSE_DIR/infra/host/post-deploy-smoke.sh"; then
    say "post-deploy smoke: every menu route reachable"
  else
    rc=$?
    if [ "$rc" = "2" ]; then
      say "⚠️  post-deploy smoke: COULD NOT RUN (no QA token at /etc/alpha-finance/qa-token)."
      say "⚠️  Menu routes were NOT verified. This deploy is unproven, not proven."
    else
      say "🔴 post-deploy smoke FAILED — a page in the menu is live and broken."
      say "🔴 Traffic is already on the new slot. Check the failures above and fix forward."
      exit 1
    fi
  fi
else
  say "⚠️  post-deploy smoke: script not found at infra/host/post-deploy-smoke.sh — routes NOT verified"
fi

# ---- 6. confirm production is now serving the commit we just built ----------
# The health endpoint reports the commit baked into the running image. If it
# does not match HEAD, the flip did not pick up the new image and the deploy has
# quietly achieved nothing — which is the failure this whole file now exists to
# make impossible to miss.
# Re-read the live slot from Caddy: ACTIVE_PORT is the slot that was live
# BEFORE the flip, and querying it here would check the slot we just retired.
now_port=$(grep -oE 'reverse_proxy 127\.0\.0\.1:(8000|8001)' "$UPSTREAM" \
            | grep -oE '(8000|8001)' | head -1)
live_sha=$(curl -s --max-time 15 "http://127.0.0.1:${now_port:-8000}/" 2>/dev/null \
            | python3 -c 'import json,sys;print(json.load(sys.stdin).get("commit","unknown"))' 2>/dev/null || echo unknown)
if [ "$live_sha" = "unknown" ]; then
  say "version check: production does not report a commit (older image?) — cannot confirm"
elif [ "$live_sha" = "$GIT_SHA" ]; then
  say "version check: :${now_port} is serving ${live_sha:0:12} — matches what was built"
else
  say "🔴 version check: production reports ${live_sha:0:12} but we built ${GIT_SHA:0:12}."
  say "🔴 The new image is NOT live. Do not report this deploy as done."
  exit 1
fi
