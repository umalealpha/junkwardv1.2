#!/usr/bin/env bash
#
# ops/backup_daily.sh — RETIRED 2026-08-25. Do not schedule this.
#
# ─────────────────────────────────────────────────────────────────────────────
# WHY IT WAS RETIRED (CFO decision, 2026-08-25)
#
# This job pushed the encrypted dump to a private GitHub repo. It last succeeded
# on 7 JULY 2026. Every night after that the push was refused:
#
#     File dumps/omni-2026-08-25.sql.gz.gpg is 139.05 MB;
#     this exceeds GitHub's file size limit of 100.00 MB
#
# The database had simply grown past 100MB compressed. The dump and the GPG step
# kept working, so the artifact looked current and a dry-run passed — which is
# exactly why a readiness check reported this leg as healthy. Nothing looked at
# the push result. Seven weeks with no off-site copy from this leg.
#
# It was ALSO scheduled twice, at 04:00, under both `ubuntu` and `root`. root has
# no GPG key, so root's copy died at encrypt every night AND both copies wrote to
# the same log file — so the log showed root's failure and hid the other job.
#
# The surviving off-site job is ops/backup_s3_daily.sh (00:00 UTC), which was
# working the whole time and is now GPG-encrypted before upload, plus
# `manage.py alert_backup_not_landed` (02:00) which shouts if a night is missed.
#
# Kept in the tree, unscheduled, for provenance: the 30-day GitHub retention and
# the manifest format are the record of what off-site meant before this date.
# ─────────────────────────────────────────────────────────────────────────────
#
# Original header follows.
#
#
# CFO directive 2026-05-19: nightly backup of the production database
# (and a snapshot of the code + image manifest) pushed to a dedicated
# private GitHub repo with 30-day rolling retention.
#
# What it does:
#   1. pg_dump  → gzip                        (compressed plaintext SQL)
#   2. gpg --encrypt --recipient <CFO key>    (recipient holds private key)
#   3. write manifest.json (git sha, image hashes, entity row counts)
#   4. git add / commit / push to
#      alphadirectinsurance/ERP-OMNI-backup
#   5. prune files older than 30 days; force-push the pruned tree
#
# Cron: `0 6 * * *` (06:00 Africa/Maputo) on prod EC2 i-02a5d76a61f4f09a5.
#
# Setup pre-reqs (one-off):
#   * GPG public key for the CFO imported into the ubuntu user's keyring
#     under uid "Alpha Direct ERP Backup". Private key held offline by CFO.
#   * Deploy key for ERP-OMNI-backup repo installed at ~/.ssh/erp_omni_backup
#     and ssh-config alias `github-backup`.
#   * `dpkg -l postgresql-client gnupg gzip git` installed.
#
# Test manually:
#   sudo -u ubuntu /opt/alpha-finance/ops/backup_daily.sh --dry-run
#
set -euo pipefail

LOG_FILE="/var/log/alpha-finance/backup-$(date -u +%Y%m%d).log"
mkdir -p "$(dirname "$LOG_FILE")"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "================================================================"
echo "[$(date -u +%FT%TZ)] backup_daily start"
echo "================================================================"

DRY_RUN=0
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=1
  echo "DRY RUN — pg_dump + manifest will be produced but not pushed."
fi

# ── Config ─────────────────────────────────────────────────────────────
BACKUP_REPO_DIR="/var/lib/alpha-finance/backup-repo"
BACKUP_REPO_GIT="git@github-backup:alphadirectinsurance/ERP-OMNI-backup.git"
GPG_RECIPIENT="${GPG_RECIPIENT:-Alpha Direct ERP Backup}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"
SOURCE_REPO_DIR="/opt/alpha-finance"
COMPOSE_ENV="/etc/alpha-finance/.env"

DATE="$(date -u +%Y-%m-%d)"
TS="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
DUMP_NAME="omni-$DATE.sql.gz.gpg"
MANIFEST_NAME="manifest-$DATE.json"

# ── 1. pg_dump ────────────────────────────────────────────────────────
TMP_DIR="$(mktemp -d /tmp/omni-backup.XXXXXX)"
trap 'rm -rf "$TMP_DIR"' EXIT

echo "[1/5] pg_dump → ${TMP_DIR}/omni.sql.gz"
# Extract only the DB credentials from /etc/alpha-finance/.env.
# Do NOT `source` the file: it is root-owned 0640 (this script runs as
# ubuntu), and docker env-file syntax is looser than shell — values that
# docker accepts can be bash syntax errors. Grep the three keys via sudo
# instead, the same pattern alpha-finance-backup.sh (root S3 backup) uses.
# docker-compose.yml maps DB_USER/DB_NAME/DB_PASSWORD onto the Postgres
# container's POSTGRES_* vars; resolve in that order, POSTGRES_* fallback.
_envget() { sudo grep -E "^${1}=" "$COMPOSE_ENV" 2>/dev/null | head -1 | cut -d= -f2- || true; }
DB_USER_FX="$(_envget DB_USER)";     DB_USER_FX="${DB_USER_FX:-$(_envget POSTGRES_USER)}";     DB_USER_FX="${DB_USER_FX:-postgres}"
DB_NAME_FX="$(_envget DB_NAME)";     DB_NAME_FX="${DB_NAME_FX:-$(_envget POSTGRES_DB)}";       DB_NAME_FX="${DB_NAME_FX:-alpha_finance}"
DB_PASS_FX="$(_envget DB_PASSWORD)"; DB_PASS_FX="${DB_PASS_FX:-$(_envget POSTGRES_PASSWORD)}"; DB_PASS_FX="${DB_PASS_FX:-postgres}"
# Use the running db container's pg_dump so we don't need PG client on
# the host. Pass PGPASSWORD via `-e` because docker compose exec doesn't
# inherit the container's env when pg_dump prompts for a password.
sudo docker compose --env-file "$COMPOSE_ENV" \
    -f "$SOURCE_REPO_DIR/docker-compose.yml" exec -T \
    -e PGPASSWORD="$DB_PASS_FX" \
    db \
    pg_dump --clean --if-exists --no-owner --no-privileges \
            -U "$DB_USER_FX" \
            "$DB_NAME_FX" \
    | gzip -c > "$TMP_DIR/omni.sql.gz"
