"""
bonu/members.py — the membership roll, and the one question it exists to answer.

CFO 2026-08-18: *"We also need a place where I can put all the member details so we only
pay claims for members who pay premiums. It's very important."*

Why this could not be derived from money we already hold: BONU has 9,000+ members and
pays Alpha Direct **two payments a month for the whole scheme**. Two lump sums cannot tell
you which of nine thousand people is up to date, so paid-up status has to come from the
union's own membership list. Everything here is therefore anchored to a DATED LOAD of that
list (`BonuMemberListLoad`), never to our bank statement.

`bonu/member_rules.py` has named this gap since 3 Aug 2026 — *"Still missing, and it is the
big one: whether that member's premium was actually PAID, and whether they were on cover on
the day the work was done."* This module is that missing half.

**It advises, it does not yet block a payment.** `check(...)` returns a verdict and a
reason; nothing in this module reaches into the payment path. Wiring a refusal directly
into money movement is the CFO's call, because on the day the union's list arrives late
every genuine claim in the country would stop. The verdict is built to be shown to a human
who is about to pay, and to be recorded next to what they decided.
"""
from __future__ import annotations

import datetime as dt
import logging
from decimal import Decimal

from bonu.member_rules import MEMBER_ANNUAL_LIMIT
from bonu.models import BonuMember, BonuMemberListLoad
from django.utils import timezone

logger = logging.getLogger(__name__)

Z = Decimal('0')

#: Verdicts. Deliberately three, not two — "we cannot tell" must never be dressed
#: up as either a yes or a no. A refusal we cannot substantiate is how a paid-up
#: nurse gets turned away; an approval we cannot substantiate is how the scheme
#: pays for someone who left the union two years ago.
ALLOW = 'allow'
REFUSE = 'refuse'
UNKNOWN = 'unknown'


def current_list():
    """The list eligibility is measured against, or None if none has been loaded."""
    return (BonuMemberListLoad.objects.filter(is_current=True).order_by('-as_at').first()
            or BonuMemberListLoad.objects.order_by('-as_at').first())


def _spend_this_year(member, on_date):
    """What the scheme has already spent on this member in the CALENDAR year.

    The calendar year is the CFO's definition (3 Aug 2026), not a rolling twelve
    months. Totalled off the invoice lines by member token, so no name is needed.
    """
    if not member.member_token:
        return None
    from bonu.models import BonuInvoiceLine
    rows = BonuInvoiceLine.objects.filter(
        member_token=member.member_token,
        invoice__invoice_date__year=on_date.year)
    return sum((r.amount or Z for r in rows), Z)


