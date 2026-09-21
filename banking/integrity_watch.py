"""
banking/integrity_watch.py

Does Omni's copy of the bank agree with the bank? CFO directive 2026-09-20.

WHY THIS EXISTS. Between 2026-06 and 2026-09-11 the daily statement pull
re-imported the same seven-day window every morning into a NEW BankStatement
row, so one day's transactions were stored up to seven times. By 2026-09-20,
20,779 of 25,871 bank lines (80.4%) were copies. Reconciliation could never
match them, because six in every seven had no payment to match to. The guard
that stopped it (fnb/statements.py, existing_keys, 2026-09-12) fixed the cause
but nobody was told the damage was there, and nobody would have been told if
it started again.

So this is not a bank-connection check — fnb/health_watch.py already answers
"is the link up". This answers the question that went unasked for three months:
"is what we hold still the same as what the bank says?"

THE ONE RULE IT ENFORCES. FNB is the source of truth. Every check below
compares Omni against the bank's own answer, or against a shape that can only
occur when Omni has drifted from it. Nothing is judged against Omni alone —
that is exactly how the duplicates survived.

WHO GETS TOLD. Kago Tshutlhedi, Pako Kago and Keetile Mokhendo (CFO cc'd, as
always). Only when a check FAILS — a daily "all clear" email becomes a filter
rule in Outlook within a fortnight, which is how the last watcher died
(see infra/cron/fnb-health-watch.cron).

WHICH AI. core.ai_assist.reasoning_complete — never gemini_complete on its own
(house rule). It walks local Ollama (free) → DeepSeek → GEMINI → OpenAI, so a
DeepSeek outage falls through to Gemini rather than losing the alert. If every
engine is down, _plain_summary() is sent verbatim: the AI only ever rewrites
the findings, it never produces them, so no finding can be lost to an AI
failure.

SAFETY. The AI only ever sees counts, dates and account numbers masked to the
last four digits — never a transaction description, a counterparty or an
amount. That last one is load-bearing and was briefly untrue: a finding once
carried "the balance moved by BWP 5,000.00", which went verbatim to DeepSeek,
Gemini and OpenAI and bypassed the house redactor. Findings state WHAT moved
and WHERE to look, never HOW MUCH. Same posture as fnb/ai_health.py, and the
assembled prompt is run through core.ai_assist.is_safe_for_ai().safe as a
second net before it leaves.
"""
from __future__ import annotations

import datetime
from decimal import Decimal
import logging
import uuid
from collections import defaultdict

from django.conf import settings
from django.db.models import Count
from django.utils import timezone

from core.ai_assist import DeepSeekUnavailable, is_safe_for_ai, reasoning_complete
from core.notifications import send_with_cfo_cc

log = logging.getLogger(__name__)

# Kago, Pako, Keetile — CFO's named fixers for bank/payment exceptions
# (2026-09-20). The CFO is cc'd automatically by send_with_cfo_cc.
_DEFAULT_RECIPIENTS = [
    'ktshutlhedi@alphadirect.co.bw',   # Kago Tshutlhedi
    'pkago@alphadirect.co.bw',         # Pako Kago
    'kmokhendo@alphadirect.co.bw',     # Keetile Mokhendo
]

# How far back to re-ask the bank. Seven days covers the statement pull's own
# window, so any day the cron could still be rewriting is re-checked.
_TIE_DAYS = 7


# A finding that starts with this is the check reporting its OWN failure, not
# a statement about the bank. The two must never be confused — see
# _plain_summary.
COULD_NOT_RUN = 'This check could not run:'


def _mask(account_number: str) -> str:
    digits = ''.join(c for c in (account_number or '') if c.isdigit())
    return f'…{digits[-4:]}' if len(digits) >= 4 else '…????'


# ---------------------------------------------------------------------------
# The checks. Each returns a list of plain-English finding strings.
# ---------------------------------------------------------------------------

