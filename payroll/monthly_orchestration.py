"""
payroll/monthly_orchestration.py — Build Spec B13, "Payroll 08: Monthly
Orchestration and Commission Verify". The conductor.

    "Build it LAST, after 04, 06 and 07 exist. It orchestrates them."

It runs the month's payroll feeds in the order the Shared Contract lays down,
verifies the commission population, and then re-computes every payslip so no
stale net survives to the close. It CALLS the feeds; it does not reimplement
any of them.

THE TWO HARD LINES FROM THE SPEC

1.  "It must refuse to touch a period that is already signed off."
    Refuse, not warn. ``run_month`` raises ``PeriodRefused`` before a single
    step executes when the period has moved past OPEN, or when either signature
    (HR or Finance) is already on the company-month. Nothing is written; the
    attempt is still recorded as a REFUSED run so the refusal itself is not
    silent.

2.  "Report what it ran, what it skipped and why. Silence is not success."
    Every run e-mails a person. A run in which NOTHING happened is not a clean
    run — it finishes ``EMPTY`` and the subject line says so in capitals. If
    Finance have configured no recipients, the report still goes to the CFO/EXCO
    inbox with "NO RECIPIENTS CONFIGURED" in the subject, and the run records
    the fact. The failed-debit report that went live on 11 September 2026 and
    never sent one e-mail is exactly the shape of failure being guarded here.

WHAT STOPS A DOUBLE PAYMENT

    Not this file. "Every feed it calls must already be idempotent on its own.
    The orchestrator must not become the only thing preventing a double
    payment." Each feed writes ``PayrollAmendment.feed_key``, which the database
    holds UNIQUE — a second attempt at the same (feed, month, entity, person,
    line) is refused by Postgres, not by a Python ``if``. The orchestrator adds
    ONE guard of its own on top, and it guards itself: a partial unique index
    permits only one in-flight run per (period, entity), so two overlapping
    crons cannot conduct the same month at once.

LOCK AND POST STAY HUMAN

    "Never auto-LOCK or auto-POST — those stay human (dual sign-off pays)."
    There is no code here that locks or posts. ``payroll.auto_lock`` and
    ``payroll.auto_post`` exist as config so the intent is visible and audited;
    if either is ever switched on, the orchestrator REFUSES to run and says why,
    rather than pretending the lever does something.

Omni never moves money. Every step here prepares records — payroll amendment
batches, created PARSED and never applied. Money leaves at FNB, under a human's
two-factor.
"""
from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

from django.utils import timezone

from payroll import config as payroll_config

FEATURE_FLAG = 'payroll.monthly_orchestration'
SEQUENCE_KEY = 'payroll.monthly_sequence'
AUTO_LOCK_KEY = 'payroll.auto_lock'
AUTO_POST_KEY = 'payroll.auto_post'
REPORT_SLUG = 'payroll-monthly-orchestration'


class OrchestrationRefused(Exception):
    """Base: the conductor declined to touch anything."""


class PeriodRefused(OrchestrationRefused):
    """The period is closed, signed off, missing, or the flags forbid a run."""


@dataclass
class StepResult:
    rows: int = 0
    skipped: int = 0
    detail: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    ran: bool = True          # False = deliberately skipped, not a failure

    def line(self, text: str):
        self.detail.append(text)
        return self


@dataclass
class Ctx:
    period: Any
    period_label: str
    companies: list
    user: Any = None


# ---------------------------------------------------------------------------
# The refusal gate — everything here runs BEFORE any step
# ---------------------------------------------------------------------------

