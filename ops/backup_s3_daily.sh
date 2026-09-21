#!/usr/bin/env bash
#
# ops/backup_s3_daily.sh — the nightly OFF-SITE database copy. 00:00 UTC.
#
# Provenance: this is the script that was living ONLY at
# /usr/local/bin/alpha-finance-backup.sh on prod, which infra/cron/
# alpha-finance-backup.cron itself flagged as "lives on the host, not in this
# repo — its durability is a separate follow-up". Brought into the repo
# 2026-08-25 so the one job that actually protects the database is not an
# undocumented file on a single server.
#
# WHY THIS IS NOW THE ONLY OFF-SITE JOB (CFO decision, 2026-08-25)
# ────────────────────────────────────────────────────────────────
# There were three overlapping backup jobs and only this one worked:
#
#   1. THIS one (00:00, root, via /etc/cron.d/alpha-finance-backup) — succeeded
#      every night. 1,516 objects in the bucket when checked.
#   2. ops/backup_daily.sh under `ubuntu` (04:00) — encrypted fine, then FAILED
#      to push: "File dumps/omni-2026-08-25.sql.gz.gpg is 139.05 MB; this
#      exceeds GitHub's file size limit of 100.00 MB". The off-site GitHub copy
#      last landed on 7 July 2026. Seven weeks of silent failure, because
#      nothing looked at the push result.
#   3. The SAME ops/backup_daily.sh ALSO under `root` (04:00) — root has no GPG
#      key, so it died at encrypt every night AND clobbered the shared log file,
#      which is what anyone reading the log actually saw.
#
# So the GitHub leg is retired and this leg is hardened instead.
#
# WHAT CHANGED HERE vs the host copy
#   * GPG. The dump used to go up as plain gzipped SQL — readable by anyone with
#     read access to the bucket. It is now encrypted to the offline CFO key
#     BEFORE it leaves the machine, so S3's own AES256 is the second lock, not
#     the only one.
#   * Fail-closed. If gpg fails for any reason the job ABORTS. It must never
#     fall back to uploading plaintext — a silent downgrade to clear text is
#     worse than a missed night, because a missed night gets noticed.
#   * A heartbeat object, so ops/backup_watch.sh can tell "ran and succeeded"
#     from "did not run at all" without parsing logs.
#
# Pre-reqs on the host (one-off):
#   * GPG public key "Alpha Direct ERP Backup" (4BB3686BD867019B21378968B43E3D7AEDF07326)
#     imported into the keyring of the user this runs as. The PRIVATE key is
#     held offline by the CFO — this script can encrypt and can never decrypt.
#   * awscli, gnupg, gzip. Instance role with s3:PutObject on the bucket.
#
# Restore (the drill this exists for):
#   aws s3 cp s3://<bucket>/alpha-finance/daily/<name>.sql.gz.gpg .
#   gpg --decrypt <name>.sql.gz.gpg | gunzip | psql -U alpha_admin -d <scratch_db>
#   ...then count rows against prod. Needs the CFO's offline private key.
set -euo pipefail
export PATH=/snap/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin

S3_BUCKET="${S3_BUCKET:-alphadirect-db-backups-capetown}"
S3_PREFIX="${S3_PREFIX:-alpha-finance}"
GPG_RECIPIENT="${GPG_RECIPIENT:-Alpha Direct ERP Backup}"
INSTANCE_ID="${INSTANCE_ID:-i-02a5d76a61f4f09a5}"
AWS_REGION_="${AWS_REGION_:-af-south-1}"

TS=$(date -u +%Y-%m-%dT%H-%M-%SZ)
BASE="alpha_finance_${TS}.sql.gz"
LOCAL="/tmp/${BASE}"
ENC="${LOCAL}.gpg"

cleanup() { rm -f "${LOCAL}" "${ENC}"; }
trap cleanup EXIT

DB_PW=$(sudo grep -E '^DB_PASSWORD=' /etc/alpha-finance/.env | head -1 | cut -d= -f2-)
DB_USER=$(sudo grep -E '^DB_USER=' /etc/alpha-finance/.env | head -1 | cut -d= -f2-)
DB_NAME=$(sudo grep -E '^DB_NAME=' /etc/alpha-finance/.env | head -1 | cut -d= -f2-)
DB_USER=${DB_USER:-alpha_admin}
DB_NAME=${DB_NAME:-alpha_finance}
if [ -z "${DB_PW}" ]; then
  echo 'ERROR: DB_PASSWORD not found in /etc/alpha-finance/.env' >&2
  exit 1
fi

# Refuse to start without the encryption key, rather than discovering it after
# the dump — this is the failure mode that killed the root 04:00 job nightly.
if ! gpg --list-keys "${GPG_RECIPIENT}" >/dev/null 2>&1; then
  echo "ERROR: GPG key '${GPG_RECIPIENT}' is not in $(whoami)'s keyring." >&2
  echo "       Refusing to run: an unencrypted database dump must never be" >&2
  echo "       uploaded as a fallback." >&2
  exit 1
fi

echo "[1/4] pg_dump -> ${LOCAL}"
cd /opt/alpha-finance
docker compose --env-file /etc/alpha-finance/.env exec -T \
  -e PGPASSWORD="${DB_PW}" db \
  pg_dump -U "${DB_USER}" "${DB_NAME}" --no-owner --clean --if-exists \
  | gzip -9 > "${LOCAL}"

RAW_SIZE=$(stat -c %s "${LOCAL}")
if [ "${RAW_SIZE}" -lt 1000000 ]; then
  echo "ERROR: dump is only ${RAW_SIZE} bytes — that is not a real database." >&2
  exit 1
fi

echo "[2/4] gpg --encrypt (recipient: ${GPG_RECIPIENT})"
gpg --batch --yes --trust-model always \
    --recipient "${GPG_RECIPIENT}" --encrypt --output "${ENC}" "${LOCAL}"
[ -s "${ENC}" ] || { echo 'ERROR: gpg produced no output.' >&2; exit 1; }
ENC_SIZE=$(stat -c %s "${ENC}")

echo "[3/4] upload s3://${S3_BUCKET}/${S3_PREFIX}/daily/${BASE}.gpg"
AWS_DEFAULT_REGION="${AWS_REGION_}" aws s3 cp "${ENC}" \
  "s3://${S3_BUCKET}/${S3_PREFIX}/daily/${BASE}.gpg" \
  --metadata "instance=${INSTANCE_ID},raw_bytes=${RAW_SIZE},enc_bytes=${ENC_SIZE},encrypted=gpg" \
  --storage-class STANDARD --only-show-errors

# [4/4] A heartbeat, overwritten each run. ops/backup_watch.sh reads THIS rather
# than listing the whole daily/ prefix, so "the job did not run" and "the job
# ran and failed" are different, visible states.
echo "[4/4] heartbeat"
printf '%s\n' "${TS} ${BASE}.gpg raw=${RAW_SIZE} enc=${ENC_SIZE}" \
  | AWS_DEFAULT_REGION="${AWS_REGION_}" aws s3 cp - \
      "s3://${S3_BUCKET}/${S3_PREFIX}/last-success.txt" --only-show-errors

logger -t alpha-finance-backup "Backup ${BASE}.gpg uploaded, ${ENC_SIZE} bytes (gpg)"
echo "OK ${BASE}.gpg raw=${RAW_SIZE} enc=${ENC_SIZE}"
