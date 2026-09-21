#!/usr/bin/env bash
# Fresh-instance host bootstrap for the omni EC2. Run ONCE as root after the
# repo is cloned to /opt/alpha-finance on a rebuilt box, before the first
# deploy. Closes the chicken-and-egg gap: the deploy script (golive.sh) and the
# cron installer both live in the repo, but something has to place golive.sh and
# kick the first cron sync — that is this script.
#
#   sudo bash /opt/alpha-finance/infra/bootstrap-host.sh
#
# Idempotent. Assumes /etc/alpha-finance/.env already exists (secrets are NOT in
# the repo). Does not build or start containers — that is golive.sh / compose.
set -uo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "bootstrap-host.sh: must run as root" >&2
    exit 1
fi

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "== log dir =="
mkdir -p /var/log/alpha-finance && echo "ok /var/log/alpha-finance"

echo "== .env presence check =="
if [ ! -f /etc/alpha-finance/.env ]; then
    echo "WARN /etc/alpha-finance/.env missing — restore it before deploying (holds all secrets)" >&2
fi

echo "== deploy script =="
install -o root -g root -m 0755 "$REPO/infra/host/golive.sh" /opt/alpha-finance/golive.sh
echo "ok /opt/alpha-finance/golive.sh"

echo "== backup script =="
install -o root -g root -m 0755 "$REPO/infra/host/alpha-finance-backup.sh" /usr/local/bin/alpha-finance-backup.sh
echo "ok /usr/local/bin/alpha-finance-backup.sh"

echo "== cron jobs =="
bash "$REPO/infra/install-crons.sh"

echo "== done =="
echo "bootstrap complete — run a deploy (golive.sh) to build & start containers."
