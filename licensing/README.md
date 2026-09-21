# licensing — M365 active-user tracking

Refreshes a list of real human users whose last *interactive* M365 sign-in
is within the last 6 months. Stores them in `licensing.M365ActiveUser`,
shows them under **Settings → M365 Active Users**, and emails the list to
HR + Unami every Friday morning.

## Why

CFO directive 2026-05-28 — Pako/Kago/HR need a single, weekly-fresh view
of who is actually using their Microsoft 365 licence so dormant licences
can be reclaimed quickly.

## Wiring

| Layer | Where |
|---|---|
| Django app | `licensing/` (added to `LOCAL_APPS` in `alpha_finance/settings.py`) |
| Models | `M365ActiveUser`, `M365LicenseSyncRun` |
| Graph client | `licensing/services/graph.py` (MSAL client-credentials) |
| Cron command | `python manage.py sync_m365_active_users` |
| Settings page | `frontend/src/app/(dashboard)/settings/m365-active-users/page.tsx` |
| Sidebar | `Sidebar.tsx` under "Configuration" → **M365 Active Users** |
| Schedule | `infra/cron/m365-license-sync.cron` — Friday 06:00 SAST |
| Notify | `M365_NOTIFY_EMAILS` env (default `hr@alphadirect.co.bw, ubutale@alphadirect.co.bw`) |

## Required environment (in `/etc/alpha-finance/.env`)

```
M365_TENANT_ID=fb4aec07-793a-494c-92f6-7a527a7f89c0
M365_CLIENT_ID=c8169386-c4c6-4dd6-8eb4-f1aaf2912e52
M365_CLIENT_SECRET=<paste — DO NOT COMMIT>
M365_ACTIVE_CUTOFF_MONTHS=6
M365_NOTIFY_EMAILS=hr@alphadirect.co.bw,ubutale@alphadirect.co.bw
```

The Entra app registration `omni-m365-license-sync` was created in tenant
`fb4aec07-…`. The application permissions it requests are:

- `User.Read.All` — list licensed users
- `AuditLog.Read.All` — read `signInActivity`

**Admin consent is required once.** A Global Administrator opens this URL
and clicks Accept:

    https://login.microsoftonline.com/fb4aec07-793a-494c-92f6-7a527a7f89c0/adminconsent?client_id=c8169386-c4c6-4dd6-8eb4-f1aaf2912e52

Without consent the cron will run and fail with a Graph 403; the
`M365LicenseSyncRun` row records the error for visibility on the
Settings page.

## Filters applied to the Graph result

1. `accountEnabled eq true and userType eq 'Member'` server-side.
2. Has at least one assigned licence (client-side).
3. Not a shared/forward mailbox (heuristic in `services/graph.py`):
   - displayName is ALL-CAPS, **or**
   - displayName is a single word ("Talent", "Staff", "Conditions"), **or**
   - UPN local part matches a function-keyword regex (claim, health,
     helpdesk, hr, accounts, info, support, …).
4. `signInActivity.lastSignInDateTime` is on/after the cutoff
   (`M365_ACTIVE_CUTOFF_MONTHS` ago).

## Running manually

```bash
# Inside the backend container on prod:
python manage.py sync_m365_active_users               # full run + email
python manage.py sync_m365_active_users --no-email    # write DB, skip email
python manage.py sync_m365_active_users --dry-run     # show counts, no writes
```

## Installing the cron entry on the omni EC2

```bash
# On the EC2 (af-south-1):
sudo cp /opt/alpha-finance/infra/cron/m365-license-sync.cron /etc/cron.d/m365-license-sync
sudo chmod 644 /etc/cron.d/m365-license-sync
sudo touch /var/log/m365-license-sync.log
sudo chown root:root /var/log/m365-license-sync.log
# Verify next run:
sudo systemctl status cron
```

## Roll-back

```bash
sudo rm /etc/cron.d/m365-license-sync
# Then either remove 'licensing' from LOCAL_APPS or just stop running the cron;
# the table stays put — read-only on the Settings page.
```
