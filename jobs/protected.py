"""
jobs/protected.py — which jobs the switch must NEVER turn off.

Set in CODE, not the database, so no edit on the dashboard and no bug in it can
unlock one. Anything writing an audit trail, moving money or data, running a
backup or a watchdog, OR whose SILENCE is the only sign that something else
broke, is protected.

The base rule ("protected if not merely a send") was applied by the off-line
build. This file layers on Fable 5.1's safety review of 2026-09-09, which caught
sends whose silence hides a fault — those are moved into PROTECTED by name here.
"""

# Fault-hiding "sends": an empty or missing one is the only human signal that a
# pipeline broke. Fable flagged each of these explicitly.
FAULT_HIDING = {
    'payment-daily-digest',    # missing digest = payments stopped or doubled
    'consolidated-ops-digest', # may carry the watchdogs' findings
    'omni-outbound-digest',    # the only daily proof customer mail actually left
    'exec-provider-dashboard', # goes to EXTERNAL providers — their money statement
    'quote-expiry-reminder',   # goes to CUSTOMERS — silence = lapsed premium
    'monthly-feedback',        # creates the month's HR records, not just a nag
    'manager-objectives',      # writes the weekly objectives other jobs compute on
}

# Never a switch at all — one-offs, OS jobs, dead .DISABLED files. Excluded from
# the board entirely (import_cron_state drops them).
NOT_A_SWITCH = {
    'e2scrub_all', 'sysstat',                 # Ubuntu package jobs, not Omni
    'frontend-swap-tonight',                  # one-shot deploy flip
    'hours-correction-oneoff',                # one-off, mutates on re-run
    'frozen-screen-shadow',                   # shadow twin of screen-integrity
}

# The base safety set — everything that is plainly not "just a send".
_BASE_PROTECTED = {
    'alpha-finance-backup', 'backup-watch', 'omni-watchdog', 'row-watchdog',
    'timedoctor-pull', 'timedoctor-token-check', 'timedoctor-healthcheck',
    'td-enforce', 'td-reconcile', 'realpay-reconcile', 'fnb-sync',
    'graphite-claims-sync', 'screen-integrity', 'duplicate-account-watch',
    'finance-monitoring', 'premium-refund-import', 'pin-access',
    'compliance-brain', 'dpo-checklist', 'purge-old-cvs', 'aware-reports',
    'staff-loan-rate', 'staff-loan-repayments', 'presummarise-claims',
    'stuck-work-sweep', 'exceptions-report', 'omni-recurring-incentives',
    'omni-mail-auto-reply', 'payroll-signoff-chase', 'fnb-email-autoclose',
    'workforce-offboarding', 'nbfira', 'bug-triage',
}

PROTECTED = _BASE_PROTECTED | FAULT_HIDING


def is_protected(name: str) -> bool:
    return name in PROTECTED
