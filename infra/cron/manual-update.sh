#!/usr/bin/env bash
#
# Omni User Manual — nightly "What's New" updater.
#
# Called by cron at 22:00 UTC (= Botswana midnight, CAT/UTC+2). The backend
# container has no .git, so git log runs here on the host (as ubuntu, who owns
# the checkout) and the commits are piped into the Django command, which turns
# new feat: commits into plain-English manual entries. Idempotent — the command
# dedupes by commit sha, so the 8-day overlap window never double-lists and a
# missed night self-heals on the next run.
#
# Field separator is 0x1F (%x1f): <sha>\x1f<YYYY-MM-DD>\x1f<subject>.
set -euo pipefail

cd /opt/alpha-finance

sudo -u ubuntu git -C /opt/alpha-finance log --since="8 days ago" --no-merges \
  --pretty=format:'%H%x1f%cs%x1f%s' \
  | /usr/bin/docker compose --env-file /etc/alpha-finance/.env exec -T backend \
      python manage.py update_user_manual
