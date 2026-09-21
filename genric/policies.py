"""genric/policies.py — the policy book, and what the bank says about it.

Two sources, one shape:

  * the Graphite all-policy export Finance attaches (a CSV), plus the
    prior-month export — this is what Finance actually has in hand, and the
    build prompt lists both as inputs; and
  * ``integrations.graphite_ro.query()`` — the read-only replica, for when the
    book can be read directly instead of exported.

Both land as ``PolicyRow``. Nothing downstream knows or cares which was used;
the Master report records which one, so a figure can always be traced back to
the file or the query that produced it.

DPA: only the policy number travels. The control is an ALLOW-LIST, not a
deny-list: ``normalise_row`` builds a ``PolicyRow`` out of the seven fields it
names and nothing else, so a column the export has never carried before — a new
name, ID number, address or contact field — is dropped AT LOAD simply by not
being asked for, rather than by being listed as forbidden. There is therefore no
path by which a name reaches an export, an email or a log. The build prompt says
"policy numbers only (DPA)"; this is where that is enforced.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Iterable, Optional, Sequence

from . import constants as K
from .money import q2

# ── Graphite payment statuses, per the build prompt ─────────────────────────
PAID = 'paid'
FAILED = 'failed'
DORMANT = 'dormant'
UNKNOWN = 'unknown'

_STATUS_MAP = {
    'payment successful': PAID,
    'successful': PAID,
    'paid': PAID,
    'payment failed': FAILED,
    'failed': FAILED,
    'payment status not found': DORMANT,
    'status not found': DORMANT,
    'not found': DORMANT,
    '': DORMANT,
}

STATUS_LABELS = {
    PAID: 'Payment Successful — collected',
    FAILED: 'Payment Failed — debit order bounced',
    DORMANT: 'Payment Status Not Found — dormant, no collection',
    UNKNOWN: 'Unrecognised payment status',
}


@dataclass(frozen=True)
class PolicyRow:
    policy_number: str
    status: str                      # PAID | FAILED | DORMANT | UNKNOWN
    raw_status: str
    created_at: Optional[date]       # inception / createdAt — needed for new business + the 18-month rule
    premium_incl_vat: Decimal
    active: bool
    #: The date of the failed or missing collection the pay window runs from.
    #: None when the export does not carry it — and None is NEVER stood in for.
    #: See the note on ``_LAST_FAILED_KEYS`` below.
    last_failed_collection_at: Optional[date] = None

    def age_months(self, as_at: date) -> Optional[int]:
        if not self.created_at:
            return None
        return (as_at.year - self.created_at.year) * 12 + (as_at.month - self.created_at.month)

    def unpaid_days(self, as_at: date) -> Optional[int]:
        """Days unpaid at ``as_at``, or None when it cannot be measured.

        ``as_at`` is the pack's period END, never "today". A July pack must
        measure the window as at 31 July however long afterwards the button is
        pressed; measuring against the clock would give a different answer every
        day and would drag the machine's timezone into a day count. There is
        deliberately no ``date.today()`` anywhere in this app.

        None means "the export did not say when this policy last failed". It is
        deliberately NOT zero and deliberately NOT the inception date: either
        substitution produces a day count that looks real, on the one list in
        this pack that gets ACTED ON.
        """
        if not self.last_failed_collection_at:
            return None
        return (as_at - self.last_failed_collection_at).days


# ── Header normalisation ────────────────────────────────────────────────────
# Graphite exports have been through several column namings. Match on a
# normalised key rather than an exact header, and NEVER positionally: a column
# order change would silently swap policy number for status.
def _norm(s: str) -> str:
    return ''.join(ch for ch in (s or '').lower() if ch.isalnum())


_POLICY_KEYS = {'policynumber', 'policyno', 'policy', 'policynum', 'polnumber'}
_STATUS_KEYS = {'paymentstatus', 'status', 'collectionstatus', 'debitorderstatus'}
_CREATED_KEYS = {'createdat', 'created', 'inceptiondate', 'inception', 'startdate', 'commencementdate'}
_PREMIUM_KEYS = {'premium', 'premiumamount', 'monthlypremium', 'grosspremium', 'premiuminclvat'}
_ACTIVE_KEYS = {'active', 'isactive', 'policystatus', 'state'}

# The date the pay window runs from. This list is deliberately NARROW and only
# holds names that can mean one thing: the date of a collection that FAILED, or
# the date the policy went unpaid.
#
# A looser name was considered and rejected. "lastcollectiondate" / "lastpayment
# date" are the obvious near-misses, and on most exports they mean the last
# collection that SUCCEEDED. Reading one of those as a failure date would count
# the window from the last time the customer PAID US, which on a long-standing
# policy is a large number — every such policy would land on the cancellation
# list on day one. Reading nothing is recoverable; reading the wrong column
# cancels paying customers, and the report cannot tell the difference.
#
# So: if the export starts carrying a failed-collection date under some other
# heading, ADD IT HERE deliberately after checking what it means. Do not widen
# the match to catch it by accident.
_LAST_FAILED_KEYS = {
    'lastfailedcollectiondate', 'lastfailedcollection', 'lastfailedcollectionat',
    'failedcollectiondate', 'failedcollectionat', 'collectionfaileddate',
    'lastfailedpaymentdate', 'lastfailedpayment', 'lastfaileddebitorderdate',
    'unpaidsince', 'unpaidsincedate', 'unpaidfrom',
}


def _pick(row: dict, keys: set) -> str:
    for k, v in row.items():
        if _norm(k) in keys:
            return (v or '').strip() if isinstance(v, str) else v
    return ''


def _local_day(moment: datetime) -> date:
    """The calendar day a timestamp fell on IN BOTSWANA — never in UTC.

    Botswana and South Africa are UTC+2, so every timestamp between 22:00 and
    midnight UTC belongs to the NEXT local day. Calling .date() on a tz-aware
    UTC datetime silently reports the day before, and this app counts DAYS: a
    policy that failed at 22:30 on 30 June reads as 31 days unpaid at the July
    period end instead of 30, which is precisely the far side of the pay-window
    boundary — it would be recommended for cancellation a day early, the one
    error build_cancellations is written to avoid.

    Naive values are left alone: the export's date-only and local formats carry
    no zone and are already local.
    """
    if moment.tzinfo is None:
        return moment.date()
    from zoneinfo import ZoneInfo
    from django.conf import settings as dj_settings
    return moment.astimezone(ZoneInfo(dj_settings.TIME_ZONE)).date()


def _parse_date(value) -> Optional[date]:
    if not value:
        return None
    if isinstance(value, datetime):
        return _local_day(value)
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    # Graphite exports have shipped all of these. ISO first — it is
    # unambiguous — then the two local forms, day-first (SA/BW convention).
    for fmt in ('%Y-%m-%d', '%Y-%m-%dT%H:%M:%S', '%Y/%m/%d',
                '%d/%m/%Y', '%d-%m-%Y', '%d %b %Y', '%d %B %Y'):
        try:
            return datetime.strptime(text[:len(fmt) + 8].strip(), fmt).date()
        except ValueError:
            continue
    try:
        # An ISO timestamp with a zone ('...T22:30:00Z') lands here. Convert to
        # the Botswana day before taking .date() — see _local_day.
        return _local_day(datetime.fromisoformat(text.replace('Z', '+00:00')))
    except ValueError:
        return None


def _parse_money(value) -> Decimal:
    if value in (None, ''):
        return K.STANDARD_PREMIUM_INCL_VAT
    if isinstance(value, Decimal):
        return q2(value)
    text = str(value).replace('R', '').replace(',', '').replace(' ', '').strip()
    if not text:
        return K.STANDARD_PREMIUM_INCL_VAT
    try:
        return q2(Decimal(text))
    except (InvalidOperation, ValueError, TypeError):
        # A premium column that will not parse falls back to the standard R99
        # rather than to zero: zero would quietly remove the policy from the
        # premium-at-risk total, which is the figure the whole cancellations
        # decision turns on. Named exceptions only — a blind except here would
        # swallow a genuine bug in the loader.
        return K.STANDARD_PREMIUM_INCL_VAT


def _is_active(raw) -> bool:
    text = str(raw or '').strip().lower()
    if text in ('', 'true', '1', 'yes', 'y', 'active', 'inforce', 'in force', 'live'):
        return True
    return text not in ('false', '0', 'no', 'n', 'cancelled', 'canceled', 'lapsed', 'inactive', 'terminated')


def normalise_row(row: dict) -> Optional[PolicyRow]:
    """One export row → a PolicyRow, PII dropped. None if there is no policy number."""
    policy = str(_pick(row, _POLICY_KEYS) or '').strip()
    if not policy:
        return None
    raw_status = str(_pick(row, _STATUS_KEYS) or '').strip()
    status = _STATUS_MAP.get(raw_status.lower(), UNKNOWN if raw_status else DORMANT)
    return PolicyRow(
        policy_number=policy,
        status=status,
        raw_status=raw_status,
        created_at=_parse_date(_pick(row, _CREATED_KEYS)),
        premium_incl_vat=_parse_money(_pick(row, _PREMIUM_KEYS)),
        active=_is_active(_pick(row, _ACTIVE_KEYS)),
        # No fallback. An export with no failed-collection column leaves this
        # None and the cancellations report says so, per policy and in its KPIs,
        # rather than measuring the pay window from a date that means something
        # else. (Contrast _parse_money above, where falling back to the standard
        # premium is safe — it only affects a total, never who gets cancelled.)
        last_failed_collection_at=_parse_date(_pick(row, _LAST_FAILED_KEYS)),
    )


def load_export(content) -> list[PolicyRow]:
    """Parse a Graphite all-policy CSV export into PolicyRows (PII dropped)."""
    if isinstance(content, bytes):
        content = content.decode('utf-8-sig', errors='replace')
    reader = csv.DictReader(io.StringIO(content))
    out = []
    for raw in reader:
        row = normalise_row(raw)
        if row:
            out.append(row)
    return out


def load_rows(rows: Iterable[dict]) -> list[PolicyRow]:
    """Same normalisation for rows already in memory (graphite_ro, or a test)."""
    out = []
    for raw in rows:
        row = normalise_row(raw)
        if row:
            out.append(row)
    return out


def load_from_graphite_ro(limit: int = 5000) -> list[PolicyRow]:
    """Read the policy book off the read-only replica.

    Deliberately SELECTs named columns and no PII column at all, rather than
    ``SELECT *`` followed by a filter — the query itself must be safe to read in
    a log.

    🔴 No failed-collection date is selected. The replica's column for it has not
    been identified, and inventing a name here does not fail safe — it raises a
    SQL error and takes the whole policy read down with it. Until somebody
    confirms the real column, policies loaded this way carry no
    ``last_failed_collection_at`` and the cancellations report counts them as
    "cannot be measured" rather than as in or out of the window. Add the column
    to this SELECT once its name is known; ``normalise_row`` already reads it
    (see ``_LAST_FAILED_KEYS``).
    """
    from integrations import graphite_ro
    sql = (
        'SELECT policy_number, payment_status, created_at, premium, status '
        'FROM policies WHERE status IS NOT NULL'
    )
    return load_rows(graphite_ro.query(sql, limit=limit))


# ── Classification against the bank ─────────────────────────────────────────
@dataclass(frozen=True)
class BookPosition:
    total_active: int
    paid: list
    failed: list
    dormant: list
    unknown: list
    bank_confirmed_count: int
    graphite_paid_count: int

    @property
    def bank_vs_graphite_gap(self) -> int:
        """Positive = the bank saw more collections than Graphite called successful.

        July 2026: bank 93, Graphite 81, gap 12. This gap is reported, never
        reconciled away — the bank is the truth and the difference is the point.
        """
        return self.bank_confirmed_count - self.graphite_paid_count

    @property
    def cancellation_candidates(self) -> list:
        """Failed + dormant. Candidates only — nothing here is ever auto-cancelled."""
        return list(self.failed) + list(self.dormant)

    @property
    def monthly_premium_at_risk(self) -> Decimal:
        return q2(sum((p.premium_incl_vat for p in self.cancellation_candidates),
                      Decimal('0.00')))


def position(policies: Sequence[PolicyRow], bank_confirmed_count: int) -> BookPosition:
    active = [p for p in policies if p.active]
    return BookPosition(
        total_active=len(active),
        paid=[p for p in active if p.status == PAID],
        failed=[p for p in active if p.status == FAILED],
        dormant=[p for p in active if p.status == DORMANT],
        unknown=[p for p in active if p.status == UNKNOWN],
        bank_confirmed_count=bank_confirmed_count,
        graphite_paid_count=len([p for p in active if p.status == PAID]),
    )


def consecutive_failures(current: Sequence[PolicyRow],
                         prior: Sequence[PolicyRow]) -> set:
    """Policy numbers that failed or were dormant in BOTH months.

    The treaty's 2-month grace (Art. 10.4) needs two consecutive months of
    failure, which is why the prior-month export is a required input. With no
    prior month this returns an empty set and the caller must say so — it must
    never quietly treat "we have no prior month" as "no policy has failed twice".
    """
    prior_bad = {p.policy_number for p in prior if p.status in (FAILED, DORMANT)}
    return {p.policy_number for p in current
            if p.status in (FAILED, DORMANT) and p.policy_number in prior_bad}


def month_end(year: int, month: int) -> date:
    import calendar
    return date(year, month, calendar.monthrange(year, month)[1])
