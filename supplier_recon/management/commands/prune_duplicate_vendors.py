"""Delete duplicate vendor rows that carry NO transactions.

CFO directive 2026-07-25: "duplicate no transactions please delete." A duplicate
splits one supplier's position across two lines on the reconciliation board.

SAFETY — this command deletes live master data, so it is deliberately narrow:

* A row is only a DUPLICATE if another vendor in the **same legal entity** has
  the same normalised name (case, punctuation, spacing and common suffixes like
  "(Pty) Ltd" ignored). Vendors legitimately exist once per entity — CFO
  directive 2026-05-18 — so the same name under ADSA and ADIC is NOT a
  duplicate and is never touched.
* A row is only deleted when EVERY transaction count is zero: invoices,
  purchase orders, payments, journal lines, GRNs, bank-rec rules, KYC,
  vendor bank accounts, subrogations, salvage purchases, and any
  reconciliation profile/line. If a single one is non-zero the row stays.
* The survivor is the row with the most transactions; ties keep the oldest.
* ``--dry-run`` is the default posture in review: run it first, read the list,
  then run for real.

    python manage.py prune_duplicate_vendors --dry-run
    python manage.py prune_duplicate_vendors --company ADIC
    python manage.py prune_duplicate_vendors --apply
"""

import re

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import ProtectedError

from billing.models import Contact, Invoice
from core.models import Company
from payments.models import Payment
from procurement.models import GoodsReceiptNote, PurchaseOrder

SUFFIXES = [
    'proprietary limited', 'pty ltd', 'pty limited', '(pty) ltd',
    'pty', 'ltd', 'limited', 'inc', 'cc', 't/a', 'ta',
]


def normalise(name: str) -> str:
    """Fold case, punctuation, spacing and company suffixes."""
    s = (name or '').lower()
    s = re.sub(r'[^a-z0-9 ]+', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    for suf in sorted(SUFFIXES, key=len, reverse=True):
        cleaned = re.sub(r'[^a-z0-9 ]+', ' ', suf)
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        if cleaned and s.endswith(' ' + cleaned):
            s = s[: -len(cleaned) - 1].strip()
    return s


def transaction_counts(contact) -> dict:
    """Every way a vendor row can be referenced. All must be zero to delete."""
    counts = {
        'invoices': Invoice.objects.filter(contact=contact).count(),
        'purchase_orders': PurchaseOrder.objects.filter(supplier=contact).count(),
        'payments': Payment.objects.filter(contact=contact).count(),
        'grns': GoodsReceiptNote.objects.filter(
            purchase_order__supplier=contact).count(),
    }
    # Anything else pointing at this contact — walk the reverse relations so a
    # new FK added later cannot silently make a deletion unsafe.
    for rel in contact._meta.related_objects:
        accessor = rel.get_accessor_name()
        if accessor in ('invoices', 'payments', 'purchase_orders'):
            continue
        try:
            manager = getattr(contact, accessor, None)
        except Exception:  # noqa: BLE001
            counts[accessor] = -1
            continue
        if manager is None:
            continue
        try:
            counts[accessor] = manager.count()
        except (AttributeError, TypeError):
            # one-to-one: presence is the count
            counts[accessor] = 1
        except Exception:  # noqa: BLE001
            counts[accessor] = -1
    return counts


def total(counts: dict) -> int:
    """-1 anywhere means "could not verify" and must block deletion."""
    if any(v < 0 for v in counts.values()):
        return 10 ** 9
    return sum(counts.values())


class Command(BaseCommand):
    help = 'Delete same-entity duplicate vendor rows that have no transactions.'

    def add_arguments(self, parser):
        parser.add_argument('--company', default=None,
                            help='Company code. Defaults to every entity.')
        parser.add_argument('--apply', action='store_true',
                            help='Actually delete. Without this it is a dry run.')

    def handle(self, *args, **options):
        apply = options['apply']
        code = options['company']

        vendors = Contact.objects.filter(
            contact_type__in=[Contact.ContactType.VENDOR,
                              Contact.ContactType.BROKER,
                              Contact.ContactType.REINSURER],
        ).select_related('company')
        if code:
            company = Company.objects.filter(code__iexact=code).first()
            if company is None:
                self.stderr.write(self.style.ERROR(f"No company '{code}'."))
                return
            vendors = vendors.filter(company=company)

        # Group by (entity, normalised name). Entity is part of the key: the
        # same name in two entities is two legitimate vendors.
        groups: dict[tuple, list] = {}
        for v in vendors:
            groups.setdefault((v.company_id, normalise(v.name)), []).append(v)

        deleted = kept_busy = 0
        for (company_id, key), rows in sorted(groups.items(), key=lambda kv: str(kv[0])):
            if len(rows) < 2 or not key:
                continue

            scored = sorted(
                ((total(transaction_counts(r)), r.created_at, r) for r in rows),
                key=lambda x: (-x[0], x[1]),
            )
            survivor = scored[0][2]
            entity = survivor.company.code if survivor.company_id else 'NO-ENTITY'
            self.stdout.write(
                f"\n{entity}  '{key}'  {len(rows)} rows -> keeping "
                f"\"{survivor.name}\" ({scored[0][0]} txns)")

            for count, _created, row in scored[1:]:
                if count > 0:
                    kept_busy += 1
                    self.stdout.write(self.style.WARNING(
                        f"    KEEP   \"{row.name}\" - has {count} transaction(s), "
                        f"not safe to delete; merge manually"))
                    continue
                if not apply:
                    self.stdout.write(
                        f"    would delete \"{row.name}\" (0 transactions)")
                    deleted += 1
                    continue
                try:
                    with transaction.atomic():
                        row.delete()
                except ProtectedError as exc:
                    kept_busy += 1
                    self.stderr.write(self.style.WARNING(
                        f"    KEEP   \"{row.name}\" - protected reference: {exc}"))
                    continue
                deleted += 1
                self.stdout.write(self.style.SUCCESS(
                    f"    DELETED \"{row.name}\" (0 transactions)"))

        self.stdout.write('')
        verb = 'Would delete' if not apply else 'Deleted'
        self.stdout.write(self.style.SUCCESS(
            f"{verb} {deleted} empty duplicate row(s). "
            f"Kept {kept_busy} duplicate(s) that carry transactions - those need "
            f"a manual merge, never a delete."))
        if not apply:
            self.stdout.write(self.style.WARNING(
                'Dry run - nothing deleted. Re-run with --apply.'))
