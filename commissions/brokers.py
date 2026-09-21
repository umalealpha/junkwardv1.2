"""commissions/brokers.py — the Broker Commission register.

Replaces the hand-kept "Broker Commission - <Month>.xlsm" (28 broker tabs, a
manually downloaded RealPay report VLOOKUP'd into each one). Rose Mokgware
raised it 2026-09-08; the CFO put it under Commissions.

Three rules from the CFO, encoded here rather than left to a reader:
  * Brokers write COMMERCIAL and DOMESTIC business only, never Instant.
  * The broker link is ``policies.agency_id`` — see realpay.graphite_feed.
  * The commission MATHS is Finance's, not ours: this module records and
    reconciles, it does not invent a rate. Nothing here computes commission
    from a premium.
"""
from __future__ import annotations

import logging
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.db import transaction
from django.db.models.functions import Upper

from commissions.models import Broker, BrokerAlias, BrokerPolicy
from realpay import graphite_feed

log = logging.getLogger(__name__)

#: Channels that carry an agency row but are not intermediaries we pay.
#: Same list aware/modes/broker_analysis.py uses, kept in step deliberately.
_DIRECT_MARKERS = ('alpha direct insurance co', 'unicoin')

#: Row cap on one broker's client list. Biggest real broker is ~500.
_POLICY_CAP = 5000


def is_direct_channel(agency_name: str) -> bool:
    n = (agency_name or '').lower()
    return any(m in n for m in _DIRECT_MARKERS)


def _flatten(name: str) -> str:
    """Letters and digits only, lower-cased — for matching a human-typed tab
    name against a broker, where being loose is safe (see import_workbook)."""
    return re.sub(r'[^a-z0-9]+', '', (name or '').lower())


#: Generic industry words. Stripping them folds a broker's variants together —
#: and, if done unconditionally, also folds two DIFFERENT firms that happen to
#: share a first word ("Botswana Insurance Brokers" / "Botswana Risk Services").
_NOUNS = (r'\b(insurance|brokers?|broking|services?|agency|agencies|consultancy|'
          r'risk|managers?|holdings?|enterprises?|investments?|solutions?)\b')
_BRANCH = r'\s*[-–]\s*(gaborone|francistown|palapye|maun|kasane|lobatse)(\s+branch)?\s*$'


def _strip_common(n: str) -> str:
    n = re.sub(r'\((pty|proprietary)\)', ' ', n)
    return re.sub(r'\b(pty|proprietary|limited|ltd|inc|co|company)\b', ' ', n)


def has_variant_marker(agency_name: str) -> bool:
    """Does this name carry evidence it is a VARIANT of another row — a
    trading-as marker or a branch suffix? Only then is it safe to fold on the
    loose key."""
    n = (agency_name or '').lower().strip()
    return bool(re.search(_BRANCH, n) or re.search(r'\bt/?\s*a\b\s*\S', n))


def merge_key(agency_name: str) -> str:
    """The STRICT key: legal form and branch removed, industry nouns KEPT.

    Two rows with the same strict key are the same broker beyond doubt —
    "Finsef (Pty) Ltd" and "FinSef (Pty) Ltd-Francistown" both give 'finsef'.
    """
    n = (agency_name or '').lower().strip()
    n = re.sub(_BRANCH, ' ', n)
    m = re.search(r'\bt/?\s*a\b(.+)$', n)      # "… T/a Redhill Risk" -> "redhill risk"
    if m and m.group(1).strip():
        n = m.group(1)
    n = _strip_common(n)
    return re.sub(r'[^a-z0-9]+', '', n)


def loose_key(agency_name: str) -> str:
    """The LOOSE key: strict, then the industry nouns dropped too.

    Folding on this alone would merge two different firms, so it is only ever
    used to join a group where at least one member carries a variant marker —
    which is how "Dynamic Insurance Brokers (Pty) Ltd" joins its own Gaborone
    and Palapye branches.
    """
    n = (agency_name or '').lower().strip()
    n = re.sub(_BRANCH, ' ', n)
    m = re.search(r'\bt/?\s*a\b(.+)$', n)
    if m and m.group(1).strip():
        n = m.group(1)
    n = _strip_common(n)
    n = re.sub(_NOUNS, ' ', n)
    return re.sub(r'[^a-z0-9]+', '', n)


