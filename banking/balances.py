"""banking/balances.py — the Morning Bank Balances read model.

Turns ``BankBalanceSnapshot`` rows into the payload
``GET /api/v1/banking/balances/`` returns. The shape is fixed by
BALANCES_CONTRACT.md and is not this module's to change.

Two rules run through everything here:

*   **A figure we do not have is ``None``, never 0.00.** A real zero is
    ``"0.00"`` with status ``ok``. Confusing the two is what made the Claims
    account report P0.00 while the bank held about P256,000.
*   **An old figure is never shown as if it were current.** That is what
    ``balance_status`` is for, and why a stale or failed reading carries a
    note and its own timestamp rather than quietly passing for today's.

Full account numbers never leave the server — every row is masked to the last
four digits, and the bank's own error text (which can echo the account number
straight back at us) is never put in a response.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Count, Q, Sum
from django.utils import timezone

from .models import BankAccount, BankBalanceSnapshot


# ── the five statuses, and nothing else ─────────────────────────────────────
OK          = 'ok'
STALE       = 'stale'
FAILED      = 'failed'
NO_BALANCE  = 'no_balance'
NEVER_READ  = 'never_read'

#: A reading counts as current for eight hours — the gap between the 06:00,
#: 12:00 and 17:00 pulls, plus room for one slow run.
FRESH_HOURS = 8

#: "Most serious" for the headline. A row with no figure at all outranks a row
#: that at least has an old one, and never-read — an account nothing is even
#: trying to read — is the worst of the lot.
SEVERITY = {OK: 0, STALE: 1, FAILED: 2, NO_BALANCE: 3, NEVER_READ: 4}

TWO_PLACES = Decimal('0.01')

#: Plain words for the screen. The raw ``account_name`` ("FNBB (Claims
#: A/C)-62493282265") carries the full account number and is never shown.
ACCOUNT_ROLE = {
    '62403392335': 'Current',
    '62493282265': 'Claims',
    '62407809485': 'Call',
    '62477843132': 'Current',
    '62477854999': 'Current',
    '62842621725': 'Current',
}

#: Short company names for the group headings — Company.name is the legal-ish
#: "Alpha Direct Insurance", the CFO's screen says "Alpha Direct".
COMPANY_DISPLAY = {
    'ADIC': 'Alpha Direct',
    'VCM':  'Veritas',
    'RSA':  'Risk Software',
    'UNI':  'Unicoin',
}

#: The requests counted as money already committed out of Omni.
OUTGOING_OMNI_STATUSES = ('pending_finance', 'pending_cfo')

#: Batches at the bank but not yet confirmed debited. `settled` is deliberately
#: absent: that money is already inside the bank's own balance, and taking it
#: off again is a double count.
OUTGOING_AT_BANK_STATUSES = ('submitted', 'acknowledged', 'unknown')

_NUMBER_WORDS = {0: 'no', 1: 'one', 2: 'two', 3: 'three', 4: 'four', 5: 'five',
                 6: 'six', 7: 'seven', 8: 'eight', 9: 'nine', 10: 'ten'}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _money(value) -> str | None:
    """Two places, ROUND_HALF_UP — rounding money is never a language default."""
    if value is None:
        return None
    return str(Decimal(value).quantize(TWO_PLACES, rounding=ROUND_HALF_UP))


def _mask(account_number: str) -> str:
    """Only ever the last four digits. The full number stays on the server."""
    tail = (account_number or '')[-4:]
    return f'…{tail}'


def _word(n: int) -> str:
    return _NUMBER_WORDS.get(n, str(n))


def _iso(moment) -> str | None:
    if moment is None:
        return None
    return moment.astimezone(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def watched_accounts():
    """Every account flagged for FNB reading — the CFO's six today.

    Flag, not name match: see BankAccount.fnb_balance_watch. Deliberately does
    NOT filter on is_active — the contract says an account never disappears
    from this screen, and an account going quiet is exactly what the CFO
    asked to be able to see.
    """
    return (BankAccount.objects
            .filter(fnb_balance_watch=True)
            .select_related('gl_account__owner_company')
            .order_by('account_number'))


# ---------------------------------------------------------------------------
# One account's state
# ---------------------------------------------------------------------------

def resolve_account_state(latest, last_good, *, now=None):
    """(status, balance, taken_at) for one account.

    ``latest``    — its most recent snapshot, or None if it has never been read.
    ``last_good`` — its most recent snapshot that actually carries a figure.

    The two cases worth stating out loud:

    * a FAILED latest read falls back to the older figure and says ``failed``,
      so the CFO sees a number AND sees that it is not today's;
    * a NO_BALANCE latest read does not fall back at all. The bank answered
      and told us it has no balance to give; presenting yesterday's figure as
      the answer to today's question would be exactly the lie this feature
      exists to prevent.
    """
    now = now or timezone.now()

    if latest is None:
        return NEVER_READ, None, None

    if latest.outcome == BankBalanceSnapshot.Outcome.FAILED:
        if last_good is not None:
            return FAILED, last_good.closing, last_good.taken_at
        return FAILED, None, None

    if latest.outcome == BankBalanceSnapshot.Outcome.NO_BALANCE_RETURNED:
        return NO_BALANCE, None, latest.taken_at

    # outcome == ok. Defensive: an `ok` with no figure is a no_balance in all
    # but name, and must not be allowed to render as a blank "ok".
    if latest.closing is None:
        return NO_BALANCE, None, latest.taken_at

    age_hours = (now - latest.taken_at).total_seconds() / 3600.0
    status = OK if age_hours < FRESH_HOURS else STALE
    return status, latest.closing, latest.taken_at


def _note(status, taken_at, *, now):
    """One plain sentence, or ''. Never the bank's own error text — it can
    echo the full account number, which must not leave the server."""
    if status == NEVER_READ:
        return 'Never read from the bank.'
    if status == NO_BALANCE:
        return 'The bank answered but sent no balance.'
    if status == FAILED:
        if taken_at is None:
            return 'The last read failed and there is no earlier figure.'
        return ('The last read failed — this is the figure from '
                f'{timezone.localtime(taken_at):%-d %b %H:%M}.')
    if status == STALE:
        hours = int((now - taken_at).total_seconds() // 3600)
        return f'Last read {hours} hours ago.'
    return ''


# ---------------------------------------------------------------------------
# The outgoings
# ---------------------------------------------------------------------------

def _outgoing_at_bank(account_ids):
    """Per account: money sent to the bank and not yet confirmed debited."""
    from fnb.models import FNBBatchSubmission

    rows = (FNBBatchSubmission.objects
            .filter(source_account_id__in=account_ids,
                    status__in=OUTGOING_AT_BANK_STATUSES)
            .values('source_account_id')
            .annotate(total=Sum('total_amount_bwp'), n=Count('id')))
    return {r['source_account_id']: (r['total'] or Decimal('0.00'), r['n'])
            for r in rows}


def _outgoing_omni_by_account(numbers_by_company):
    """Per ACCOUNT: payment requests raised but not yet through Omni.

    `numbers_by_company` is {company code: [account number, ...]} in the order
    they are shown, so the first entry is that company's main account.

    WHY THIS IS PER-ACCOUNT NOW. The first version totalled by COMPANY and the
    caller then hung the whole company's pending on its first watched account.
    So every Alpha Direct payment — claims included — landed on the Current
    account and the Claims account showed nothing going out. The CFO asked the
    obvious question ("how much loaded for it, and what will be the balance")
    and the screen could not answer it: Current was overstated by P419,199.21
    of claims and Claims understated by the same.

    COMPANY FIRST, THEN CATEGORY — both, in that order, because either alone is
    wrong. Company alone is what produced the bug above. Category alone is
    worse: `source_account_number_for` answers with the DEFAULT (Alpha Direct's
    Current) for every ordinary category, so Veritas and Unicoin pending would
    be subtracted from Alpha Direct's balance — a company paying another
    company's bills on the CFO's screen.

    `taskboard.fnb_autoload.source_account_number_for()` is the map Omni ALREADY
    uses to decide which account really pays a request — CFO 2026-08-21,
    "claims payments will go through alpha Direct claims accounts". Reusing it
    means the screen and the payment agree by construction; a second copy of
    that mapping would drift the day somebody adds a category. It is only
    honoured when the account it names belongs to the request's own company,
    exactly as `_resolve_source_account` scopes it before paying.

    Anything else falls back to the company's main account rather than being
    dropped. Money that cannot be placed precisely must still be visible on the
    company that owes it — losing it understates what is going out, which is the
    wrong direction to be wrong in.
    """
    from taskboard.models import PaymentRequest
    from taskboard.payment_views import _entity_code
    from taskboard.fnb_autoload import source_account_number_for

    totals = {number: Decimal('0.00')
              for numbers in numbers_by_company.values() for number in numbers}
    # NOT the ones already loaded at the bank. Omni loads a request into FNB at
    # FINANCE sign-off, not at CFO authorisation, so a request sitting at
    # pending_cfo normally ALREADY has a live FNBBatchSubmission against the
    # same account — it is waiting for the CFO's phone, at the bank, not in
    # Omni. Counting it in both buckets subtracted the same money twice.
    #
    # Measured on prod 2026-09-20: 10 of the 15 pending requests had a live
    # batch, so P486,835.61 was being taken off twice. The Claims account read
    # a floor of -827,727.02 when the honest figure was -408,527.81, and the
    # CFO's own two screens disagreed with each other — his Omni queue showed
    # BHUMI 111,720.00 waiting for him while the same payment sat in his FNB
    # queue as "Authorisation Requested".
    #
    # `_outgoing_at_bank` already reasons this way about `settled` ("taking it
    # off again is a double count"); the same rule simply was not applied on
    # this side. The bank bucket wins because it is the later, more specific
    # state: money at FNB is nearer to leaving than money still only in Omni.
    # BOTH links, because neither is complete on its own.
    # `PaymentRequest.fnb_batch` is the populated one — 312 of 321 batches on
    # prod 2026-09-20 — but it is written ONLY on the success path
    # (`fnb_autoload.py` `_record(loaded=True, ...)`), so it is NULL after an
    # indeterminate submit, which is exactly the case that leaves a live
    # `unknown` batch behind. `FNBBatchSubmission.payment_request` (reverse
    # `fnb_batches`) is the newer lineage link and covers only 15.
    #
    # Keying on either alone leaves the same money in both buckets. A review
    # recommended the reverse link on its own; measured against this data that
    # would have matched 1 request instead of 10 and silently undone the fix it
    # was reviewing. Measure which relation is populated before trusting a
    # recommendation about it.
    #
    # Resolved to an ID SET rather than an OR across the reverse join: that
    # join fans out one row per batch, and Sum('total') over a fanned-out join
    # multiplies a request's total by how many batches it has — an Individual
    # request with four batches would have counted four times.
    live_batch_requests = set(
        PaymentRequest.objects
        .filter(Q(fnb_batch__status__in=OUTGOING_AT_BANK_STATUSES)
                | Q(fnb_batches__status__in=OUTGOING_AT_BANK_STATUSES))
        .values_list('id', flat=True))

    rows = (PaymentRequest.objects
            .filter(status__in=OUTGOING_OMNI_STATUSES)
            .exclude(id__in=live_batch_requests)
            .values('entity', 'category')
            .annotate(total=Sum('total')))
    for row in rows:
        mine = numbers_by_company.get(_entity_code(row['entity']))
        if not mine:
            continue        # not one of the six accounts — nothing to show it on
        number = source_account_number_for(row['category'] or '')
        if number not in mine:
            number = mine[0]
        totals[number] += (row['total'] or Decimal('0.00'))
    return totals


# ---------------------------------------------------------------------------
# The payload
# ---------------------------------------------------------------------------

def _latest_snapshots(account_ids):
    """(latest, last_good) per account id, in one pass over the snapshots.

    Ordered newest-first, so the first row seen for an account is its latest
    and the first row WITH a figure is its last good one.
    """
    latest, last_good = {}, {}
    for snap in (BankBalanceSnapshot.objects
                 .filter(bank_account_id__in=account_ids)
                 .order_by('bank_account_id', '-taken_at')):
        key = snap.bank_account_id
        latest.setdefault(key, snap)
        if (snap.closing is not None
                and snap.outcome == BankBalanceSnapshot.Outcome.OK):
            last_good.setdefault(key, snap)
    return latest, last_good


def build_balances_payload(*, now=None) -> dict:
    """The whole GET /api/v1/banking/balances/ response."""
    now = now or timezone.now()

    accounts = list(watched_accounts())
    account_ids = [a.id for a in accounts]
    latest, last_good = _latest_snapshots(account_ids)
    at_bank = _outgoing_at_bank(account_ids)

    def company_code(account):
        gl = account.gl_account if account.gl_account_id else None
        owner = gl.owner_company if gl and gl.owner_company_id else None
        return owner.code if owner else ''

    def company_name(account):
        code = company_code(account)
        if code in COMPANY_DISPLAY:
            return COMPANY_DISPLAY[code]
        gl = account.gl_account if account.gl_account_id else None
        owner = gl.owner_company if gl and gl.owner_company_id else None
        return (owner.name if owner else None) or account.bank_name or 'Bank'

    numbers_by_company: dict[str, list[str]] = {}
    for account in accounts:            # already ordered by account_number
        numbers_by_company.setdefault(
            company_code(account), []).append(account.account_number)
    omni_by_number = _outgoing_omni_by_account(numbers_by_company)

    groups: list[dict] = []
    group_index: dict[str, dict] = {}
    read_count = 0
    fresh_count = 0
    total_balance = None
    total_out_omni = Decimal('0.00')
    total_out_bank = Decimal('0.00')
    total_out_count = 0
    oldest_reading = None
    worst = OK

    for account in accounts:
        status, balance, taken_at = resolve_account_state(
            latest.get(account.id), last_good.get(account.id), now=now)

        bank_total, bank_count = at_bank.get(
            account.id, (Decimal('0.00'), 0))
        omni_total = omni_by_number.get(account.account_number, Decimal('0.00'))

        floor = (None if balance is None
                 else balance - omni_total - bank_total)

        # Outgoings are totalled for EVERY account, read or not. The money is
        # committed whether or not we could read the balance it will leave —
        # dropping it because the balance is unknown would understate what is
        # going out, which is the wrong direction to be wrong in.
        total_out_omni += omni_total
        total_out_bank += bank_total
        total_out_count += bank_count

        if balance is not None:
            read_count += 1
            # Counted separately from read_count. `resolve_account_state`
            # hands back an OLDER good figure when today's read failed, so an
            # account can carry a number and still not have been read today.
            # The card's "all six read" test was `read_count == total`, which
            # would go true — and drop its warning — the first morning the
            # three FNB-400 accounts succeed once and then start failing
            # again. It would then show days-old cash as today's, with no
            # amber line, which is the bug this feature shipped three times on
            # 19-Sep. Freshness is a DIFFERENT question from having a figure.
            if status == OK:
                fresh_count += 1
            total_balance = (balance if total_balance is None
                             else total_balance + balance)
            if oldest_reading is None or (
                    taken_at is not None and taken_at < oldest_reading):
                oldest_reading = taken_at

        if SEVERITY[status] > SEVERITY[worst]:
            worst = status

        role = ACCOUNT_ROLE.get(account.account_number)
        label = (f'{company_name(account)} — '
                 f'{role or "Account " + _mask(account.account_number)}')

        row = {
            'id':             str(account.id),
            'label':          label,
            'account_masked': _mask(account.account_number),
            'balance':        _money(balance),
            'balance_status': status,
            'taken_at':       _iso(taken_at),
            'age_hours':      (None if taken_at is None else round(
                (now - taken_at).total_seconds() / 3600.0, 1)),
            'outgoing_omni':               _money(omni_total),
            'outgoing_at_bank':            _money(bank_total),
            'outgoing_unconfirmed_count':  bank_count,
            'projected_floor':             _money(floor),
            'note':           _note(status, taken_at, now=now),
        }

        name = company_name(account)
        group = group_index.get(name)
        if group is None:
            group = {'company': name, 'subtotal_balance': None, 'accounts': []}
            group_index[name] = group
            groups.append(group)
        group['accounts'].append(row)

    # A group subtotal is null the moment ANY account in it is unknown — a
    # partial subtotal presented as the group's cash is a wrong number.
    for group in groups:
        figures = [r['balance'] for r in group['accounts']]
        group['subtotal_balance'] = (
            None if any(f is None for f in figures)
            else _money(sum((Decimal(f) for f in figures), Decimal('0.00'))))

    total_n = len(accounts)
    if total_balance is None:
        worst = worst if accounts else NEVER_READ
        sentence = (f'No balance could be read from any of the '
                    f'{_word(total_n)} accounts.')
    else:
        # fresh_count, not read_count. An account that fell back to an older
        # good reading has a figure but is not up to date, and the home card
        # now says so. Leaving this on read_count meant the card warned "3 of
        # 6 are showing an older balance — tap to see which", and the screen
        # he tapped through to answered "6 of 6 accounts read". Two Alpha
        # Direct screens contradicting each other about the same six accounts
        # is how a correct figure loses its authority.
        sentence = (f'Cash in the {_word(total_n)} accounts: '
                    f'BWP {Decimal(_money(total_balance)):,.2f} — '
                    f'{fresh_count} of {total_n} accounts up to date')

    # What the CFO actually asked for, in his words: "balances - pending
    # payments = theoretical balance". The floor is null whenever the balance
    # is, because you cannot subtract from a figure you do not have.
    total_outgoing = total_out_omni + total_out_bank
    total_floor = (None if total_balance is None
                   else total_balance - total_outgoing)

    return {
        'headline': {
            'total_balance':  _money(total_balance),
            # Going out, split the way the CFO reads it: raised in Omni and
            # waiting on a person, versus already sitting in the bank's own
            # queue. Both leave the account; only one is still ours to stop.
            'total_outgoing_omni':    _money(total_out_omni),
            'total_outgoing_at_bank': _money(total_out_bank),
            'total_outgoing':         _money(total_outgoing),
            'outgoing_unconfirmed_count': total_out_count,
            # "Theoretical balance" in his words. It is a FLOOR, not a
            # forecast: it subtracts everything committed and adds nothing
            # coming in, because Omni does not know what is coming in.
            'total_projected_floor':  _money(total_floor),
            'accounts_read':  read_count,
            'accounts_fresh': fresh_count,
            'accounts_total': total_n,
            'worst_status':   worst,
            'taken_at':       _iso(oldest_reading),
            'sentence':       sentence,
        },
        'groups': groups,
    }