def check(membership_no, *, on_date=None, matter_type=''):
    """May the scheme pay for this member, on this date?

    Returns {'verdict', 'reason', 'membership_no', 'member', 'list_as_at', 'spend_this_year'}.

    Every refusal carries the fact behind it, because "not a member" is an accusation
    while "not on the list dated 31 July, which carried 9,014 members" is something the
    union can check and correct.
    """
    on_date = on_date or timezone.localdate()
    ref = (membership_no or '').strip()
    lst = current_list()

    def out(verdict, reason, member=None, spend=None):
        return {
            'verdict': verdict,
            'reason': reason,
            'membership_no': ref,
            'member': member,
            'list_as_at': lst.as_at.isoformat() if lst else None,
            'spend_this_year': spend,
        }

    if not ref:
        return out(UNKNOWN, 'No membership number was given, so nothing can be checked.')

    if lst is None:
        # The honest answer on day one. NOT a refusal: with no list loaded, refusing
        # would stop every legitimate claim, and approving would defeat the point.
        return out(UNKNOWN,
                   'No membership list has been loaded yet, so paid-up status cannot be '
                   'checked. Load the union\'s list before relying on this answer.')

    member = BonuMember.objects.filter(membership_no__iexact=ref).first()
    if member is None:
        return out(REFUSE,
                   f'Membership number {ref} does not appear on any membership list the '
                   f'union has sent us. The most recent is dated {lst.as_at}.')

    if member.last_seen_id and member.last_seen_id != lst.id:
        return out(REFUSE,
                   f'Last appeared on the list dated {member.last_seen.as_at}, and is NOT on '
                   f'the current list dated {lst.as_at}. Treat as no longer a member until '
                   f'the union says otherwise.', member=member)

    if member.left_on and on_date > member.left_on:
        return out(REFUSE, f'Left the union on {member.left_on}, before this date.', member=member)

    if member.joined_on and on_date < member.joined_on:
        return out(REFUSE,
                   f'Joined on {member.joined_on}, which is after this date — the member was '
                   f'not on cover when the work was done.', member=member)

    if member.status in (BonuMember.Status.RESIGNED, BonuMember.Status.SUSPENDED,
                         BonuMember.Status.ARREARS):
        return out(REFUSE,
                   f'The union\'s list dated {lst.as_at} marks this member '
                   f'"{member.get_status_display()}".', member=member)

    if member.paid_up_to and member.paid_up_to < on_date:
        return out(REFUSE,
                   f'Premium is paid only to {member.paid_up_to}, which is before this date.',
                   member=member)

    spend = _spend_this_year(member, on_date)
    if spend is not None and spend >= MEMBER_ANNUAL_LIMIT:
        return out(REFUSE,
                   f'The {MEMBER_ANNUAL_LIMIT:,.0f} annual legal benefit for {on_date.year} is '
                   f'already used up — {spend:,.2f} spent. This is a breach of cover, not a '
                   f'judgement call.', member=member, spend=spend)

    if member.status == BonuMember.Status.UNKNOWN:
        return out(UNKNOWN,
                   f'On the current list dated {lst.as_at}, but the list did not state a '
                   f'paid-up status, so premium payment cannot be confirmed.',
                   member=member, spend=spend)

    return out(ALLOW,
               f'On the union\'s current list dated {lst.as_at}, marked '
               f'"{member.get_status_display()}".', member=member, spend=spend)


# ---------------------------------------------------------------------------
# Loading the union's list
# ---------------------------------------------------------------------------

#: Header synonyms. The union's file is not ours and its headings move, so match
#: on meaning. Never match a column by POSITION — the same mistake that put one
#: NBFIRA class's figure on another class's line (18-Aug).
FIELD_HINTS = {
    'membership_no': ('membership no', 'membership number', 'member no', 'member number',
                      'membership', 'memb no', 'member id'),
    'full_name': ('full name', 'member name', 'names', 'name'),
    'station': ('station', 'work station', 'workplace', 'work place', 'hospital',
                'clinic', 'employer', 'facility'),
    'district': ('district', 'region', 'town', 'branch', 'location', 'area'),
    'status': ('status', 'membership status', 'standing'),
    'monthly_premium': ('premium', 'monthly premium', 'contribution', 'subscription'),
    'paid_up_to': ('paid up to', 'paid to', 'paid up', 'paid until'),
    'joined_on': ('date joined', 'joined', 'join date', 'date of joining'),
    'left_on': ('date left', 'left', 'exit date', 'resigned on'),
}

#: What the union's words mean in our four statuses. Anything unrecognised stays
#: UNKNOWN rather than being guessed into ACTIVE — guessing here pays claims.
STATUS_WORDS = {
    'active': BonuMember.Status.ACTIVE, 'paid': BonuMember.Status.ACTIVE,
    'paid up': BonuMember.Status.ACTIVE, 'current': BonuMember.Status.ACTIVE,
    'good standing': BonuMember.Status.ACTIVE, 'member': BonuMember.Status.ACTIVE,
    'arrears': BonuMember.Status.ARREARS, 'in arrears': BonuMember.Status.ARREARS,
    'unpaid': BonuMember.Status.ARREARS, 'overdue': BonuMember.Status.ARREARS,
    'defaulter': BonuMember.Status.ARREARS,
    'suspended': BonuMember.Status.SUSPENDED, 'frozen': BonuMember.Status.SUSPENDED,
    'resigned': BonuMember.Status.RESIGNED, 'left': BonuMember.Status.RESIGNED,
    'terminated': BonuMember.Status.RESIGNED, 'exited': BonuMember.Status.RESIGNED,
    'withdrawn': BonuMember.Status.RESIGNED, 'deceased': BonuMember.Status.RESIGNED,
}