def group_agencies(names):
    """{name: group_id} — which Graphite agency rows are one broker.

    Strict key first. Then a loose-key group is joined ONLY when at least one of
    its members carries a trading-as marker or a branch suffix, i.e. when the
    data itself says "this is a variant of something". Without that guard,
    dropping the industry nouns silently pays two firms as one.
    """
    strict: dict[str, list[str]] = {}
    for n in names:
        k = merge_key(n)
        if k:
            strict.setdefault(k, []).append(n)

    parent = {k: k for k in strict}

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    by_loose: dict[str, list[str]] = {}
    for k, members in strict.items():
        lk = loose_key(members[0])
        if lk:
            by_loose.setdefault(lk, []).append(k)
    for lk, keys in by_loose.items():
        if len(keys) < 2:
            continue
        # only fold when the data says one of them is a variant
        if not any(has_variant_marker(n) for k in keys for n in strict[k]):
            continue
        root = find(keys[0])
        for k in keys[1:]:
            parent[find(k)] = root

    return {n: find(merge_key(n)) for n in names if merge_key(n)}


def display_name(agency_name: str) -> str:
    """A short label Finance recognises from the workbook tab.

    Prefers the trading name for the same reason merge_key does: the tab says
    "Redhill", not "Hilrange Enterprises (Pty) Ltd".
    """
    # Same branch pattern the keys use — including a trailing "Branch", or the
    # merged broker ends up NAMED after one of its own branches ("Dynamic
    # Insurance Brokers - Gaborone Branch" for a firm with three of them).
    n = re.sub(_BRANCH, '', (agency_name or '').strip(), flags=re.I)
    m = re.search(r'\bt/?\s*a\b(.+)$', n, flags=re.I)
    if m and m.group(1).strip():
        n = m.group(1)
    n = re.sub(r'\s*\((pty|proprietary)\)\s*', ' ', n, flags=re.I)
    n = re.sub(r'\s*\b(ltd|limited)\b\.?', '', n, flags=re.I)
    return ' '.join(n.split()).strip(' .,-') or (agency_name or '').strip()


@transaction.atomic
def sync_brokers_from_graphite() -> dict:
    """Build/refresh the broker register from Graphite's agencies.

    Idempotent: re-running adds new brokers and new aliases and touches nothing
    else. Never deletes a Broker — one with no business this month still has
    history and bank details hanging off it.
    """
    book = graphite_feed.broker_book()
    if not book.get('configured'):
        return {'configured': False, 'created': 0, 'aliases': 0, 'skipped_direct': 0}

    rows = [r for r in book['rows'] if r['agency'] and not is_direct_channel(r['agency'])]
    skipped = len(book['rows']) - len(rows)
    groups = group_agencies([r['agency'] for r in rows])

    # existing aliases anchor a group to the broker that already owns it
    owner: dict[str, Broker] = {}
    for b in Broker.objects.prefetch_related('aliases'):
        for al in b.aliases.all():
            g = groups.get(al.graphite_agency_name)
            if g:
                owner.setdefault(g, b)

    created = aliases = 0
    for row in rows:
        agency = row['agency']
        g = groups.get(agency)
        if not g:
            skipped += 1
            continue
        broker = owner.get(g)
        if broker is None:
            name = display_name(agency)
            existing = Broker.objects.filter(name__iexact=name).first()
            broker = existing or Broker.objects.create(name=name, short_name=name[:40])
            if existing is None:
                created += 1
            owner[g] = broker
        _, made = BrokerAlias.objects.get_or_create(
            graphite_agency_name=agency,
            defaults={'broker': broker, 'graphite_agency_id': row.get('agency_id', '')})
        aliases += 1 if made else 0

    return {'configured': True, 'created': created, 'aliases': aliases,
            'skipped_direct': skipped}


def possible_duplicates() -> list[dict]:
    """Brokers that are probably the same firm, for a HUMAN to confirm.

    Automatic merging takes us as far as trading names and branches. It cannot
    safely close the last gap: "Minet Botswana PTY LTD" and "Minet- Francistown"
    are one broker, but merging every key that is a prefix of another would also
    fuse genuinely different firms. So the near-misses are surfaced and someone
    who knows the book presses the button — an over-merge silently pays two
    brokers as one, which is worse than an extra row on a screen.
    """
    keyed = []
    for b in Broker.objects.prefetch_related('aliases'):
        k = merge_key(b.name) or merge_key(b.short_name)
        if k:
            keyed.append((k, b))
    out, seen = [], set()
    for i, (ka, a) in enumerate(keyed):
        for kb, b in keyed[i + 1:]:
            if ka == kb or not (ka.startswith(kb) or kb.startswith(ka)):
                continue
            if min(len(ka), len(kb)) < 4:      # 'boc' vs 'bocx' is not evidence
                continue
            pair = tuple(sorted([str(a.id), str(b.id)]))
            if pair in seen:
                continue
            seen.add(pair)
            out.append({'a': {'id': str(a.id), 'name': a.name},
                        'b': {'id': str(b.id), 'name': b.name},
                        'shared_key': ka if len(ka) < len(kb) else kb})
    return out


