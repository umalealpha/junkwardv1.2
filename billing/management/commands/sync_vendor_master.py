"""
python manage.py sync_vendor_master /path/to/Alpha_Direct_Vendors_CLEANED.csv [--commit]

CFO directive 2026-07-07 — the attached CSV is the MASTER approved vendor list,
per company entity (11 companies). Sync rules:

  1. Match by (company, name) — case/whitespace-insensitive on name. Companies
     are matched by exact Company.name; a CSV company that doesn't resolve
     aborts the run (nothing is guessed).
  2. UPDATE matched vendors' details to the CSV values (tax_id, email, phone,
     address, registration_number, payment_terms_days, is_resident, wht_exempt,
     is_active, currency_code, graphite_id, external_ref — graphite_id /
     external_ref only where the CSV value is non-empty).
  3. CREATE vendors on the list that don't exist.
  4. DELETE vendors NOT on the list ONLY if they have zero transaction history
     (no PO, no invoice/JV, no payment — discovered via every FK that points at
     Contact). Vendors with history are kept and reported, never deleted.
     Deletion is scoped to the companies present in the CSV.
  5. Never merge across companies — the key is (company, name).

Default is a DRY RUN that prints the full plan; --commit applies it.
"""
from __future__ import annotations

import csv

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from billing.models import Contact
from core.models import Company, Currency

# Field -> CSV column (simple scalar updates)
FIELD_MAP = {
    'registration_number': 'registration_number',
    'tax_id': 'tax_id',
    'email': 'email',
    'phone': 'phone',
    'address': 'address',
}
BOOL_FIELDS = {'is_resident': 'is_resident', 'wht_exempt': 'wht_exempt', 'is_active': 'is_active'}

# FKs that constitute TRANSACTION HISTORY (PO raised / JV passed / money moved).
# Anything else referencing the contact (e.g. vendor bank accounts, KYC docs)
# is master-data appendage: it does not protect a vendor from removal, and is
# deleted together with it.
HISTORY_MODELS = {
    'procurement.PurchaseOrder',
    'billing.Invoice',
    'payments.Payment',
}


def _norm(name: str) -> str:
    return ' '.join((name or '').split()).lower()