def check_duplicate_lines() -> list[str]:
    """The 2026-09 defect itself: one transaction stored under two statements.

    A genuine repeat (two identical debit orders on one day) arrives inside a
    SINGLE statement, so it is not a finding. The same identity appearing in
    more than one statement can only mean the same day was imported twice.
    """
    from .models import BankStatementLine

    # Bounded to the same window as every other check. Unbounded, it rescans
    # all history and re-reports the SAME old duplicates every morning for
    # ever — which is the standing-backlog noise this module deleted its own
    # stuck-batch check to avoid. Bounded, it reports a NEW re-import, which
    # is what it is for. (The 20,779 historical duplicates were cleaned on
    # 20-Sep-2026; a future backlog must not turn this into a daily nag.)
    since = timezone.localdate() - datetime.timedelta(days=_TIE_DAYS)
    rows = (BankStatementLine.objects
            .filter(transaction_date__gte=since)
            .values('statement__bank_account__account_number',
                    'transaction_date', 'amount', 'description', 'reference')
            .annotate(stmts=Count('statement_id', distinct=True))
            .filter(stmts__gt=1))

    per_account: dict[str, int] = defaultdict(int)
    for r in rows:
        per_account[r['statement__bank_account__account_number']] += 1

    return [
        f'Account {_mask(acct)}: {n} transactions are stored under more than '
        f'one statement — the same day has been imported twice.'
        for acct, n in sorted(per_account.items(), key=lambda kv: -kv[1])
    ]


def check_balance_moved_without_transactions() -> list[str]:
    """The balance changed but we stored no transactions to explain it.

    A statement with zero lines is NOT itself a fault — a quiet Sunday is zero
    lines, and the first draft of this check flagged 13-Sep and 20-Sep-2026,
    both Sundays, on its very first live run. An alert that fires on ordinary
    weekends is the alert everyone filters, which is precisely how the previous
    watcher died (infra/cron/fnb-health-watch.cron).

    What cannot happen honestly is the balance MOVING with nothing stored to
    account for the movement. Money left or arrived and Omni holds no line for
    it. That is a contradiction on its own terms — no weekday calendar, no
    holiday table and no second call to the bank needed to judge it.
    """
    from .models import BankAccount, BankStatement

    since = timezone.localdate() - datetime.timedelta(days=_TIE_DAYS)
    findings: list[str] = []

    for ba in BankAccount.objects.filter(is_active=True, statements__isnull=False).distinct():
        recent = list(BankStatement.objects
                      .filter(bank_account=ba, statement_date__gte=since)
                      .order_by('statement_date', 'created_at')
                      .values('statement_date', 'closing_balance', 'line_count'))
        for prev, cur in zip(recent, recent[1:]):
            if cur['line_count']:
                continue
            if cur['closing_balance'] == prev['closing_balance']:
                continue                      # nothing moved — genuinely quiet
            # The AMOUNT is deliberately not in the finding. Findings are sent
            # verbatim to the AI (Ollama -> DeepSeek -> Gemini -> OpenAI) to be
            # rewritten, and this module's safety note promises the AI sees no
            # amounts. Putting "BWP 5,000.00" here quietly broke that promise
            # and bypassed the house redactor. The date and the account are
            # enough to find the movement in Omni; whoever opens it sees the
            # figure there.
            findings.append(
                f'Account {_mask(ba.account_number)}: on {cur["statement_date"]} '
                f'the balance moved but Omni stored no transactions for that '
                f'day. Something went in or out that we have no record of.'
            )
    return findings


# NO STUCK-BATCH CHECK HERE, DELIBERATELY.
#
# The first draft of this module had one. `fnb/health_watch.py:286` already
# does it: same 48-hour threshold, same three non-terminal statuses, same
# recipients (Kago, Pako, CFO), emailed at 06:30 — and it keys on WHICH
# batches are stuck (`FNBHealthAlertState.stuck_keys`) so it alerts once per
# newly-stuck batch instead of re-reporting the standing backlog.
#
# Ours had no such state. It would have sent a second email half an hour
# later, about the same 29 batches, every morning, for ever. That is precisely
# how the previous watcher got switched off as noise — the failure this
# module's own docstring warns about, committed by the module itself.
#
# If the stuck-batch threshold or wording needs to change, change it in
# fnb/health_watch.py. One alarm per condition.


