#!/usr/bin/env sh
# Alpha Direct Finance — Django container entrypoint
# - Waits for the database to accept connections
# - Runs migrations
# - (Optionally) seeds initial data on first boot
# - Collects static files for whitenoise to serve
# - Hands off to the configured CMD (gunicorn or runserver)
set -eu

: "${DB_HOST:=db}"
: "${DB_PORT:=5432}"
: "${DJANGO_RUN_MIGRATIONS:=1}"
: "${DJANGO_COLLECTSTATIC:=1}"
: "${DJANGO_SEED_INITIAL:=0}"
: "${DJANGO_CREATE_SUPERUSER:=0}"

echo "[entrypoint] waiting for db at ${DB_HOST}:${DB_PORT} ..."
i=0
until python -c "import socket,sys; s=socket.socket(); s.settimeout(2); s.connect(('${DB_HOST}', ${DB_PORT}))" 2>/dev/null; do
    i=$((i+1))
    if [ "$i" -gt 60 ]; then
        echo "[entrypoint] database did not become reachable in 60s — aborting"
        exit 1
    fi
    sleep 1
done
echo "[entrypoint] db is reachable"

if [ "${DJANGO_RUN_MIGRATIONS}" = "1" ]; then
    echo "[entrypoint] running migrations"
    python manage.py migrate --noinput
fi

# Claims-PO approver titles self-heal on EVERY deploy (CFO directive 2026-07-11 —
# "lock this instruction re PO approvals"). Idempotent + name-guarded; only pins
# the claims seniors so the approver set can't silently drift back to the FM.
echo "[entrypoint] pinning claims-PO approvers"
python manage.py seed_claims_approvers --commit || echo "[entrypoint] seed_claims_approvers skipped/failed"

# Named individuals' cross-entity access self-heals on EVERY deploy too (CFO
# directive 2026-07-14 — Lemogang must always have ADIC access for Claims PO).
# Additive-only; never touches anyone not explicitly listed in the roster.
echo "[entrypoint] pinning named cross-entity access"
python manage.py seed_pinned_company_access --commit || echo "[entrypoint] seed_pinned_company_access skipped/failed"

# Transformation Board — the four-month path (CFO 2026-09-20). Idempotent:
# refreshes wording, money and dates, never resets anyone's progress. Without
# this the board deploys green and opens completely empty.
python manage.py transformation_seed || echo "[entrypoint] transformation_seed skipped/failed"

if [ "${DJANGO_COLLECTSTATIC}" = "1" ]; then
    echo "[entrypoint] collecting static files"
    python manage.py collectstatic --noinput --clear > /dev/null
fi

if [ "${DJANGO_SEED_INITIAL}" = "1" ]; then
    echo "[entrypoint] seeding initial data (idempotent)"
    python manage.py setup_initial_data       || echo "[entrypoint] setup_initial_data skipped/failed"
    python manage.py setup_chart_of_accounts  || echo "[entrypoint] setup_chart_of_accounts skipped/failed"
    python manage.py seed_frozen_figures      || echo "[entrypoint] seed_frozen_figures skipped/failed"
fi

if [ "${DJANGO_CREATE_SUPERUSER}" = "1" ] \
   && [ -n "${DJANGO_SUPERUSER_USERNAME:-}" ] \
   && [ -n "${DJANGO_SUPERUSER_EMAIL:-}" ] \
   && [ -n "${DJANGO_SUPERUSER_PASSWORD:-}" ]; then
    echo "[entrypoint] ensuring superuser ${DJANGO_SUPERUSER_USERNAME} exists"
    python manage.py createsuperuser --noinput || true
fi

echo "[entrypoint] starting: $*"
exec "$@"