def map_columns(columns):
    """{field: column label} for the columns we recognise. Longest hint wins so
    'membership number' is not claimed by the looser 'member'."""
    out = {}
    lowered = [(c, (c or '').strip().lower()) for c in columns]
    for field, hints in FIELD_HINTS.items():
        best, best_len = '', -1
        for col, low in lowered:
            for h in hints:
                if h in low and len(h) > best_len:
                    best, best_len = col, len(h)
        if best:
            out[field] = best
    return out


def read_status(raw):
    low = (raw or '').strip().lower()
    if not low:
        return BonuMember.Status.UNKNOWN
    if low in STATUS_WORDS:
        return STATUS_WORDS[low]
    for word, status in STATUS_WORDS.items():
        if word in low:
            return status
    return BonuMember.Status.UNKNOWN


def read_date(raw):
    """A date out of the union's spreadsheet. None ONLY when the cell is blank.

    A value we cannot read RAISES rather than returning None, because None means
    "no date", and a missing `paid_up_to` makes a member look LESS constrained,
    not more — an unreadable date would quietly widen who may be paid. The loader
    records these against the load so they are visible, never silent.

    Excel serials are handled: an xlsb date arrives as a number and becomes
    nonsense otherwise. The range is checked instead of caught, so nothing here
    depends on an exception to decide what is valid.
    """
    s = (raw or '').strip()
    if not s:
        return None

    if s.replace('.', '', 1).isdigit():
        serial = int(float(s))
        # 1000 excludes a bare year typed into a date column; 100000 is far past
        # any real membership date and catches a phone number pasted by mistake.
        if 1000 < serial < 100000:
            return dt.date(1899, 12, 30) + dt.timedelta(days=serial)
        raise ValueError(f'{s!r} is a number but not a usable date serial')

    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%Y/%m/%d', '%d %b %Y', '%d %B %Y'):
        try:
            return dt.datetime.strptime(s[:11].strip(), fmt).date()
        except ValueError: continue    # not this format — try the next one
    raise ValueError(f'{s!r} is not a date in any format we recognise')


def read_money(raw):
    """An amount, or None when the cell is blank. Unreadable RAISES — same reason
    as read_date: a silent None cannot be told apart from "nothing was stated"."""
    s = (raw or '').replace(',', '').replace('P', '').strip()
    if not s:
        return None
    try:
        return Decimal(s)
    except (ArithmeticError, TypeError, ValueError) as e: raise ValueError(f'{raw!r} is not an amount') from e


def read_or_note(reader, raw, label, notes):
    """The value, or None having RECORDED why it could not be read.

    The single place in the loader that tolerates a bad cell. One row with a
    mistyped date must not abandon a 9,000-row load, but nor may the problem
    vanish — so it lands in `notes`, which the loader writes onto the load record
    and the upload response shows back to whoever loaded the file.
    """
    try:
        return reader(raw)
    except ValueError as e: notes.append(f'{label} — {e}'); return None


