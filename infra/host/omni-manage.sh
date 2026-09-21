#!/usr/bin/env bash
# omni-manage — run a Django management command inside the omni backend, RIDING OUT
# a deploy window (audit follow-up 2026-09-04).
#
# Why: on 2-Sep-2026 the 07:05 UTC daily brief hit "service backend is not running"
# because the container was mid blue/green flip, and the ENTIRE company lost its
# 1-September attendance record (5 rows written instead of ~100). Four people were
# later docked pay for 1-Sep against a day that had no record. Every cron that
# `docker compose exec`s into the backend has the same hole; this wrapper closes it
# for the record-writing ones first.
#
# Usage:  omni-manage <manage.py args...>
#   e.g.  omni-manage send_daily_brief --no-email
# Retries up to OMNI_MANAGE_ATTEMPTS (default 6) times, OMNI_MANAGE_SLEEP (default
# 120) seconds apart — a 10-minute window, longer than any flip — but ONLY when the
# failure is the container being unavailable. A command that runs and fails on its
# own terms is NOT retried (it may have written half its work).
set -u
cd /opt/alpha-finance || exit 97
ATTEMPTS="${OMNI_MANAGE_ATTEMPTS:-6}"
SLEEP="${OMNI_MANAGE_SLEEP:-120}"
COMPOSE=(/usr/bin/docker compose --env-file /etc/alpha-finance/.env)

for ((i = 1; i <= ATTEMPTS; i++)); do
  # Is the backend there at all? (compose ps prints nothing while it is being recreated.)
  if ! "${COMPOSE[@]}" ps --status running --services 2>/dev/null | grep -qx backend; then
    echo "[omni-manage] $(date -u +%FT%TZ) attempt $i/$ATTEMPTS: backend not running (deploy window?) — waiting ${SLEEP}s" >&2
    sleep "$SLEEP"
    continue
  fi
  "${COMPOSE[@]}" exec -T backend python manage.py "$@"
  rc=$?
  # 125/126/127 = docker could not exec into the container (gone mid-command).
  if [ $rc -eq 125 ] || [ $rc -eq 126 ] || [ $rc -eq 127 ]; then
    echo "[omni-manage] $(date -u +%FT%TZ) attempt $i/$ATTEMPTS: exec failed rc=$rc — waiting ${SLEEP}s" >&2
    sleep "$SLEEP"
    continue
  fi
  exit $rc
done
echo "[omni-manage] $(date -u +%FT%TZ) GAVE UP after $ATTEMPTS attempts: backend never became available for: $*" >&2
exit 96