def check_bank_tie(days: int = _TIE_DAYS) -> list[str]:
    """Ask FNB, per account per day, how many transactions it holds.

    This is the check that would have caught the duplicates on day one: it
    never trusts Omni's own numbers. A day where FNB answers and the counts
    differ is a real finding. A day FNB will not answer is reported as a
    separate, milder line — we cannot check what the bank will not tell us,
    and saying "agreed" there would be a lie.
    """
    from fnb.client import FNBClient, FNBAPIError, FNBNotConfigured
    from fnb.endpoints import STATEMENT_RETRIEVE
    from fnb.models import FNBSyncLog
    from .models import BankAccount, BankStatementLine

    findings: list[str] = []
    today = timezone.localdate()
    accounts = (BankAccount.objects
                .filter(is_active=True, statements__isnull=False)
                .exclude(account_number__in=('', '0'))
                .distinct())

    for ba in accounts:
        mismatched: list[str] = []
        unanswered = 0
        for back in range(1, days + 1):
            d = today - datetime.timedelta(days=back)
            try:
                resp = FNBClient().post(
                    STATEMENT_RETRIEVE,
                    service=FNBSyncLog.Service.STATEMENT,
                    json_body={'accountId': ba.account_number,
                               'fromDate': d.isoformat(),
                               'toDate': d.isoformat()},
                    request_summary=f'integrity tie {_mask(ba.account_number)} {d}',
                    extra_headers={'X-Request-ID': str(uuid.uuid4())},
                    timeout=90,
                )
            except (FNBAPIError, FNBNotConfigured, OSError) as exc:
                # Masked: the module promises a full account number never
                # leaves, and the application log is somewhere it leaves to.
                log.info('integrity tie: %s %s unanswered (%s)',
                         _mask(ba.account_number), d, type(exc).__name__)
                unanswered += 1
                continue

            statement = (resp.json or {}).get('statement') or {}
            bank_n = len(statement.get('entry') or [])
            held_n = BankStatementLine.objects.filter(
                statement__bank_account=ba, transaction_date=d).count()
            if bank_n != held_n:
                mismatched.append(f'{d} (bank {bank_n}, Omni {held_n})')

        if mismatched:
            findings.append(
                f'Account {_mask(ba.account_number)} does not agree with the '
                f'bank on {len(mismatched)} of the last {days} days: '
                + '; '.join(mismatched[:5])
                + ('…' if len(mismatched) > 5 else '')
            )
        # ANY unanswered day is reported, not only a total blackout. The first
        # version fired on `unanswered == days`, so six silent days out of
        # seven printed "Omni vs the bank: ok" and the run went on to say
        # "Omni agrees with the bank. No email sent." — agreement it had never
        # established. Unknown is not the same as checked, and the count says
        # which it is.
        if unanswered:
            checked = days - unanswered
            # COULD_NOT_RUN prefix: this says what was NOT measured. Without
            # it the finding lands in `measured_disagreement` and the email
            # opens "Omni's copy of the bank does not agree with the bank" —
            # a claim about figures nobody compared, off one flaky FNB call.
            findings.append(
                f'{COULD_NOT_RUN} FNB answered for account '
                f'{_mask(ba.account_number)} on only {checked} of the last '
                f'{days} days, so the other {unanswered} could not be checked '
                f'either way.'
            )

    if not accounts:
        findings.append(
            f'{COULD_NOT_RUN} no bank account is set up to be checked against '
            'FNB, so nothing was compared. Somebody has to switch the '
            'accounts on.'
        )
    return findings