SQL_SIZE=$(stat -c%s "$TMP_DIR/omni.sql.gz")
echo "  dump size: $(numfmt --to=iec --suffix=B $SQL_SIZE)"

# ── 2. GPG encrypt ────────────────────────────────────────────────────
echo "[2/5] gpg --encrypt → ${DUMP_NAME}"
gpg --batch --yes --trust-model always \
    --recipient "$GPG_RECIPIENT" \
    --output "$TMP_DIR/$DUMP_NAME" \
    --encrypt "$TMP_DIR/omni.sql.gz"
rm -f "$TMP_DIR/omni.sql.gz"   # plaintext + gzip-only never persisted

# ── 3. Manifest ───────────────────────────────────────────────────────
echo "[3/5] write ${MANIFEST_NAME}"
cd "$SOURCE_REPO_DIR"
CODE_SHA=$(git rev-parse HEAD)
CODE_BRANCH=$(git rev-parse --abbrev-ref HEAD || echo "detached")
BACKEND_IMAGE=$(sudo docker compose --env-file "$COMPOSE_ENV" images backend --format json 2>/dev/null | tail -1 || echo '{}')
FRONTEND_IMAGE=$(sudo docker compose --env-file "$COMPOSE_ENV" images frontend --format json 2>/dev/null | tail -1 || echo '{}')

# Row counts per entity — best-effort, never block the backup if shell errors
ROWS=$(sudo docker compose --env-file "$COMPOSE_ENV" exec -T backend \
       python manage.py shell -c "
import json
from core.models import Company
from ledger.models import JournalEntry, Account
from billing.models import Contact
from payroll.models import Employee
from assets.models import Asset
out = {}
for c in Company.objects.order_by('code'):
    out[c.code] = {
        'journal_entries': JournalEntry.objects.filter(company=c).count(),
        'accounts':        Account.objects.filter(owner_company=c).count(),
        'contacts':        Contact.objects.filter(company=c).count(),
        'employees':       Employee.objects.filter(company=c).count(),
        'assets':          Asset.objects.filter(company=c).count(),
    }
print(json.dumps(out))
" 2>/dev/null | tail -1 || echo "{}")

cat > "$TMP_DIR/$MANIFEST_NAME" <<MANIFEST
{
  "backup_timestamp_utc": "$TS",
  "code": {
    "branch": "$CODE_BRANCH",
    "sha":    "$CODE_SHA"
  },
  "images": {
    "backend":  $BACKEND_IMAGE,
    "frontend": $FRONTEND_IMAGE
  },
  "dump_file": "$DUMP_NAME",
  "dump_size_bytes": $SQL_SIZE,
  "gpg_recipient": "$GPG_RECIPIENT",
  "entity_row_counts": $ROWS,
  "ec2_instance":  "i-02a5d76a61f4f09a5",
  "region":        "af-south-1"
}
MANIFEST

# ── 4. Push to backup repo ────────────────────────────────────────────
if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "[4/5] dry-run: skipping git push"
  ls -la "$TMP_DIR"
  exit 0
fi

echo "[4/5] git push to $BACKUP_REPO_GIT"
if [[ ! -d "$BACKUP_REPO_DIR" ]]; then
  echo "  cloning backup repo (first run)…"
  git clone "$BACKUP_REPO_GIT" "$BACKUP_REPO_DIR"
fi

cd "$BACKUP_REPO_DIR"
git fetch origin --prune
git reset --hard origin/main 2>/dev/null || git checkout -b main
mkdir -p dumps manifests
cp "$TMP_DIR/$DUMP_NAME"     "dumps/$DUMP_NAME"
cp "$TMP_DIR/$MANIFEST_NAME" "manifests/$MANIFEST_NAME"

# ── 5. Prune retention window ────────────────────────────────────────
echo "[5/5] prune files older than ${RETENTION_DAYS} days"
find dumps     -type f -mtime +"$RETENTION_DAYS" -name "omni-*.sql.gz.gpg" -delete -print || true
find manifests -type f -mtime +"$RETENTION_DAYS" -name "manifest-*.json"   -delete -print || true

git add -A
if git diff --cached --quiet; then
  echo "  no changes to commit (already backed up today?)"
  exit 0
fi
git -c user.email="backup-bot@alphadirect.co.bw" \
    -c user.name="Omni Backup Bot" \
    commit -m "backup $DATE  code=${CODE_SHA:0:7}  size=$(numfmt --to=iec --suffix=B $SQL_SIZE)"
# Force-push only when prune actually removed history-bearing files.
# Normal day = fast-forward append. Use --force-with-lease as safety.
git push origin main --force-with-lease

echo "================================================================"
echo "[$(date -u +%FT%TZ)] backup_daily done"
echo "================================================================"
