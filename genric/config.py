"""genric/config.py — the settings this pack needs, and the ONE still missing.

✅ ANSWERED — the pay window is 30 days (CFO, 14 Sep 2026)

The cancellations report could not be finished without knowing how many days a
policy may be unpaid before it becomes a cancellation candidate. That number is
not in the treaty, not in the MOU and not in the build prompt — the build prompt
itself listed it under "Confirm with the CFO: the pay-window length before
cancellation (the one number the documents do not fix)".

The CFO has now fixed it at **30 days**, to match the company's 30-day statement
terms. It was never guessed, and it still is not: 30 is the SEEDED value of a
row (``genric.models.GenricSetting``), not a literal any code path compares
against. It is changed on screen in Omni Admin → GENRIC Settings and the next
pack uses the new number — no deploy, no release, no server access.

NOTE ON WHO: Django admin needs ``is_staff``, and ``core/azure_auth.py`` grants
that only to ``OMNI_OWNER_EMAILS`` (the CFO). So today this is the CFO's screen,
not the wider Finance team's — say so rather than telling a Finance Manager to
go somewhere they cannot sign in. Widening it is an access decision, not a code
one. (Inherited from the PayrollSetting house pattern, which has the same shape.)

The safety valve is kept. Blank the row and every path that needs the window
raises ``GenricConfigurationError`` with the question in plain words, the rest of
the pack still generates, and the cancellations report comes back BLOCKED rather
than as an empty list — an empty list is indistinguishable from "nothing to
cancel", and the old manual process reporting this as nil is the exact failure
this build exists to fix.

Note this is NOT the treaty's 2-month grace (``constants.CANCELLATION_GRACE_
MONTHS``, Art. 10.4). The grace period is how long cover continues; the pay
window is how long after a failed collection the customer gets to pay before the
file becomes a cancellation candidate. The treaty fixes the first. The CFO has
now fixed the second.

WHY THE DJANGO SETTING IS GONE. ``GENRIC_UNPAID_DAYS_BEFORE_CANCELLATION`` used
to be read off ``django.conf.settings`` (an env var, no default). It is not read
anywhere any more, and it must not come back: two sources for one control is how
the screen and the report end up disagreeing about which number produced the
figures. The row is the single source of truth.
"""
from __future__ import annotations

from django.conf import settings

#: The plain-English question, so the API, the report and the log all say the
#: same thing and the CFO can answer it in one line.
UNPAID_DAYS_QUESTION = (
    'How many days may a policy stay unpaid, after a failed or missing '
    'collection, before it becomes a cancellation candidate?'
)

#: The setting key shown on the admin screen and named on the report.
UNPAID_DAYS_KEY = 'genric.unpaid_days_before_cancellation'

#: Seeded initial values. A row is created from this the first time it is read
#: and NEVER overwritten afterwards, so a value Finance has edited survives every
#: deploy. Code must never compare against the literal — it reads the key.
_DEFAULTS: dict[str, str] = {
    # CFO decision, 14 Sep 2026: 30 days, chosen to match the company's 30-day
    # statement terms. This is the seed for the row, not a fallback inside a
    # comparison — blank the row and the report goes BLOCKED rather than
    # quietly reverting to 30.
    UNPAID_DAYS_KEY: '30',
}

_DESCRIPTIONS: dict[str, str] = {
    UNPAID_DAYS_KEY: (
        'Days a policy may stay unpaid after a failed or missing collection '
        'before it is listed as a cancellation candidate. CFO: 30, to match '
        '30-day statement terms. Blank = the cancellations report is BLOCKED.'
    ),
}


class GenricConfigurationError(RuntimeError):
    """A required GENRIC setting is missing. Raised loudly, never defaulted."""


def get_setting(key: str, default: str | None = None) -> str:
    """Raw text value for ``key``, seeding the row from ``_DEFAULTS`` on first
    read. ``default`` is only used for a key this module does not know about."""
    from .models import GenricSetting
    initial = _DEFAULTS.get(key, default if default is not None else '')
    row, _ = GenricSetting.objects.get_or_create(
        key=key,
        defaults={'value': initial, 'description': _DESCRIPTIONS.get(key, '')},
    )
    return row.value


def unpaid_days_before_cancellation() -> int:
    """The pay window in days, as Finance has it set.

    Raises if the row has been blanked — a blank is "nobody has answered", not
    "zero days", and zero days would cancel a customer the morning their debit
    order bounced.
    """
    value = get_setting(UNPAID_DAYS_KEY)
    if value is None or str(value).strip() == '':
        raise GenricConfigurationError(
            f'The GENRIC pay window ({UNPAID_DAYS_KEY}) is blank, so the '
            f'cancellations report cannot be produced. {UNPAID_DAYS_QUESTION} '
            f'Set it to that number of days in GENRIC Settings. There is '
            f'deliberately no fallback: a guessed pay window cancels real cover.'
        )
    try:
        days = int(str(value).strip())
    except (TypeError, ValueError):
        raise GenricConfigurationError(
            f'{UNPAID_DAYS_KEY} must be a whole number of days, got {value!r}.'
        )
    if days < 0:
        raise GenricConfigurationError(
            f'{UNPAID_DAYS_KEY} must be zero or more days, got {days}.'
        )
    return days


def is_unpaid_days_configured() -> bool:
    """True when the CFO's answer is in place. Used to mark the report BLOCKED."""
    try:
        unpaid_days_before_cancellation()
    except GenricConfigurationError:
        return False
    return True


# ── GENRIC's own bank details — the second thing nobody may guess ───────────
#: CFO ruling, 13 Sep 2026: the money runs Alpha Direct → GENRIC. This is a
#: quota-share cession, so the cedant (Alpha Direct South Africa) remits the net
#: reinsurance premium to the reinsurer (GENRIC). The invoice as first built had
#: it the wrong way round: it printed "PAYABLE TO" over Alpha Direct's OWN FNB
#: account, which would have invited GENRIC to pay us.
#:
#: GENRIC's account is NOT in the treaty, NOT in the build prompt and NOT in
#: Omni. It is deliberately NOT defaulted and NOT guessed — a wrong account
#: number on a reinsurance invoice sends real money to a stranger, which is far
#: worse than an invoice that says the details are still being obtained.
REMIT_TO_SETTING = 'GENRIC_REINSURER_BANK_DETAILS'

REMIT_TO_QUESTION = (
    'Which account does Alpha Direct pay the GENRIC net reinsurance premium '
    'into? Bank, account number, branch and SWIFT, exactly as GENRIC gave them.'
)


def reinsurer_remit_to() -> str:
    """GENRIC's bank details, as one line. Raises if nobody has supplied them."""
    value = getattr(settings, REMIT_TO_SETTING, None)
    if value is None or not str(value).strip():
        raise GenricConfigurationError(
            f'{REMIT_TO_SETTING} is not set, so the GENRIC invoice cannot say '
            f'where to pay. {REMIT_TO_QUESTION} '
            f'Set {REMIT_TO_SETTING} to that one line. There is deliberately no '
            f"default: Alpha Direct's own account was the old (inverted) "
            f'assumption, and a guessed reinsurer account pays a stranger.'
        )
    return str(value).strip()


def is_remit_to_configured() -> bool:
    """True once GENRIC's bank details are on file."""
    try:
        reinsurer_remit_to()
    except GenricConfigurationError:
        return False
    return True