def check_claims_account_is_oversubscribed() -> list[str]:
    """More claims queued for approval than the claims account can pay.

    CFO /recc 20-Sep-2026: "tell me in the morning email", so he can move money
    from Call before he starts authorising rather than finding out when a
    payment bounces at the bank. On the day he asked, Claims held BWP
    256,229.74 against BWP 419,199.21 of claims waiting on his phone — short by
    roughly P163,000 — and nothing anywhere said so.

    Reuses `build_balances_payload`, which is what the screen and the phone
    card both read. A second implementation of "what is going out" would drift
    from them, and the whole point is that the CFO's screens agree.

    Only reports an account whose balance we actually READ. An account we could
    not reach has an unknown balance, and "unknown minus committed" is not a
    shortfall — reporting one would be inventing a number, which is the failure
    this whole feature has already had three times.

    No amounts: these findings go verbatim to an external model for rewriting.
    The shortfall is described, never quantified.
    """
    from banking.balances import build_balances_payload

    payload = build_balances_payload()
    findings: list[str] = []
    for group in payload.get('groups', []):
        for acct in group.get('accounts', []):
            # Gate on the STATUS, not on the floor being non-null. A FAILED
            # read falls back to the last GOOD figure (balances.py
            # resolve_account_state), and a reading older than FRESH_HOURS
            # comes back `stale` — both hand out a real number, so
            # `projected_floor` is populated from a balance that is not
            # today's. Subtracting today's commitments from last week's
            # balance invents a shortfall: the Claims account 400s regularly,
            # so this would have emailed four people every morning, off a
            # figure getting older each day, about an account the CFO had
            # already funded. `ok` is the only status that means read AND
            # current.
            if acct.get('balance_status') != 'ok':
                continue
            floor = acct.get('projected_floor')
            if floor is None or Decimal(str(floor)) >= 0:
                continue
            findings.append(
                f"{acct.get('label', 'An account')}: more payments are approved "
                f"or waiting for authorisation than the account currently holds. "
                f"Move funds across before authorising, or some of them will be "
                f"refused at the bank.")
    return findings


CHECKS = (
    ('Duplicate bank lines',        check_duplicate_lines),
    ('Balance moved with no transactions', check_balance_moved_without_transactions),
    ('Omni vs the bank',            check_bank_tie),
    ('An account cannot cover what is queued', check_claims_account_is_oversubscribed),
)


def run_checks(*, skip_bank_tie: bool = False) -> list[tuple[str, list[str]]]:
    """Run every check. A check that throws is reported, never swallowed —
    a silent watcher is worse than none, which is the whole lesson here."""
    out: list[tuple[str, list[str]]] = []
    for name, fn in CHECKS:
        if skip_bank_tie and fn is check_bank_tie:
            continue
        try:
            out.append((name, fn()))
        except Exception as exc:                       # noqa: BLE001
            log.exception('integrity check %r failed', name)
            out.append((name, [f'{COULD_NOT_RUN} '
                               f'{type(exc).__name__}. Treat as unchecked.']))
    return out


# ---------------------------------------------------------------------------
# The email
# ---------------------------------------------------------------------------

def _plain_summary(results: list[tuple[str, list[str]]]) -> str:
    # The opening line must describe what ACTUALLY failed. The first version
    # said "Omni does not agree with the bank" unconditionally, and on its
    # first live run that headline sat above a finding that was only about
    # unconfirmed payment batches — the bank data itself was fine. The AI then
    # faithfully repeated the wrong headline. A finance team that reads one
    # false headline stops reading the rest.
    # A check that ERRORED is excluded here. It produces a finding string, so
    # without this it lands in `failed` and flips the headline straight back to
    # "the bank figures disagree" — asserting a disagreement nobody measured,
    # which is the very defect this conditional was written to fix, arriving
    # again through the error path.
    # And a check that is not ABOUT the bank data is excluded too. The
    # oversubscription check reports that an account cannot cover what is
    # queued against it — Omni and FNB may agree perfectly on every figure
    # involved. Letting it into this list flipped the headline to "Omni's copy
    # of the bank does not agree with the bank" on a morning when the bank data
    # was fine: the exact regression described above, arriving through a new
    # check instead of the error path. Membership here is a claim about what
    # was MEASURED, so every future check has to declare itself.
    BANK_TIE_CHECKS = {'Duplicate bank lines',
                       'Balance moved with no transactions',
                       'Omni vs the bank'}
    measured_disagreement = [
        name for name, findings in results
        if name in BANK_TIE_CHECKS
        and any(not f.startswith(COULD_NOT_RUN) for f in findings)
    ]
    if measured_disagreement:
        opening = "Omni's copy of the bank does not agree with the bank."
    else:
        opening = 'The daily bank check found something that needs a person.'

    lines = [opening, '']
    for name, findings in results:
        if findings:
            lines.append(f'{name}:')
            lines.extend(f'  - {f}' for f in findings)
            lines.append('')
    lines.append('Please check these against FNB and correct them in Omni.')
    lines.append('')
    lines.append('Regards,')
    lines.append('Aria (Omni)')
    return '\n'.join(lines)