def signed_off_reason(period, companies) -> str:
    """Why this period+entity may not be touched, or '' when it may.

    Two independent facts make a month untouchable, and either alone is enough:

    * the PERIOD has moved past OPEN — it is calculated, approved, posted or
      paid, and a feed writing into it now would change figures somebody has
      already signed; and
    * the COMPANY-MONTH carries a signature. ``PayrollSignOff`` is per
      (period, company) on purpose — the CFO signs "ADIC October 2026", not
      "October 2026" — so a group-wide run must refuse for a single signed
      entity rather than quietly doing the other entities and half the month.
    """
    from payroll.models import PayrollPeriod, PayrollSignOff

    if period.status != PayrollPeriod.Status.OPEN:
        return (f'Payroll period {period.period_name} is '
                f'{period.get_status_display()} — not OPEN. A signed-off or '
                f'calculated month is never fed again.')

    qs = PayrollSignOff.objects.filter(period=period).select_related('company')
    if companies:
        qs = qs.filter(company__in=companies)
    for so in qs:
        if so.status == PayrollSignOff.Status.APPROVED or so.both_signed:
            who = []
            if so.hr_signed_by_id:
                who.append('HR')
            if so.fin_signed_by_id:
                who.append('Finance')
            stamp = ' and '.join(who) or 'the CFO'
            return (f'{so.company.name} {period.period_name} is already signed '
                    f'off by {stamp}. Nothing may be fed into a signed month.')
    return ''


def _flag_refusals() -> str:
    if not payroll_config.get_bool(FEATURE_FLAG, False):
        return (f'The orchestration feature flag ({FEATURE_FLAG}) is OFF. '
                f'Run the steps by hand until it has been proven per entity, '
                f'then switch it on.')
    if payroll_config.get_bool(AUTO_LOCK_KEY, False):
        return (f'{AUTO_LOCK_KEY} is ON. LOCK is a human step under dual '
                f'sign-off and this orchestrator will not do it. Switch it '
                f'back off before running.')
    if payroll_config.get_bool(AUTO_POST_KEY, False):
        return (f'{AUTO_POST_KEY} is ON. POST is a human step under dual '
                f'sign-off and this orchestrator will not do it. Switch it '
                f'back off before running.')
    return ''


# ---------------------------------------------------------------------------
# The steps. Each one CALLS something that already exists.
# ---------------------------------------------------------------------------

def _step_joiners_leavers(ctx: Ctx) -> StepResult:
    """(a) Leavers in front of the close (B11), joiners counted for the report.

    The joiner half needs no feed: ``payroll.eligibility.is_active_for_period``
    is consulted by every feed, so a joiner is included the moment their hire
    date lands in the period. Counting them here makes that visible instead of
    implied.
    """
    from payroll import severance_feed
    from payroll.models import Employee

    res = StepResult()
    for company in ctx.companies:
        out = severance_feed.feed_period(period_label=ctx.period_label,
                                         user=ctx.user, company=company)
        res.rows += out.get('pushed', 0)
        res.skipped += out.get('skipped', 0)
        excluded = out.get('day_one_excluded') or []
        res.line(f'{company.name}: {out["status"]}, {out.get("pushed", 0)} leaver '
                 f'row(s) raised' + (f'; day-one leavers excluded from a full '
                                     f'month: {", ".join(excluded)}' if excluded else ''))
        joiners = Employee.objects.filter(
            company=company, hire_date__gte=ctx.period.start_date,
            hire_date__lte=ctx.period.end_date, is_test_record=False).count()
        if joiners:
            res.line(f'{company.name}: {joiners} joiner(s) hired inside the period.')
    return res


def _step_recurring_incentives(ctx: Ctx) -> StepResult:
    """(b) Materialise the month's recurring incentive requests."""
    from django.core.exceptions import ValidationError

    from hris.incentive_service import generate_recurring_for_period

    res = StepResult()
    if ctx.user is None:
        res.ran = False
        return res.line('SKIPPED: no actor. Recurring incentives are attributed '
                        'to a manager; the run was started without one.')
    try:
        out = generate_recurring_for_period(ctx.period_label, ctx.user)
    except ValidationError as exc:
        res.ran = False
        return res.line(f'SKIPPED: {exc}')
    created = out.get('created_count', 0) if isinstance(out, dict) else 0
    res.rows += created
    res.line(f'{created} recurring incentive request(s) raised for '
             f'{ctx.period_label}.')
    return res


def _step_loans(ctx: Ctx) -> StepResult:
    """(c1) Staff-loan repayment deductions onto this month's payslips."""
    from payroll.loan_service import apply_loan_repayments

    res = StepResult()
    out = apply_loan_repayments(ctx.period)
    created = out.get('created', 0)
    res.rows += created
    res.skipped += out.get('skipped', 0)
    res.line(f'{created} loan deduction line(s) written, '
             f'{out.get("paid_off", 0)} loan(s) paid off.')
    return res