class PolicyHeldElsewhere(Exception):
    """A policy is already on the register for a different broker."""

    def __init__(self, policy_number: str, broker: Broker):
        self.policy_number, self.broker = policy_number, broker
        super().__init__(f'Policy {policy_number} is already on the register for '
                         f'{broker.name}. A policy belongs to one broker — move it '
                         f'there first if this is wrong.')


@transaction.atomic
def absorb(keeper: Broker, other: Broker) -> dict:
    """Fold `other` into `keeper`: its Graphite names and added rows move across.

    Never merges a broker into itself, and never silently drops a policy row —
    a row whose policy number the keeper already has is left on the keeper.
    """
    if keeper.pk == other.pk:
        raise ValueError('That is the same broker.')
    # C3b — never move a policy that a THIRD broker already holds: the fold
    # would carry a double-commissioned policy onto the keeper unnoticed.
    numbers = {pn.upper() for pn in other.policies.values_list('policy_number', flat=True)}
    clash = (BrokerPolicy.objects.annotate(pn_upper=Upper('policy_number'))
             .filter(pn_upper__in=numbers).exclude(broker__in=[keeper, other])
             .select_related('broker').first()) if numbers else None
    if clash is not None:
        raise PolicyHeldElsewhere(clash.policy_number, clash.broker)
    moved_aliases = other.aliases.update(broker=keeper)
    moved_rows = dropped = 0
    held = {(pn.upper(), per) for pn, per in
            keeper.policies.values_list('policy_number', 'period_label')}
    for p in list(other.policies.all()):
        if (p.policy_number.upper(), p.period_label) in held:
            p.delete()          # the keeper already holds this exact row
            dropped += 1
            continue
        p.broker = keeper
        p.save(update_fields=['broker'])
        moved_rows += 1
    name = other.name
    other.delete()
    return {'kept': keeper.name, 'absorbed': name,
            'aliases_moved': moved_aliases, 'rows_moved': moved_rows,
            'rows_dropped_duplicate': dropped}


def broker_clients(broker: Broker, active_only: bool = False,
                   start=None, end=None) -> dict:
    """Every client on this broker's sheet, with the LIVE collection status.

    Two sources, clearly labelled so nobody has to guess where a row came from:
      * ``graphite`` — the live book under this broker's agency names;
      * ``manual`` / ``upload`` — rows a person added on top.

    The status overlay is read live from RealPay. When the bridge is down the
    rows still come back with status ``unknown`` and ``status_live: False`` —
    never silently as "nothing collected".
    """
    names = list(broker.aliases.values_list('graphite_agency_name', flat=True))
    gp = graphite_feed.broker_policies(names, active_only=active_only,
                                       limit=_POLICY_CAP)
    live_ok = gp is not None
    rows: dict[str, dict] = {}

    for r in (gp or []):
        pn = graphite_feed.policy_number_of(r.get('policy_number'))
        if not pn:
            continue
        rows[pn] = {
            'policy_number': pn,
            'insured_name': (r.get('insured_name') or '').strip(),
            'premium': float(r.get('premium') or 0),
            'annual_premium': float(r.get('annual_premium') or 0),
            'policy_active': str(r.get('policy_status') or '') == '1',
            'term_start_date': str(r.get('term_start_date') or '')[:10],
            'source': 'graphite',
        }

    for p in broker.policies.all():
        pn = graphite_feed.policy_number_of(p.policy_number)
        base = rows.get(pn) or {'policy_number': pn, 'insured_name': '', 'premium': 0.0,
                                'annual_premium': 0.0, 'policy_active': None,
                                'term_start_date': '', 'source': p.source}
        base.update({
            'id': str(p.id),
            'source': p.source if base.get('source') == p.source else f'{base["source"]}+{p.source}',
            'insured_name': p.insured_name or base.get('insured_name', ''),
            'period_label': p.period_label,
            'motor_premium': float(p.motor_premium),
            'motor_commission': float(p.motor_commission),
            'non_motor_premium': float(p.non_motor_premium),
            'non_motor_commission': float(p.non_motor_commission),
            'commission_payable': float(p.commission_payable),
            'vat': float(p.vat),
            'amount_received': float(p.amount_received),
            'graphite_verified': p.graphite_verified,
        })
        if p.premium:
            base['premium'] = float(p.premium)
        rows[pn] = base

    keys = list(rows.keys())
    statuses = graphite_feed.policy_statuses(keys, start=start, end=end) if keys else {}
    collected = graphite_feed.collected_in_window(keys, start=start, end=end) if keys else {}
    status_live = bool(statuses) or live_ok
    for pn, row in rows.items():
        st = statuses.get(pn)
        # Money collected is summed over the window, not taken off the last
        # attempt — a policy can be debited twice in a month.
        row['collected_amount'] = collected.get(pn, 0.0)
        if st:
            row.update({'status': st['status'], 'status_label': st['status_label'],
                        'last_attempt': st['action_date'], 'status_reason': st['reason']})
        else:
            # Not live is NOT the same as nothing collected — say Unknown.
            row.update({'status': '', 'last_attempt': '', 'status_reason': '',
                        'status_label': ('No debit in this period' if status_live
                                         else 'Unknown')})

    out = sorted(rows.values(), key=lambda r: r['policy_number'])
    tally: dict[str, int] = {}
    for r in out:
        tally[r['status_label']] = tally.get(r['status_label'], 0) + 1
    return {'rows': out, 'count': len(out), 'tally': tally,
            'status_live': status_live, 'graphite_live': live_ok,
            # A cap that bites silently is the same class of fault as the export
            # that returned 500 rows and a full total (bug d4e6d54a).
            'truncated': bool(gp is not None and len(gp) >= _POLICY_CAP)}