def compose_email(results: list[tuple[str, list[str]]]) -> tuple[str, str]:
    """(subject, body). DeepSeek writes the body; the plain summary is the
    fallback and is ALSO what the AI is asked to rewrite, so a DeepSeek outage
    never loses a finding."""
    n = sum(len(f) for _, f in results)
    subject = f'Bank data check: {n} thing{"" if n == 1 else "s"} to look at'
    fallback = _plain_summary(results)

    prompt = (
        'You are writing a short email to three finance staff (Kago, Pako and '
        'Keetile) about a daily automatic check on whether Omni\'s copy of the '
        'bank statements still matches what FNB says.\n\n'
        'These are the findings:\n\n' + fallback + '\n\n'
        'Rewrite this as a clear, calm email under 180 words. Plain English, no '
        'jargon, no technical terms, no HTTP codes. Keep EVERY number and every '
        'account reference exactly as given — do not round, drop or invent any. '
        'Say plainly what each finding means and what to check. Close with '
        '"Regards, Aria (Omni)".\n\n'
        'Report ONLY the findings listed above. Do not add a claim that is not '
        'there — in particular, if nothing above says the bank figures '
        'disagree, do not say they do. Do not write a Subject line.'
    )
    # `.safe`, NOT the report itself: is_safe_for_ai returns a SafetyReport
    # dataclass (core/ai_assist.py:87) which is ALWAYS truthy, so `if not
    # is_safe_for_ai(prompt)` could never fire — a gate that looked like a
    # gate and guarded nothing.
    # Deliberately NOT using report.redacted_text: the redactor rewrites
    # amounts, so "BWP 1,727,536.87" would reach the finance team as
    # "[AMOUNT-REDACTED]" and the email would say nothing useful. The prompt
    # already carries only counts, dates and masked account numbers.
    if not is_safe_for_ai(prompt).safe:
        log.warning('integrity watch: prompt failed the PII gate; sending plain')
        return subject, fallback
    try:
        body = reasoning_complete(prompt, max_tokens=420,
                                  feature='bank_integrity_watch')
        return subject, (body or '').strip() or fallback
    except DeepSeekUnavailable as exc:
        # Not swallowed: the findings still go out verbatim below, but the
        # outage itself must leave a trace or "the email reads oddly today"
        # has no explanation next week.
        log.warning('integrity watch: every AI engine unavailable (%s) — '
                    'sending the plain summary', exc)
        return subject, fallback


def run(*, dry_run: bool = False, skip_bank_tie: bool = False) -> dict:
    """Run the checks and email the fixers if anything failed."""
    results = run_checks(skip_bank_tie=skip_bank_tie)
    n = sum(len(f) for _, f in results)
    out = {'findings': n, 'results': results, 'emailed': False}

    if n == 0:
        out['action'] = 'all clear — no email sent'
        return out

    # compose_email only catches DeepSeekUnavailable. ANY other failure in the
    # AI layer — a bad response shape, a network error raised as something
    # else — would otherwise take the whole run down and lose the findings
    # entirely. The findings are the product; the rewrite is a courtesy.
    try:
        subject, body = compose_email(results)
    except Exception as exc:                           # noqa: BLE001
        log.warning('integrity watch: could not compose the email (%s) — '
                    'sending the plain summary', exc)
        subject = f'Bank data check: {n} thing{"" if n == 1 else "s"} to look at'
        body = _plain_summary(results)
    out['subject'] = subject
    out['body'] = body
    if dry_run:
        out['action'] = 'dry run — not sent'
        return out

    recipients = list(getattr(settings, 'BANK_INTEGRITY_RECIPIENTS',
                              _DEFAULT_RECIPIENTS))
    try:
        # send_with_cfo_cc returns Django's send() count and gives back 0 on a
        # SILENT failure — it does not raise. Recording True regardless would
        # log "emailed" for an email nobody received.
        sent = send_with_cfo_cc(subject=subject, body=body, to=recipients,
                                from_email='pganesharajah@alphadirect.co.bw')
        out['emailed'] = bool(sent)
        out['action'] = (f'emailed {len(recipients)} recipients' if sent
                         else 'email failed: the mail server accepted nothing')
    except Exception as exc:                           # noqa: BLE001
        log.warning('integrity watch: email failed (%s)', exc)
        out['action'] = f'email failed: {type(exc).__name__}'
    return out