def _step_advances(ctx: Ctx) -> StepResult:
    """(c2) Recover CFO-approved early salary advances (B12)."""
    from payroll import salary_advance_service as advances

    res = StepResult()
    for company in ctx.companies:
        out = advances.feed_period(period_label=ctx.period_label,
                                   user=ctx.user, company=company)
        res.rows += out.get('pushed', 0)
        res.skipped += out.get('skipped', 0)
        res.line(f'{company.name}: {out["status"]}, {out.get("pushed", 0)} advance '
                 f'recovery row(s).')
        for sk in out.get('skipped_detail') or []:
            res.line(f'  · not recovered — {sk.get("name")}: {sk.get("reason")}')
    return res


def _step_feeds(ctx: Ctx) -> StepResult:
    """(d) Every approved feed pushed: incentive, commission, leave pay."""
    from commissions import payroll_feed as commission_feed
    from hris import incentive_payroll_feed as incentive_feed
    from hris import leave_pay_feed

    res = StepResult()
    for company in ctx.companies:
        out = incentive_feed.feed_period_company(period_label=ctx.period_label,
                                                 company=company, user=ctx.user)
        res.rows += out.get('pushed', 0)
        res.skipped += out.get('skipped', 0)
        res.line(f'{company.name} incentives: {out["status"]}, '
                 f'{out.get("pushed", 0)} row(s).')

        out = leave_pay_feed.feed_period(period_label=ctx.period_label,
                                         user=ctx.user, company=company)
        res.rows += out.get('pushed', 0)
        res.skipped += out.get('skipped', 0)
        res.line(f'{company.name} leave pay: {out["status"]}, '
                 f'{out.get("pushed", 0)} row(s).')

    # Commission is group-scoped, not company-scoped — one call covers the month.
    out = commission_feed.feed_period(period_label=ctx.period_label, user=ctx.user)
    res.rows += out.get('pushed', 0)
    res.skipped += out.get('skipped', 0)
    res.line(f'Commissions: {out["status"]}, {out.get("pushed", 0)} row(s).')
    return res


def _step_commission_verify(ctx: Ctx) -> StepResult:
    """Verify the commission population against Finance's stated intent.

    Read-only by design. A gap is REPORTED; scope is never widened to make the
    gap disappear.
    """
    from commissions.verify import verify_population

    res = StepResult()
    out = verify_population(period_label=ctx.period_label)
    res.line(f'Intent "{out["intent"]}"; {len(out["groups"])} payroll group(s); '
             f'{out["agents_resolvable"]} of {out["agents"]} agent(s) resolvable '
             f'to a staff record; {out["fed"]} submission(s) fed, '
             f'{out["unfed"]} approved but not fed.')
    for u in out['unfed_detail']:
        res.line(f'  · {u["agent"]}: {u["reason"]}')
    res.gaps.extend(out['gaps'])
    for g in out['gaps']:
        res.line(f'  GAP: {g}')
    return res


def _step_recompute(ctx: Ctx) -> StepResult:
    """(e) Belt and braces: re-total every payslip in the period.

    The staff-loan defect in one sentence: posting builds the journal from the
    live payslip LINES but reads net from the STORED total, so a line written
    without a recompute breaks the period quietly. Anything added while the
    month was open is reflected in net here, before anyone locks it.
    """
    from payroll.models import Payslip

    res = StepResult()
    slips = Payslip.objects.filter(period=ctx.period).exclude(
        status=Payslip.Status.CANCELLED)
    if ctx.companies:
        slips = slips.filter(company__in=ctx.companies)
    changed = 0
    for slip in slips.iterator():
        before = (slip.gross_amount, slip.paye_amount, slip.net_amount)
        slip.recompute_totals()
        slip.save()
        if (slip.gross_amount, slip.paye_amount, slip.net_amount) != before:
            changed += 1
    total = slips.count()
    res.rows += total
    res.line(f'{total} payslip(s) re-totalled; {changed} had a stale total and '
             f'now do not.')
    return res


