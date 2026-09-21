"""
Backfill BLANK vendor addresses on billing.Contact from Odoo res.partner.

Kao (Claims) flagged during PO testing that some suppliers have no address
(e.g. Mancon), so the printed PO has a blank supplier block. The addresses
already exist in Odoo — omni just never captured them for vendors that came
in via the xlsx master list or that were pulled/created before the address
logic existed. This pulls street/street2/city straight from Odoo and fills
ONLY the blanks. It never overwrites an address omni already has (the CSV
master list stays authoritative where it carries a value).

  python manage.py backfill_vendor_addresses_from_odoo                 # dry run, all companies
  python manage.py backfill_vendor_addresses_from_odoo --commit         # apply
  python manage.py backfill_vendor_addresses_from_odoo --company ADIC   # scope to one entity
  python manage.py backfill_vendor_addresses_from_odoo --contains mancon --commit

Matching (safe-by-default — never guesses):
  1. external_ref 'odoo:res.partner:<id>:...'  -> exact Odoo id (zero ambiguity).
  2. else exact NORMALISED name (' '.join(split).lower()) against the Odoo
     supplier universe (supplier_rank > 0), then a bounded name-ilike fallback
     that also reaches shared / rank-0 partners (Mancon Alarms is rank 0).
  3. If a name resolves to Odoo records with MORE THAN ONE distinct address,
     the entity's Odoo company is used to disambiguate; if still ambiguous the
     row is SKIPPED and reported — we do not pick an address at random.
  4. A name that matches Odoo but where Odoo itself has no street is reported
     as 'odoo_no_street' and left blank.
"""
from __future__ import annotations

from collections import defaultdict

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q

from billing.models import Contact
from core.models import Company
from ops.migrations.odoo.client import OdooClient
from ops.migrations.odoo.runner import DEFAULT_COMPANY_MAP_BY_NAME

PARTNER_FIELDS = ['id', 'name', 'supplier_rank', 'street', 'street2', 'city', 'company_id']
FALLBACK_BATCH = 20            # names OR-ed into one Odoo ilike call
FALLBACK_NAME_CAP = 1500       # unique names to attempt in the fallback pass


def _norm(name: str) -> str:
    """Whitespace-collapsed, lower-cased — same key sync_vendor_master uses."""
    return ' '.join((name or '').split()).lower()


def _clean(val) -> str:
    """Odoo returns False for empty char fields and embeds newlines in street."""
    if not val:
        return ''
    return ' '.join(str(val).replace('\n', ' ').split())


def _build_address(rec: dict) -> str:
    parts = [_clean(rec.get('street')), _clean(rec.get('street2')), _clean(rec.get('city'))]
    return ', '.join(p for p in parts if p)


def _rec_company_id(rec: dict):
    cid = rec.get('company_id')
    if isinstance(cid, (list, tuple)) and cid:
        return int(cid[0])
    return None


def _or_ilike(names):
    """Odoo polish-notation OR of name-ilike terms."""
    terms = [('name', 'ilike', n) for n in names]
    return ['|'] * (len(terms) - 1) + terms if len(terms) > 1 else terms


