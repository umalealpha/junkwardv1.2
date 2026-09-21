#!/usr/bin/env bash
#
# ops/backup_watch.sh — shout if last night's off-site database copy did not land.
#
# WHY (2026-08-25): the off-site copy of the omni database stopped landing on
# 7 JULY 2026 and nobody knew for SEVEN WEEKS. The job ran every night, dumped
# the database, encrypted it, and then had its push refused by GitHub's 100MB
# file limit. The failure was in the LAST step and nothing looked at the result —
# so the artifact looked current and a dry-run passed. A backup nobody checks is
# not a backup, it is a cron job.
#
# Detection lives HERE, on the host, because the host already has the AWS CLI the
# backup itself uses; boto3 is deliberately not a dependency of the backend
# image. The email lives in Django (manage.py alert_backup_not_landed) because
# that is where the house template and the mail path are.
#
# Reads the heartbeat that ops/backup_s3_daily.sh writes on success, so three
# states are distinguishable — which listing the bucket cannot do:
#   * fresh heartbeat     → silent, exit 0
#   * stale heartbeat     → the job used to succeed and has stopped
#   * no heartbeat at all → it has never succeeded, or was removed
# Anything it cannot read is treated as NOT VERIFIED. Fail-closed: if we cannot
# tell, we say we cannot tell.
#
# It repairs nothing. It only makes a silent failure loud.
#
# Cron: infra/cron/backup-watch.cron (02:00 UTC — after the 00:00 backup window)
# Test: ops/backup_watch.sh --self-test    (no AWS, no email; checks the arithmetic)
set -uo pipefail
export PATH=/snap/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin

S3_BUCKET="${S3_BUCKET:-alphadirect-db-backups-capetown}"
S3_PREFIX="${S3_PREFIX:-alpha-finance}"
# One night plus six hours of slack: a copy that starts at 00:00 and takes an
# hour is fine when this runs at 02:00, and one missed night is caught the next.
MAX_AGE_HOURS="${MAX_AGE_HOURS:-30}"
AWS_REGION_="${AWS_REGION_:-af-south-1}"
APP_DIR="${APP_DIR:-/opt/alpha-finance}"
ENV_FILE="${ENV_FILE:-/etc/alpha-finance/.env}"

# The backup stamps 2026-08-25T00-00-01Z — colons are awkward in object keys,
# hence the dashes, hence this conversion before `date -d` will accept it.
stamp_to_epoch() {
  local stamp="$1" iso
  iso=$(printf '%s' "$stamp" \
        | sed -n 's/^\([0-9]\{4\}-[0-9][0-9]-[0-9][0-9]\)T\([0-9][0-9]\)-\([0-9][0-9]\)-\([0-9][0-9]\)Z$/\1T\2:\3:\4Z/p')
  [ -n "$iso" ] || { echo 0; return; }
  date -u -d "$iso" +%s 2>/dev/null || echo 0
}

if [ "${1:-}" = '--self-test' ]; then
  fail=0
  [ "$(stamp_to_epoch '2026-08-25T00-00-01Z')" = "1787616001" ] || { echo 'FAIL: good stamp'; fail=1; }
  [ "$(stamp_to_epoch 'not-a-timestamp')"      = "0" ]          || { echo 'FAIL: junk rejected'; fail=1; }
  [ "$(stamp_to_epoch '')"                     = "0" ]          || { echo 'FAIL: empty rejected'; fail=1; }
  [ "$(stamp_to_epoch '2026-08-25T00:00:01Z')" = "0" ]          || { echo 'FAIL: wrong shape rejected'; fail=1; }
  [ "$fail" = 0 ] && echo 'self-test OK'
  exit "$fail"
fi

KEY="s3://${S3_BUCKET}/${S3_PREFIX}/last-success.txt"
NOW=$(date -u +%s)
DETAIL=''

if ! HEARTBEAT=$(AWS_DEFAULT_REGION="${AWS_REGION_}" aws s3 cp "${KEY}" - 2>/dev/null); then
  DETAIL="There is no record of the nightly off-site copy ever succeeding. The marker file at ${KEY} could not be read. Either the backup has never run since it was set up, it has been removed, or this server has lost its access to the off-site store."
else
  STAMP=$(printf '%s' "${HEARTBEAT}" | awk '{print $1}')
  WHEN=$(stamp_to_epoch "${STAMP}")
  if [ "${WHEN}" -eq 0 ]; then
    DETAIL="The nightly off-site copy left a record that cannot be read: '${HEARTBEAT}'. Treat the backup as unverified until someone looks."
  else
    AGE_H=$(( (NOW - WHEN) / 3600 ))
    if [ "${AGE_H}" -gt "${MAX_AGE_HOURS}" ]; then
      DETAIL="The nightly off-site copy has not succeeded for ${AGE_H} hours. The last one landed at ${STAMP}. Last recorded success: ${HEARTBEAT}"
    fi
  fi
fi

if [ -z "${DETAIL}" ]; then
  echo "OK off-site copy is current: ${HEARTBEAT}"
  exit 0
fi

# Print BEFORE emailing, so the cron log carries the finding even if mail fails.
echo "BACKUP NOT VERIFIED: ${DETAIL}"
cd "${APP_DIR}" || exit 1
/usr/bin/docker compose --env-file "${ENV_FILE}" exec -T backend \
  python manage.py alert_backup_not_landed --detail "${DETAIL}" \
  || echo 'WARN: the alert email did not send — the line above is the record.'
