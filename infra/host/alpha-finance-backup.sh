#!/usr/bin/env bash
# Nightly Postgres backup -> S3 (af-south-1). Installed at /usr/local/bin/ on the
# omni EC2 by infra/bootstrap-host.sh; scheduled by infra/cron/alpha-finance-backup.cron.
# Reads DB creds from /etc/alpha-finance/.env at runtime (no secrets in this file).
set -euo pipefail
export PATH=/snap/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
S3_BUCKET=alphadirect-db-backups-capetown
S3_PREFIX=alpha-finance
TS=$(date -u +%Y-%m-%dT%H-%M-%SZ)
NAME="alpha_finance_${TS}.sql.gz"
LOCAL=/tmp/${NAME}
DB_PW=$(sudo grep -E '^DB_PASSWORD=' /etc/alpha-finance/.env | head -1 | cut -d= -f2-)
DB_USER=$(sudo grep -E '^DB_USER=' /etc/alpha-finance/.env | head -1 | cut -d= -f2-)
DB_NAME=$(sudo grep -E '^DB_NAME=' /etc/alpha-finance/.env | head -1 | cut -d= -f2-)
DB_USER=${DB_USER:-alpha_admin}
DB_NAME=${DB_NAME:-alpha_finance}
if [ -z "${DB_PW}" ]; then echo 'ERROR: DB_PASSWORD not found in /etc/alpha-finance/.env' >&2; exit 1; fi
cd /opt/alpha-finance
docker compose --env-file /etc/alpha-finance/.env exec -T -e PGPASSWORD="${DB_PW}" db pg_dump -U "${DB_USER}" "${DB_NAME}" --no-owner --clean --if-exists | gzip -9 > "${LOCAL}"
SIZE=$(stat -c %s "${LOCAL}")
AWS_DEFAULT_REGION=af-south-1 aws s3 cp "${LOCAL}" "s3://${S3_BUCKET}/${S3_PREFIX}/daily/${NAME}" --metadata "instance=i-02a5d76a61f4f09a5,size_bytes=${SIZE}" --storage-class STANDARD
rm -f "${LOCAL}"
logger -t alpha-finance-backup "Backup ${NAME} uploaded, ${SIZE} bytes"
echo "OK ${NAME} ${SIZE}"