def load_rows(rows, *, as_at, source_name='', source_note='', user=None,
              make_current=True, columns=None):
    """Upsert a membership list. Returns the BonuMemberListLoad.

    Members ABSENT from this list are deliberately left alone, not deleted. Their
    `last_seen` simply stops advancing, and that is precisely what makes
    "was on July's list, not on August's" answerable. Deleting them would destroy
    the only evidence behind a refusal.
    """
    from django.db import transaction
    # Reuse the existing tokeniser and the existing salt — a second salt would
    # produce tokens that never match the invoice side, silently.
    from bonu.member_identity import get_salt, token

    email = (getattr(user, 'email', '') or '')[:200]
    salt = get_salt()
    with transaction.atomic():
        load = BonuMemberListLoad(as_at=as_at, source_name=source_name[:300],
                                  source_note=source_note, rows_seen=len(rows),
                                  loaded_by_email=email, is_current=False)
        load.save(audit_user=user)

        cols = map_columns(columns or (list(rows[0].keys()) if rows else []))
        if 'membership_no' not in cols:
            raise ValueError(
                'No membership-number column found. Looked for: '
                + ', '.join(FIELD_HINTS['membership_no'])
                + '. Without it a row cannot be tied to a person, so nothing was loaded.')

        loaded = 0
        warnings = []
        for cells in rows:
            ref = (cells.get(cols['membership_no']) or '').strip()
            if not ref:
                continue
            def val(field):
                col = cols.get(field)
                return (cells.get(col) or '').strip() if col else ''

            member, _new = BonuMember.objects.get_or_create(
                membership_no=ref, defaults={'first_seen': load})
            member.full_name = val('full_name')[:200] or member.full_name
            member.station = val('station')[:200] or member.station
            member.district = val('district')[:120] or member.district
            if cols.get('status'):
                member.status = read_status(val('status'))
            for field, reader in (('monthly_premium', read_money), ('paid_up_to', read_date),
                                  ('joined_on', read_date), ('left_on', read_date)):
                if cols.get(field):
                    got = read_or_note(reader, val(field), f'{ref}: {field}', warnings)
                    if got is not None:
                        setattr(member, field, got)
            if member.full_name and not member.member_token:
                member.member_token = token(member.full_name, salt)
            member.cells = dict(cells)
            member.last_seen = load
            member.first_seen = member.first_seen or load
            member.updated_by_email = email
            member.save(audit_user=user)
            loaded += 1

        load.members_loaded = loaded
        if warnings:
            # Visible on the screen and in the load record, so a column the union
            # reformatted cannot quietly stop being read.
            head = '; '.join(warnings[:20])
            load.source_note = (
                f'{load.source_note}\n{len(warnings)} value(s) could not be read and were '
                f'left unset: {head}').strip()
            logger.warning('BONU member list %s: %d unreadable value(s)', as_at, len(warnings))
        if make_current:
            BonuMemberListLoad.objects.exclude(pk=load.pk).update(is_current=False)
            load.is_current = True
        load.save(audit_user=user)
    return load


def load_workbook(path, *, as_at, source_name='', user=None, make_current=True):
    """Read the union's spreadsheet and load its first populated sheet.

    Reuses `bonu.schedule.parse_workbook` — the header-detection there already
    survives the union's habit of putting a title and blank rows above the
    headings, and duplicating that logic is how two parsers drift apart.
    """
    from bonu.schedule import parse_workbook
    sheets = [s for s in parse_workbook(path) if s['rows']]
    if not sheets:
        raise ValueError('That file has no rows in any sheet.')
    sheet = max(sheets, key=lambda s: len(s['rows']))
    return load_rows([r['cells'] for r in sheet['rows']], as_at=as_at,
                     source_name=source_name or sheet['title'], user=user,
                     make_current=make_current, columns=sheet['columns'])


def summary():
    """The counts the screen leads with."""
    lst = current_list()
    qs = BonuMember.objects.all()
    on_current = qs.filter(last_seen=lst).count() if lst else 0
    return {
        'members_total': qs.count(),
        'list_as_at': lst.as_at.isoformat() if lst else None,
        'list_source': lst.source_name if lst else '',
        'on_current_list': on_current,
        'not_on_current_list': qs.count() - on_current,
        'by_status': {s: qs.filter(status=s).count() for s, _ in BonuMember.Status.choices},
    }
