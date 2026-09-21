#!/usr/bin/env bash
# Omni after-hours go-live (CFO 2026-07-22): pull main, rebuild backend+frontend,
# restart, health-check, self-remove the one-shot cron. flock guard prevents the
# 18:05 timer and a manual "go now" from ever running at the same time.
# Staff auto-emails (Leave Excuse rules) stay OFF unless LEAVE_EXCUSE_AUTOSEND=1.
#
# Installed at /opt/alpha-finance/golive.sh on the omni EC2 by
# infra/bootstrap-host.sh. Kept in the repo so the deploy procedure itself
# survives an instance rebuild.
set -uo pipefail
exec 9>/tmp/omni-golive.lock
if ! flock -n 9; then echo "golive already running $(date -u)" >> /var/log/alpha-finance/golive.log; exit 0; fi
cd /opt/alpha-finance || exit 1
LOG=/var/log/alpha-finance/golive.log
{
  echo "=== GOLIVE START $(date -u) ==="
  sudo -u ubuntu git pull --ff-only origin main
  echo "--- on commit ---"; sudo -u ubuntu git log -1 --oneline
  # This script runs as an INSTALLED COPY at /opt/alpha-finance/golive.sh (placed by
  # infra/bootstrap-host.sh), so `git pull` does NOT update it -- a fix to the deploy
  # procedure itself would sit in the repo doing nothing until somebody remembered to
  # re-run bootstrap. Refresh the copy after every pull. The RUNNING process keeps the
  # old code for this pass (bash has already read it); the next deploy is current.
  if [ -f infra/host/golive.sh ] && ! cmp -s infra/host/golive.sh /opt/alpha-finance/golive.sh; then
    install -o root -g root -m 0755 infra/host/golive.sh /opt/alpha-finance/golive.sh \
      && echo "golive.sh refreshed from the repo — the NEXT deploy uses it"
  fi
  if [ -f infra/install-crons.sh ]; then echo cron-sync; bash infra/install-crons.sh || true; fi
  # Orphan sweep (11-Aug-2026). Compose recreates a container by RENAMING the old
  # one to <hash>_<name>, creating the new one, then removing the old. Interrupted
  # between those steps -- two sessions deploying the same stack -- the rename
  # survives, holds the real name, and the new container can never bind it. That
  # left the backend Created-but-never-started and every API call 502'd, while the
  # front page still served a clean 200. Clear them first; they are already dead.
  ORPHANS=$(docker ps -a --format '{{.Names}}' \
            | grep -E '^[0-9a-f]{12}_alpha-finance-' || true)
  if [ -n "$ORPHANS" ]; then
    echo "!! clearing interrupted-recreate orphans:"; echo "$ORPHANS"
    echo "$ORPHANS" | xargs -r docker rm -f
  fi
  # Pre-flight: two sessions pushing migrations can fork the graph (two leaf nodes);
  # `migrate` then refuses and the backend crash-loops (API down, 2026-08-12). Catch
  # it HERE — build the image, check the graph in it, and ABORT before any slot boots
  # the bad image. A forked graph is then a no-op, not an outage. Build is cached, so
  # deploy-zero-downtime reuses it seconds later.
  echo "--- pre-flight: migration graph (two-leaf) check ---"
  PC="docker compose --env-file /etc/alpha-finance/.env"
  if $PC build backend && $PC run --rm --no-deps -T --entrypoint python backend manage.py check_migrations; then
    echo "migration graph OK — proceeding"
  else
    echo "!! MIGRATION CONFLICT (multiple leaf nodes) or backend build failed —"
    echo "!! NOT deploying. Nothing switched over; the old version is still serving."
    echo "!! Fix: python manage.py makemigrations --merge, commit, push, re-run golive."
    exit 1
  fi
  # Zero-downtime (CFO 2026-08-06). `up -d` recreates the container, so port 8000
  # vanishes for ~20s. Both Caddy upstreams carry lb_try_duration 75s, so requests
  # HANG rather than 502 — to whoever is clicking, the system is simply frozen.
  # That blip is the entire reason deploys were pushed past 18:00.
  #
  # deploy-zero-downtime.sh boots the idle slot, proves it healthy, then moves
  # traffic with a graceful Caddy reload. It is run TWICE on purpose: it alternates
  # slots and STOPS the retired one, so a single run leaves green live and blue
  # stopped — and 15 files in /etc/cron.d plus ops/backup_daily.sh address the
  # container by NAME (`compose exec backend`). Leaving green live silently breaks
  # the nightly backup, the morning brief, bug triage, the FNB sync and staff loans.
  # The second run returns home to blue. Each run is independently zero-downtime.
  if [ -x infra/host/deploy-zero-downtime.sh ] || [ -f infra/host/deploy-zero-downtime.sh ]; then
    bash infra/host/deploy-zero-downtime.sh --frontend
    RC1=$?
    echo "--- out: exit $RC1 ---"
    if [ "$RC1" = 0 ]; then
      bash infra/host/deploy-zero-downtime.sh
      RC2=$?
      echo "--- back home: exit $RC2 ---"
      if [ "$RC2" != 0 ]; then
        echo "!! STILL ON THE OTHER SLOT. The site is UP, but cron, the nightly"
        echo "!! backup and the digests are pointing at a stopped container."
        echo "!! Fix: bash infra/host/deploy-zero-downtime.sh   (returns to :8000)"
      fi
    else
      echo "!! zero-downtime deploy failed — NOTHING was switched over and the"
      echo "!! old version is still serving. Not falling back to a restart."
    fi
  else
    echo "!! deploy-zero-downtime.sh missing — falling back to the restart path"
    # Every step is checked. Without this the script walked straight past a failed
    # build and a failed `up -d` and still reported DONE: on 11-Aug-2026 a frontend
    # build failed, the old image was restarted, and the deploy read as a success.
    C="docker compose --env-file /etc/alpha-finance/.env"
    if ! $C build backend; then
      echo "!! BACKEND BUILD FAILED — nothing restarted, the old version still serves."
      FELL_OVER=1
    elif ! $C build --no-cache frontend; then
      echo "!! FRONTEND BUILD FAILED — nothing restarted, the old version still serves."
      FELL_OVER=1
    elif ! $C up -d backend frontend; then
      echo "!! up -d FAILED — the stack may be part-recreated. Check docker ps -a for"
      echo "!! <hash>_alpha-finance-* orphans, remove them, and re-run up -d."
      FELL_OVER=1
    else
      sleep 25
    fi
  fi
  echo "--- health ---"
  curl -s -o /dev/null -w 'backend  http %{http_code}\n' http://localhost:8000/ 2>/dev/null || true
  curl -s -o /dev/null -w 'frontend http %{http_code}\n' http://localhost:3000/ 2>/dev/null || true

  # The verdict. Two printed HTTP codes and "DONE" is not a health check -- it is a
  # log line nobody reads. ops/verify_deploy.sh already knew how to judge a release
  # (site serving, migrations applied, no sick container, and now the API answering
  # through Caddy); it simply was never called from here.
  if [ -f ops/verify_deploy.sh ]; then
    bash ops/verify_deploy.sh
    VRC=$?
  else
    echo "!! ops/verify_deploy.sh missing — release NOT verified"
    VRC=2
  fi
  case "${FELL_OVER:-0}$VRC" in
    00) echo "=== GOLIVE OK $(date -u) ===" ;;
    *)  echo "=== GOLIVE PROBLEM (build/restart flag=${FELL_OVER:-0}, verify=$VRC) $(date -u) ==="
        echo "!! Do NOT record this release as done. 0=ok 1=broken 2=could not tell." ;;
  esac
} >> "$LOG" 2>&1
rm -f /etc/cron.d/omni-golive