# ─── Workbook import ─────────────────────────────────────────────────────────

_HEADERS = {
    # No bare 'policy' key: Rose's tab has 'Insured Name' but other sheets use
    # 'Policy Name', and a bare 'policy' would claim that column as the number.
    'policy_number':        ('policy no', 'policy number', 'policy #'),
    'insured_name':         ('insured name', 'client name', 'policy name'),
    'premium':              ('premium',),
    'amount_received':      ('amount received',),
    'motor_premium':        ('motor premium',),
    'motor_commission':     ('motor commission', 'motor commision'),
    'non_motor_premium':    ('non motor premium',),
    'non_motor_commission': ('non motor commission', 'non motor commision'),
    'commission_payable':   ('commission payable', 'commision payable'),
    'vat':                  ('vat',),
}
_TOTALS = re.compile(r'^(grand\s+)?totals?$', re.I)


def _norm(v) -> str:
    return ' '.join(str(v or '').lower().replace('.', ' ').split())


def _money(v) -> Decimal:
    s = str(v if v is not None else '').replace('\xa0', '').replace(',', '').replace('P', '').strip()
    if s in ('', '-', '–'):
        return Decimal('0.00')
    neg = s.startswith('(') and s.endswith(')')
    if neg:
        s = s[1:-1]
    try:
        d = Decimal(s)
    except (InvalidOperation, ValueError):
        return Decimal('0.00')
    # HALF UP, never Python's default HALF_EVEN: rounding is a tax decision and
    # VAT rounds half up (CFO standing order). These cells carry VAT and
    # commission straight off Finance's sheet.
    return (-d if neg else d).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _map_headers(row) -> dict:
    """field -> column index. Longest header text wins, so 'Non Motor Premium'
    is not swallowed by the 'premium' rule."""
    out, used = {}, set()
    cells = [(i, _norm(c)) for i, c in enumerate(row)]
    for field, keys in sorted(_HEADERS.items(), key=lambda kv: -max(len(k) for k in kv[1])):
        for i, h in cells:
            if i in used or not h:
                continue
            if any(h == k or h.startswith(k) for k in keys):
                out[field], _ = i, used.add(i)
                break
    return out


def parse_broker_sheet(rows) -> list[dict]:
    """Parse ONE broker tab. Returns [] when the sheet has no policy table."""
    hi = hmap = None
    for i, row in enumerate(rows[:15]):
        m = _map_headers(row)
        if 'policy_number' in m and len(m) >= 3:
            hi, hmap = i, m
            break
    if hmap is None:
        return []
    out = []
    for row in rows[hi + 1:]:
        def cell(f):
            i = hmap.get(f)
            return row[i] if i is not None and i < len(row) else None
        pol = str(cell('policy_number') or '').strip()
        if not pol or _TOTALS.match(pol):
            continue
        out.append({
            'policy_number': pol[:60],
            'insured_name': str(cell('insured_name') or '').strip()[:160],
            'premium': _money(cell('premium')),
            'amount_received': _money(cell('amount_received')),
            'motor_premium': _money(cell('motor_premium')),
            'motor_commission': _money(cell('motor_commission')),
            'non_motor_premium': _money(cell('non_motor_premium')),
            'non_motor_commission': _money(cell('non_motor_commission')),
            'commission_payable': _money(cell('commission_payable')),
            'vat': _money(cell('vat')),
        })
    return out


