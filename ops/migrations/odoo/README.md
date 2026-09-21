# Odoo → Omni ERP one-shot migration

One-time pull of historical data from the legacy Odoo at `odoo.alphadirect.co.bw`
into the alpha-finance Django database.

## What gets migrated

| Odoo model | alpha-finance target | Filter |
|---|---|---|
| `account.account` | `ledger.Account` | `company_id != 4` |
| `res.partner` (supplier_rank > 0) | `billing.Contact` (vendor) | `company_id != 4 OR shared` |
| `res.partner` (customer_rank > 0) | `billing.Contact` (customer) | `company_id != 4 OR shared` |
| `account.move` + `account.move.line` | `ledger.JournalEntry` + `JournalEntryLine` | `state=posted, company_id!=4, date<=2026-03-31` |

**ADIC (Odoo company_id = 4) is never migrated.** That is enforced by both
the Odoo domain filter and a defensive belt-and-braces check inside every
importer.

**No data after 2026-03-31** is migrated. Same belt-and-braces guard.

## Credentials — env vars only

| Variable | Example |
|---|---|
| `ODOO_URL` | `https://odoo.alphadirect.co.bw` |
| `ODOO_DB` | `alphadirect-odoo` |
| `ODOO_USER` | `<integration-account-email>` |
| `ODOO_PASSWORD` | (set in `/etc/alpha-finance/.env` on the EC2; never in code, never in chat) |

The command refuses to run if any of those are missing.

## Local dev

```bash
export ODOO_URL='https://odoo.alphadirect.co.bw'
export ODOO_DB='alphadirect-odoo'
export ODOO_USER='you@alphadirect.co.bw'
export ODOO_PASSWORD='...'   # NEVER commit or paste in chat
python manage.py migrate_odoo --dry-run
```

## Production deploy

1. CFO rotates the Odoo password (the one originally pasted in chat is burned).
2. Add the four vars to `/etc/alpha-finance/.env` on the omni EC2 instance.
3. Rebuild the backend image so it picks up the new env file:
   ```bash
   cd /opt/alpha-finance
   git pull --ff-only origin main
   sudo docker compose --env-file /etc/alpha-finance/.env build backend
   sudo docker compose --env-file /etc/alpha-finance/.env up -d backend
   ```
4. Apply the schema migrations:
   ```bash
   sudo docker exec alpha-finance-backend python manage.py migrate ledger billing
   ```
5. Dry-run first to see the planned counts:
   ```bash
   sudo docker exec alpha-finance-backend python manage.py migrate_odoo --dry-run
   ```
6. Review the per-model summary with the CFO.
7. Commit:
   ```bash
   sudo docker exec alpha-finance-backend python manage.py migrate_odoo --commit
   ```
8. Verify reconciliation:
   ```bash
   sudo docker exec alpha-finance-backend python manage.py shell -c "
   from billing.models import Contact
   from ledger.models import Account, JournalEntry
   print('Imported accounts:',  Account.objects.filter(external_ref__startswith='odoo:').count())
   print('Imported vendors:',   Contact.objects.filter(external_ref__startswith='odoo:res.partner:').filter(contact_type='vendor').count())
   print('Imported customers:', Contact.objects.filter(external_ref__startswith='odoo:res.partner:').filter(contact_type='customer').count())
   print('Imported moves:',     JournalEntry.objects.filter(source_type='odoo_import').count())
   "
   ```

## Idempotency

Every import uses an `external_ref` of the form `odoo:<model>:<id>[:suffix]`.
Re-running the command is a no-op for already-imported rows. The CoA importer
additionally treats existing `Account.code` matches as "wins" — our seeded
accounts (1990, 2150, 2160, 2199 etc.) are never overwritten.

## Resumability

Each run gets a UUID. If interrupted, just re-run the command — the
`external_ref` check skips anything already done.

Per-run summaries land in `ops/migrations/odoo/runs/<run_id>.json`.

## Out of scope

- ADIC migration (Odoo company_id = 4) — never
- Data > 2026-03-31 — never
- Real-time Odoo↔Omni sync — one-shot only
- Attachments / `ir.attachment`
- Payroll history (handled by `setup_employees` / `setup_payroll_components`)
- Custom Odoo addons / non-standard models