class Command(BaseCommand):
    help = 'Sync the master vendor list (per company) from a CSV. Dry-run unless --commit.'

    def add_arguments(self, parser):
        parser.add_argument('csv_path')
        parser.add_argument('--commit', action='store_true', help='Apply the changes.')

    def handle(self, *args, **opts):
        path, commit = opts['csv_path'], opts['commit']

        with open(path, encoding='utf-8-sig', newline='') as f:
            rows = list(csv.DictReader(f))
        if not rows:
            raise CommandError('CSV is empty.')

        # ── resolve companies (exact name match; abort on unknown) ─────────
        csv_companies = sorted({r['company'].strip() for r in rows})
        companies = {}
        for cname in csv_companies:
            try:
                companies[cname] = Company.objects.get(name=cname)
            except Company.DoesNotExist:
                raise CommandError(f'Company not found in omni (exact name): {cname!r}')
        self.stdout.write(f'Companies matched: {len(companies)}')

        currencies = set(Currency.objects.values_list('code', flat=True))

        # ── index the CSV by (company_id, normalised name) ─────────────────
        want = {}
        bad_ccy = set()
        for r in rows:
            co = companies[r['company'].strip()]
            key = (co.id, _norm(r['name']))
            want[key] = (co, r)
            ccy = (r.get('currency_code_id') or 'BWP').strip().upper() or 'BWP'
            if ccy not in currencies:
                bad_ccy.add(ccy)
        if bad_ccy:
            raise CommandError(f'CSV has unknown currency codes: {sorted(bad_ccy)}')

        # ── existing vendors in the scoped companies ────────────────────────
        existing = {}
        for c in (Contact.objects
                  .filter(contact_type=Contact.ContactType.VENDOR,
                          company_id__in=[co.id for co in companies.values()])
                  .select_related('currency_code')):
            existing[(c.company_id, _norm(c.name))] = c

        # ── history-FK discovery (same pattern as dedupe_contacts) ──────────
        rel_fields = [
            f for f in Contact._meta.get_fields()
            if (f.one_to_many or f.one_to_one) and f.auto_created and not f.concrete
        ]

        def refs(contact):
            """{model_label: count} of every row referencing this contact."""
            out = {}
            for f in rel_fields:
                n = f.related_model.objects.filter(**{f.field.name: contact}).count()
                if n:
                    out[f.related_model._meta.label] = n
            return out

        to_create, to_update, unchanged = [], [], 0
        for key, (co, r) in want.items():
            c = existing.get(key)
            if c is None:
                to_create.append((co, r))
                continue
            changes = {}
            for field, col in FIELD_MAP.items():
                new = (r.get(col) or '').strip() or None
                if (getattr(c, field) or None) != new:
                    changes[field] = new
            for field, col in BOOL_FIELDS.items():
                new = (r.get(col) or '').strip().lower() in ('true', '1', 'yes')
                if getattr(c, field) != new:
                    changes[field] = new
            terms = (r.get('payment_terms_days') or '').strip()
            if terms.isdigit() and c.payment_terms_days != int(terms):
                changes['payment_terms_days'] = int(terms)
            ccy = (r.get('currency_code_id') or '').strip().upper()
            if ccy and c.currency_code_id != ccy:
                changes['currency_code_id'] = ccy
            for field in ('graphite_id', 'external_ref'):
                new = (r.get(field) or '').strip()
                if new and (getattr(c, field, '') or '') != new:
                    changes[field] = new
            if changes:
                to_update.append((c, changes))
            else:
                unchanged += 1

        # deletion candidates: existing scoped vendors not on the list
        delete_ok, delete_blocked = [], []
        for key, c in existing.items():
            if key in want:
                continue
            ref = refs(c)
            history = {k: v for k, v in ref.items() if k in HISTORY_MODELS}
            if history:
                delete_blocked.append((c, history))
            else:
                delete_ok.append((c, ref))   # ref = appendages only (may be empty)

        # ── report ───────────────────────────────────────────────────────────
        w = self.stdout.write
        w(f'CSV rows:            {len(rows)}')
        w(f'Existing (scoped):   {len(existing)}')
        w(f'To create:           {len(to_create)}')
        w(f'To update:           {len(to_update)}')
        w(f'Unchanged:           {unchanged}')
        w(f'To delete (clean):   {len(delete_ok)}')
        w(f'Kept (has history):  {len(delete_blocked)}')
        for c, h in delete_blocked[:15]:
            w(f'    KEEP {c.company.code} · {c.name[:40]:40} history={h}')
        for c, ref in delete_ok[:15]:
            w(f'    DEL  {c.company.code} · {c.name[:40]:40} appendages={ref or "none"}')
        for c, ch in to_update[:10]:
            w(f'    UPD  {c.company.code} · {c.name[:40]:40} {list(ch.keys())}')
        for co, r in to_create[:10]:
            w(f'    NEW  {co.code} · {r["name"][:40]}')

        if not commit:
            w(self.style.WARNING('DRY RUN — nothing changed. Re-run with --commit.'))
            return

        # ── apply ────────────────────────────────────────────────────────────
        created = updated = deleted = 0
        with transaction.atomic():
            for c, changes in to_update:
                for k, v in changes.items():
                    setattr(c, k, v)
                c.save(update_fields=list(changes.keys()) + ['updated_at'])
                updated += 1
            for co, r in to_create:
                Contact.objects.create(
                    company=co,
                    contact_type=Contact.ContactType.VENDOR,
                    name=' '.join((r['name'] or '').split()),
                    registration_number=(r.get('registration_number') or '').strip() or None,
                    tax_id=(r.get('tax_id') or '').strip() or None,
                    email=(r.get('email') or '').strip() or None,
                    phone=(r.get('phone') or '').strip() or None,
                    address=(r.get('address') or '').strip() or None,
                    payment_terms_days=int(r['payment_terms_days']) if (r.get('payment_terms_days') or '').strip().isdigit() else 30,
                    is_resident=(r.get('is_resident') or '').strip().lower() in ('true', '1', 'yes'),
                    wht_exempt=(r.get('wht_exempt') or '').strip().lower() in ('true', '1', 'yes'),
                    is_active=(r.get('is_active') or 'true').strip().lower() in ('true', '1', 'yes'),
                    currency_code_id=(r.get('currency_code_id') or 'BWP').strip().upper() or 'BWP',
                    graphite_id=(r.get('graphite_id') or '').strip() or None,
                    external_ref=(r.get('external_ref') or '').strip() or None,
                )
                created += 1
            for c, ref in delete_ok:
                # delete appendages (non-history refs) first, then the vendor
                for f in rel_fields:
                    if f.related_model._meta.label in HISTORY_MODELS:
                        continue
                    f.related_model.objects.filter(**{f.field.name: c}).delete()
                c.delete()
                deleted += 1
        w(self.style.SUCCESS(
            f'COMMITTED: created={created} updated={updated} deleted={deleted} '
            f'kept_with_history={len(delete_blocked)}'))