def iter_workbook_sheets(path):
    """(sheet_name, rows) for EVERY sheet, reusing the commissions importer's
    readers (.xlsx/.xlsm via openpyxl, .xlsb via pyxlsb, the rest via calamine)
    rather than keeping a second copy of them.

    🔴 What is NOT reused is commissions.importer.parse_workbook: that returns
    only the single sheet with the MOST rows, so a 28-broker-tab workbook would
    import ONE tab and look like it had worked.
    """
    from commissions import importer
    for name, rows, _xlsb in importer._iter_sheets(path):
        yield name, rows


@transaction.atomic
def import_workbook(path, period_label: str = '', user=None, commit: bool = True,
                    _sheets=None) -> dict:
    """Import every broker tab in the workbook, matching tabs to brokers.

    A tab that matches no known broker is REPORTED, never silently dropped —
    that is the whole question Rose asked ("which brokers need tabs created?").
    """
    # Matching a TAB to a broker is a different job from merging two Graphite
    # agency rows, and it is safe to be looser here: the tab was named by a
    # person, an unmatched tab is REPORTED rather than guessed, and an ambiguous
    # one is refused. Finance writes 'Redhill', 'Minet', 'FirstSun' — short
    # working names that will never equal the registered name's merge key.
    known: dict[str, Broker] = {}
    loose: dict[str, list[Broker]] = {}
    for b in Broker.objects.prefetch_related('aliases'):
        texts = [b.name, b.short_name] + [al.graphite_agency_name for al in b.aliases.all()]
        for t in texts:
            k = merge_key(t)
            if k:
                known.setdefault(k, b)
            # the raw text with everything but letters/digits removed, so a tab
            # called 'Mikardow' still finds 'Mikardow Investments t/a Ignytwealth'
            flat = _flatten(t)
            if flat:
                loose.setdefault(flat, []).append(b)
    known.pop('', None)

    def _find(sheet_name: str):
        """(broker, note). note is set when nothing matched or it was ambiguous."""
        k = merge_key(sheet_name)
        if k and k in known:
            return known[k], ''
        flat = _flatten(sheet_name)
        if not flat:
            return None, 'the tab name is blank'
        hits = {id(b): b for key, bs in loose.items()
                if key.startswith(flat) or flat.startswith(key)
                for b in bs}
        if len(hits) == 1:
            return next(iter(hits.values())), ''
        if len(hits) > 1:
            names = sorted(b.name for b in hits.values())
            return None, 'matches more than one broker: ' + ', '.join(names)
        return None, 'no broker on the register matches this tab'

    # C3b — a policy belongs to ONE broker. Who holds each policy number now
    # (upper-cased, as the Add Policy guard compares case-insensitively), plus
    # whatever an earlier tab of THIS workbook has already claimed.
    holder: dict[str, Broker] = {
        pn.upper(): b for pn, b in
        ((p.policy_number, p.broker) for p in
         BrokerPolicy.objects.select_related('broker').only('policy_number', 'broker__name'))
    }

    matched, unmatched, skipped, refused, written = [], [], [], [], 0
    for sheet, rows in (_sheets if _sheets is not None else iter_workbook_sheets(path)):
        parsed = parse_broker_sheet(rows)
        if not parsed:
            skipped.append(sheet)
            continue
        broker, why = _find(sheet)
        if broker is None:
            unmatched.append({'sheet': sheet, 'rows': len(parsed), 'reason': why})
            continue
        matched.append({'sheet': sheet, 'broker': broker.name, 'rows': len(parsed)})
        for r in parsed:
            key = r['policy_number'].upper()
            held = holder.get(key)
            if held is not None and held.pk != broker.pk:
                refused.append({'sheet': sheet, 'policy_number': r['policy_number'],
                                'held_by': held.name})
                continue
            holder[key] = broker
            if not commit:
                continue
            BrokerPolicy.objects.update_or_create(
                broker=broker, policy_number=r['policy_number'],
                period_label=period_label or '',
                defaults={**r, 'source': BrokerPolicy.Source.UPLOAD, 'added_by': user},
            )
            written += 1
    return {'matched': matched, 'unmatched': unmatched, 'sheets_without_a_table': skipped,
            'refused': refused, 'rows_written': written, 'committed': bool(commit)}