class Command(BaseCommand):
    help = 'Fill blank vendor addresses on billing.Contact from Odoo (blanks only, never overwrite).'

    def add_arguments(self, parser):
        parser.add_argument('--commit', action='store_true', help='Actually write addresses.')
        parser.add_argument('--company', help='Scope to one alpha-finance Company.code (e.g. ADIC).')
        parser.add_argument('--contains', help='Only vendors whose name contains this (case-insensitive).')
        parser.add_argument('--limit', type=int, help='Cap the number of contacts scanned (debugging).')

    def handle(self, *args, **opts):
        commit = opts['commit']

        # ── target set: blank-address vendors ─────────────────────────────
        blankq = Q(address__isnull=True) | Q(address='')
        qs = (Contact.objects
              .filter(contact_type=Contact.ContactType.VENDOR)
              .filter(blankq)
              .select_related('company'))
        if opts['company']:
            qs = qs.filter(company__code__iexact=opts['company'])
        if opts['contains']:
            qs = qs.filter(name__icontains=opts['contains'])
        qs = qs.order_by('name')
        if opts['limit']:
            qs = qs[:opts['limit']]
        contacts = list(qs)
        if not contacts:
            self.stdout.write(self.style.WARNING('No blank-address vendors matched the filter.'))
            return
        self.stdout.write(f'Blank-address vendors to resolve: {len(contacts)}')

        # ── Odoo: authenticate + pull the supplier universe once ──────────
        try:
            client = OdooClient()
            client.authenticate()
        except Exception as e:  # noqa: BLE001
            raise CommandError(f'Odoo authentication failed: {e}')

        code_to_odoo = self._company_map(client)

        by_id: dict[int, dict] = {}
        by_norm: dict[str, list[dict]] = defaultdict(list)

        def _index(rec):
            by_id[int(rec['id'])] = rec
            by_norm[_norm(rec.get('name'))].append(rec)

        universe = 0
        for rec in client.search_read('res.partner', [('supplier_rank', '>', 0)], PARTNER_FIELDS):
            _index(rec)
            universe += 1
        self.stdout.write(f'Odoo supplier universe indexed: {universe}')

        # ── resolve, tracking why each row lands where it does ────────────
        stats = defaultdict(int)
        planned: list[tuple[Contact, str]] = []
        unresolved_names: dict[str, str] = {}   # normname -> a raw name for ilike
        pending_name: list[Contact] = []        # contacts awaiting fallback

        for c in contacts:
            stats['scanned'] += 1
            odoo_id = self._extref_odoo_id(c.external_ref)
            if odoo_id is not None and odoo_id in by_id:
                addr = _build_address(by_id[odoo_id])
                if addr:
                    planned.append((c, addr))
                    stats['matched_by_id'] += 1
                else:
                    stats['odoo_no_street'] += 1
                continue

            recs = by_norm.get(_norm(c.name))
            if recs:
                addr = self._resolve(c, recs, code_to_odoo, stats)
                if addr:
                    planned.append((c, addr))
                    stats['matched_by_name'] += 1
                continue

            # No hit in the rank>0 universe — defer to the fallback ilike pass.
            pending_name.append(c)
            unresolved_names.setdefault(_norm(c.name), c.name)

        # ── bounded fallback: reach shared / rank-0 partners by name ──────
        if pending_name:
            wanted = list(unresolved_names.items())[:FALLBACK_NAME_CAP]
            dropped = len(unresolved_names) - len(wanted)
            self.stdout.write(
                f'Fallback name-lookup for {len(wanted)} unique names'
                + (f' ({dropped} over cap, not attempted)' if dropped else ''))
            raw_names = [raw for _, raw in wanted]
            for i in range(0, len(raw_names), FALLBACK_BATCH):
                batch = raw_names[i:i + FALLBACK_BATCH]
                for rec in client.search_read('res.partner', _or_ilike(batch), PARTNER_FIELDS):
                    nk = _norm(rec.get('name'))
                    if nk in unresolved_names:
                        by_norm[nk].append(rec)

            for c in pending_name:
                recs = by_norm.get(_norm(c.name))
                if not recs:
                    stats['no_odoo_match'] += 1
                    continue
                addr = self._resolve(c, recs, code_to_odoo, stats)
                if addr:
                    planned.append((c, addr))
                    stats['matched_by_fallback'] += 1

        # ── report ─────────────────────────────────────────────────────────
        w = self.stdout.write
        w('')
        w(self.style.MIGRATE_HEADING('RESOLUTION SUMMARY' + ('' if commit else ' (DRY RUN)')))
        for k in ('scanned', 'matched_by_id', 'matched_by_name', 'matched_by_fallback',
                  'ambiguous', 'odoo_no_street', 'no_odoo_match'):
            w(f'  {k:<22} {stats[k]}')
        w(f'  {"WILL UPDATE":<22} {len(planned)}')
        w('  --- sample (first 12) ---')
        for c, addr in planned[:12]:
            w(f'    {(c.company.code if c.company else "?"):<6} {c.name[:34]:34} -> {addr[:60]}')

        if not commit:
            w(self.style.WARNING('DRY RUN — nothing written. Re-run with --commit.'))
            return

        # ── apply (blanks only; bulk in a transaction) ────────────────────
        updated = 0
        with transaction.atomic():
            CHUNK = 500
            for i in range(0, len(planned), CHUNK):
                chunk = planned[i:i + CHUNK]
                for c, addr in chunk:
                    c.address = addr
                Contact.objects.bulk_update([c for c, _ in chunk], ['address'])
                updated += len(chunk)
        w(self.style.SUCCESS(f'COMMITTED: {updated} vendor addresses filled from Odoo.'))

    # ------------------------------------------------------------------
    def _company_map(self, client) -> dict[str, int]:
        """alpha-finance Company.code (upper) -> Odoo res.company id."""
        out: dict[str, int] = {}
        for rec in client.search_read('res.company', [], ['id', 'name']):
            code = DEFAULT_COMPANY_MAP_BY_NAME.get((rec.get('name') or '').strip().lower())
            if code:
                out[code.upper()] = int(rec['id'])
        return out

    @staticmethod
    def _extref_odoo_id(external_ref: str):
        if not external_ref or not external_ref.startswith('odoo:res.partner:'):
            return None
        try:
            return int(external_ref.split(':')[2])
        except (IndexError, ValueError):
            return None

    def _resolve(self, contact, recs, code_to_odoo, stats):
        """Pick one address from name-matched Odoo records, or skip if ambiguous."""
        addrs = {_build_address(r) for r in recs}
        addrs.discard('')
        if not addrs:
            stats['odoo_no_street'] += 1
            return None
        if len(addrs) == 1:
            return next(iter(addrs))

        # More than one distinct address for this name — disambiguate by the
        # entity's Odoo company (or shared company_id=False), never at random.
        want = code_to_odoo.get(contact.company.code.upper()) if contact.company else None
        scoped = {
            _build_address(r) for r in recs
            if _rec_company_id(r) in (want, None) and _build_address(r)
        }
        if len(scoped) == 1:
            return next(iter(scoped))
        stats['ambiguous'] += 1
        return None
