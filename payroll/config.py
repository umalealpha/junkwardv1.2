"""
payroll/config.py — the versioned, audited config store for the payroll
automation pack (Shared Contract v1, 2026-09-12: "no hardcoded values").

Every business value named in prompts 01-03 (account codes, component codes,
period-status names, proration flags, working-days divisor, ATR defaults) is
a row in payroll.models.PayrollSetting, seeded here with its INITIAL value.
Finance/admin edits the row via the admin — no deploy needed. Code must never
compare against the literal; it reads the named key through get_setting/
get_bool.
"""
from __future__ import annotations

# Initial values for every config key this pack introduces. Seeded lazily
# (get_or_create) on first read so an upgrade never needs a data migration —
# and a value already edited by Finance is never overwritten.
#
# Only keys actually READ somewhere are listed here (coordinator review,
# 2026-09-12 removed four that were never consulted by any code path:
# payroll.active_predicate.use_effective_date, staff_loan.
# repayment_component_code, atr.effective_date_on_acceptance, and the
# get_decimal() helper below — a config key nobody reads is worse than no
# key, it looks like a lever that does something).
_DEFAULTS: dict[str, str] = {
    # Prompt 01 — staff loan repayment. CFO decision 2026-09-12: the
    # receivable account is 121010 — this is the ONE place that number lives;
    # staff_loans/services.py reads it from here too (see
    # staff_loan_receivable_account_code()) rather than keeping its own
    # fallback, so the two paths can never drift apart again.
    #
    # It was briefly set to 121000 earlier the same day. That is WRONG and the
    # mistake is worth recording: in Omni's live chart 121000 is "Provision for
    # Bad Debts - ECL", while 121010 is the account actually named "Staff Loan"
    # (with "124004 Interest From Staff Loan" beside it). _ensure_account()
    # resolves purely by code and returns whatever already holds it, so the
    # fallback would have silently handed back the bad-debt provision and every
    # payslip loan deduction would have posted there. The LOAN_REPAYMENT
    # component's posting account was still blank on prod, so the first payroll
    # run after that deploy would have bound it. Caught by the /fabe gate,
    # verified by querying prod, confirmed by the CFO.
    'staff_loan.receivable_account_code': '121010',
    'staff_loan.write_only_period_status': 'open',
    'payroll.loan_recompute_on_apply': 'true',
    # Prompt 02 — joiner/leaver active-period rule
    'payroll.prorate_partial_month': 'false',
    'payroll.working_days_per_month': '24',
    'payroll.active_period_rule': 'true',
    # Prompt 03 — ATR seeds payroll
    'atr.default_employment_type': 'permanent',
    'atr.store_reference_on_employee': 'true',
    'recruitment.atr_seed_payroll': 'true',
    # B13 (prompt 08) — monthly orchestration. The whole sequence is config
    # so Finance can reorder or drop a step without a deploy; an unknown name
    # in the list is REPORTED as a failed step, never quietly skipped.
    'payroll.monthly_orchestration': 'false',   # OFF until proven per entity
    'payroll.monthly_sequence': ('joiners_leavers,recurring_incentives,loans,'
                                 'advances,feeds,commission_verify,recompute'),
    # LOCK and POST stay human under dual sign-off. These are here so the
    # intent is visible and audited — switching either ON makes the
    # orchestrator REFUSE to run rather than pretend the lever works.
    'payroll.auto_lock': 'false',
    'payroll.auto_post': 'false',
    # Finance's stated commission-feed population. commissions/verify.py
    # compares this against what the feed actually implements and reports a
    # mismatch instead of widening either side to match.
    'commission.payroll_group_filter': 'pays_via == PAYROLL',
    # Leave-pay feed GO-LIVE cut-off (CFO decision 2026-09-13).
    #
    # Before Omni fed them, an approved leave encashment was settled DIRECTLY by
    # Finance — the money had already left the bank under a human's two-factor.
    # Switching the AUTO-LEAVEPAY feed on without a cut-off would put every one
    # of those already-settled encashments onto a payslip and the person would
    # be paid a second time.
    #
    # An encashment whose Finance approval fell ON OR BEFORE this date is never
    # fed. ISO yyyy-mm-dd, Botswana time. Blank means "no cut-off" — everything
    # is eligible — which is only right on a system that never settled one
    # outside payroll, so it is NOT the default here.
    'leave_pay.golive_date': '2026-09-13',
}


def get_setting(key: str, default: str | None = None) -> str:
    """Raw string value for `key`. Seeds the row from _DEFAULTS on first
    read if it does not exist yet. `default` is only used for a key this
    module does not know about (should not happen in normal use)."""
    from .models import PayrollSetting
    initial = _DEFAULTS.get(key, default if default is not None else '')
    row, _ = PayrollSetting.objects.get_or_create(
        key=key, defaults={'value': initial},
    )
    return row.value


def get_bool(key: str, default: bool = False) -> bool:
    raw = get_setting(key, 'true' if default else 'false').strip().lower()
    return raw in ('1', 'true', 'yes', 'on')


def get_date(key: str, default: str = ''):
    """The value of `key` read as a date, or None when it is blank/unreadable.

    A blank is a real answer — "no cut-off" — not an error, so it comes back as
    None rather than raising. An unreadable value also comes back as None and
    the caller says so; a half-parsed cut-off silently letting rows through
    would be worse than no cut-off at all.
    """
    import datetime
    raw = (get_setting(key, default) or '').strip()
    if not raw:
        return None
    try:
        return datetime.date.fromisoformat(raw)
    except ValueError:
        return None


def get_int(key: str, default: int = 0) -> int:
    try:
        return int(get_setting(key, str(default)))
    except (TypeError, ValueError):
        return default