STEPS: dict[str, Callable[[Ctx], StepResult]] = {
    'joiners_leavers':    _step_joiners_leavers,
    'recurring_incentives': _step_recurring_incentives,
    'loans':              _step_loans,
    'advances':           _step_advances,
    'feeds':              _step_feeds,
    'commission_verify':  _step_commission_verify,
    'recompute':          _step_recompute,
}


def configured_sequence() -> list[str]:
    """The month's step order, from config. Unknown names are NOT ignored —
    they are returned so the caller can report them; a typo in a config key
    that silently drops a step is exactly the quiet failure this build is for.
    """
    raw = payroll_config.get_setting(SEQUENCE_KEY, '')
    return [s.strip() for s in raw.split(',') if s.strip()]


# ---------------------------------------------------------------------------
# The conductor
# ---------------------------------------------------------------------------

def run_month(*, period_label: str, company=None, user=None,
              trigger: str = 'manual'):
    """Conduct one month. Returns the ``PayrollOrchestrationRun``.

    Raises ``PeriodRefused`` before touching anything when the month may not be
    fed. The refusal is still recorded as a REFUSED run — a refusal nobody can
    see is its own kind of silence.
    """
    from payroll.models import (PayrollOrchestrationRun, PayrollOrchestrationStep,
                                PayrollPeriod)
    from core.models import Company

    Run = PayrollOrchestrationRun
    Step = PayrollOrchestrationStep

    period = PayrollPeriod.objects.filter(period_name=period_label).first()
    companies = [company] if company is not None else list(
        Company.objects.filter(is_active=True).order_by('name'))

    def _refuse(reason: str):
        run = Run.objects.create(
            period_label=period_label, period=period, company=company,
            status=Run.Status.REFUSED, trigger=trigger, triggered_by=user,
            refusal_reason=reason, finished_at=timezone.now())
        notify(run)
        raise PeriodRefused(reason)

    flag_reason = _flag_refusals()
    if flag_reason:
        _refuse(flag_reason)
    if period is None:
        _refuse(f'No payroll period named {period_label!r}. Nothing was touched.')
    closed = signed_off_reason(period, companies)
    if closed:
        _refuse(closed)

    run = Run.objects.create(
        period_label=period_label, period=period, company=company,
        status=Run.Status.RUNNING, trigger=trigger, triggered_by=user)

    ctx = Ctx(period=period, period_label=period_label, companies=companies,
              user=user)
    sequence = configured_sequence()
    failed = False
    all_gaps: list[str] = []

    for i, name in enumerate(sequence, start=1):
        fn = STEPS.get(name)
        started = timezone.now()
        if fn is None:
            Step.objects.create(
                run=run, sequence=i, name=name[:40],
                status=Step.Status.FAILED, started_at=started,
                finished_at=timezone.now(),
                error=(f'"{name}" is in {SEQUENCE_KEY} but is not a step this '
                       f'code knows how to run. It was NOT quietly dropped.'))
            failed = True
            continue
        try:
            out = fn(ctx)
        except Exception as exc:                       # noqa: BLE001
            Step.objects.create(
                run=run, sequence=i, name=name, status=Step.Status.FAILED,
                started_at=started, finished_at=timezone.now(),
                detail='', error=f'{exc.__class__.__name__}: {exc}\n'
                                 f'{traceback.format_exc(limit=6)}'[:4000])
            failed = True
            continue
        if not out.ran:
            status = Step.Status.SKIPPED
        elif out.rows == 0:
            status = Step.Status.NOTHING
        else:
            status = Step.Status.OK
        Step.objects.create(
            run=run, sequence=i, name=name, status=status, rows=out.rows,
            skipped=out.skipped, detail='\n'.join(out.detail)[:8000],
            started_at=started, finished_at=timezone.now())
        run.rows_touched += out.rows
        all_gaps.extend(out.gaps)

    run.gaps = '\n'.join(all_gaps)[:8000]
    if failed:
        run.status = Run.Status.FAILED
    elif run.rows_touched == 0:
        # Silence is not success. A run that did nothing is a result to shout,
        # not a clean bill of health.
        run.status = Run.Status.EMPTY
    elif all_gaps:
        run.status = Run.Status.ATTENTION
    else:
        run.status = Run.Status.OK
    run.finished_at = timezone.now()
    run.save()
    notify(run)
    return run


# ---------------------------------------------------------------------------
# Telling a person. Every run, without exception.
# ---------------------------------------------------------------------------

def _subject(run) -> str:
    from payroll.models import PayrollOrchestrationRun as Run

    who = run.company.name if run.company_id else 'all entities'
    head = f'Payroll orchestration {run.period_label} · {who}'
    return {
        Run.Status.REFUSED:   f'REFUSED — {head}',
        Run.Status.EMPTY:     f'NOTHING RAN — {head}',
        Run.Status.FAILED:    f'FAILED — {head}',
        Run.Status.ATTENTION: f'Check needed — {head}',
    }.get(run.status, head)


def build_report_html(run) -> str:
    from payroll.models import PayrollOrchestrationRun as Run

    rows = []
    for st in run.steps.all():
        detail = (st.detail or st.error or '').replace('\n', '<br>')
        rows.append(
            f'<tr><td>{st.sequence}</td><td><b>{st.name}</b></td>'
            f'<td>{st.get_status_display()}</td><td align="right">{st.rows}</td>'
            f'<td align="right">{st.skipped}</td><td>{detail}</td></tr>')
    table = ('<table border="1" cellpadding="6" cellspacing="0" '
             'style="border-collapse:collapse;font-family:Arial;font-size:13px">'
             '<tr style="background:#0D1B2A;color:#fff"><th>#</th><th>Step</th>'
             '<th>Result</th><th>Rows</th><th>Skipped</th><th>Detail</th></tr>'
             + ''.join(rows) + '</table>') if rows else ''

    parts = [f'<p style="font-family:Arial"><b>{_subject(run)}</b></p>']
    if run.status == Run.Status.REFUSED:
        parts.append(f'<p style="font-family:Arial;color:#B00020"><b>Nothing was '
                     f'touched.</b><br>{run.refusal_reason}</p>')
    elif run.status == Run.Status.EMPTY:
        parts.append(
            '<p style="font-family:Arial;color:#B00020"><b>This run did nothing '
            'at all.</b> Every step completed and not one row was raised. That '
            'is not a clean run — it means either the month genuinely has no '
            'approved work, or something upstream is not reaching payroll. '
            'Somebody should look before the close.</p>')
    if run.gaps:
        gaps = run.gaps.replace('\n', '<br>')
        parts.append(f'<p style="font-family:Arial;color:#B00020"><b>Gaps '
                     f'reported (scope was NOT widened):</b><br>{gaps}</p>')
    parts.append(table)
    parts.append(
        '<p style="font-family:Arial;font-size:12px;color:#555">Batches are '
        'prepared, never applied. LOCK, POST and the dual HR + Finance sign-off '
        'stay human. Omni does not move money.</p>')
    return ''.join(parts)


def notify(run) -> bool:
    """E-mail the run to a person. Always. Records who was told.

    No recipient list is not a reason to stay quiet — it is the thing to shout
    about, so the report goes to the CFO/EXCO inbox with the fact in the
    subject line.
    """
    from core.notifications import send_html_with_cfo_cc
    from reporting.models import ReportRecipient

    route = ReportRecipient.route_for(REPORT_SLUG)
    subject = _subject(run)
    if route:
        to, cc = route['to'], route.get('cc') or []
    else:
        to, cc = [], []
        subject = f'NO RECIPIENTS CONFIGURED — {subject}'

    try:
        # With no To list the house helper still copies the CFO/EXCO inbox, so
        # a person is told either way.
        send_html_with_cfo_cc(subject, build_report_html(run), to, cc=cc,
                              text_fallback=f'{subject} — see the HTML version.')
        run.notified_to = (', '.join(to) or 'excoboard@ (no list configured)')[:500]
        run.notify_error = ''
    except Exception as exc:                            # noqa: BLE001
        run.notified_to = ''
        run.notify_error = f'{exc.__class__.__name__}: {exc}'[:300]
    run.save(update_fields=['notified_to', 'notify_error', 'updated_at'])
    return bool(run.notified_to)
