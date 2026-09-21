"""
claims/subrogation_alert.py — B9 Subrogation: the alert, the letter, the escalation.

Requested by Pako Kago (Financial Controller), "NEW FEATURE SUBROGATION",
12 September 2026. Three of the four pieces he asked for live here; the fourth
— the recovery-possible flag itself — is raised on the GRAPHITE claim by the
claims handler and is TheRiskCo / Pramod's work. Omni cannot add a field to
Graphite and does not try to. This module is the Omni end, built so it is ready
the day that flag starts riding the nightly feed.

The state machine is three timestamps on `claims.Subrogation`, not a status
column:

    recovery_flagged_at    -- stamped ONCE when the flag arrives.
    demand_letter_sent_at  -- stamped when Lindani dispatches the letter.
                              This is the only thing that clears the alert.
    escalated_at           -- stamped ONCE when the 48-hour escalation fires.

WHY TIMESTAMPS AND NOT A STATUS. The escalation has to catch a case SITTING
unactioned, and it has to survive somebody touching and re-touching the record.
A clock measured from `updated_at` is defeated by the most ordinary thing in the
world: opening the case and saving it. A control keyed to the CURRENT state is
defeated even more cheaply — people simply stop putting the case into that
state. So the clock is anchored to the moment the flag arrived and nothing an
editor does to the row moves it.

EMAIL ROUTING, and the trap in it:
  * The alert to Lindani is PERSONAL — it carries a link to her case. It goes
    with cc_cfo=False. The automatic CFO copy is excoboard@, a SHARED mailbox,
    and a personal action link sitting in a shared mailbox lets anyone there act
    as her.
  * The escalation to the Finance Manager is also cc_cfo=False; it is an
    operational nudge, copied to the Financial Controller who asked for it.
  * The demand letter is CUSTOMER-FACING — it goes to a third party outside
    Alpha Direct. It must NOT carry the red internal "do not reply, log it in
    Omni" banner, and it asks the recipient to respond, so no_reply=False.
    `send_html_with_cfo_cc` already suppresses the banner when any recipient is
    external; passing no_reply=False makes the intent explicit and survives a
    future change to that helper.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.utils import timezone

from core.notifications import send_html_with_cfo_cc, wrap_plain_as_html, _link

log = logging.getLogger(__name__)

# Named recipients. Overridable in settings so a leaver does not need a deploy.
LINDANI_EMAIL = getattr(
    settings, 'SUBROGATION_ALERT_EMAIL', 'lmababa@alphadirect.co.bw')             # Lindani Mababa
FINANCE_MANAGER_EMAIL = getattr(
    settings, 'SUBROGATION_ESCALATION_EMAIL', 'ktshutlhedi@alphadirect.co.bw')    # Kago Tshutlhedi
FINANCIAL_CONTROLLER_EMAIL = getattr(
    settings, 'SUBROGATION_CONTROLLER_EMAIL', 'pkago@alphadirect.co.bw')          # Pako Kago

# Pako's own suggestion, adopted: "auto-escalation after 48 hours unclear".
ESCALATION_HOURS = int(getattr(settings, 'SUBROGATION_ESCALATION_HOURS', 48))

ALERT_TITLE_PREFIX = 'Subrogation recovery'


def alert_task_title(sub) -> str:
    """The OmniTask title. Stable, so the closing filter can find it again."""
    return f'{ALERT_TITLE_PREFIX} {sub.claim_reference} — send the demand letter'[:200]


def _money(v) -> str:
    return f'P {Decimal(v or 0):,.2f}'


def _case_link(sub) -> str:
    return _link(f'/claims/subrogations?case={sub.id}')


# ---------------------------------------------------------------------------
# 1. The flag arrives
# ---------------------------------------------------------------------------

def flag_recovery_possible(sub, *, source: str = 'graphite', when=None) -> bool:
    """Record that Claims flagged this case as recovery-possible.

    Returns True only when this call is the TRANSITION — the first time the flag
    lands on this case. A second arrival of the same flag (the nightly feed
    re-sends the whole snapshot every night, by design) is a no-op: it does not
    re-stamp the clock, does not re-alert Lindani, and above all does not hand
    the case a fresh 48 hours it has not earned.
    """
    if sub.recovery_flagged_at is not None:
        return False
    sub.recovery_flagged_at = when or timezone.now()
    sub.recovery_flag_source = (source or '')[:20]
    sub.save(update_fields=['recovery_flagged_at', 'recovery_flag_source', 'updated_at'])
    return True


# ---------------------------------------------------------------------------
# 2. The alert to Lindani
# ---------------------------------------------------------------------------

def notify_recovery_flagged(sub) -> int:
    """Email Lindani and raise a persistent task on her Omni board."""
    _raise_alert_task(sub)

    flagged = timezone.localtime(sub.recovery_flagged_at or timezone.now())
    body = (
        f'Claims have flagged claim {sub.claim_reference} as recovery-possible.\n\n'
        f'Third party: {sub.third_party_name or "— to be completed"}\n'
        f'Third-party insurer: {sub.third_party_insurer or "— to be completed"}\n'
        f'Claim paid: {_money(sub.claim_paid_amount)}\n'
        f'Estimated recovery: {_money(sub.total_recoverable)}\n'
        f'Flagged: {flagged:%d %b %Y %H:%M} (Gaborone)\n\n'
        f'Please open the case, complete the third-party details and send the '
        f'demand letter. The alert clears when the letter is dispatched.\n\n'
        f'If nothing is sent within {ESCALATION_HOURS} hours this goes to the '
        f'Finance Manager.\n\n'
        f'Open the case: {_case_link(sub)}\n'
    )
    return send_html_with_cfo_cc(
        subject=f'Recovery possible — claim {sub.claim_reference}',
        html=wrap_plain_as_html(body),
        to=[LINDANI_EMAIL],
        text_fallback=body,
        # PERSONAL alert with a one-tap link into her case. excoboard@ is a
        # SHARED mailbox; a personal action link must not land there.
        cc_cfo=False,
    )


def _raise_alert_task(sub) -> None:
    try:
        from core.models import OmniTask
        from core.notifications import _resolve_omni_users

        users = _resolve_omni_users({LINDANI_EMAIL})
        if not users:
            log.warning('Subrogation alert: no active Omni user for %s', LINDANI_EMAIL)
            return
        assignee = users[0]
        assigner = getattr(sub, 'created_by', None) or assignee
        title = alert_task_title(sub)
        if OmniTask.objects.filter(
                title=title, assignee=assignee,
                status__in=[OmniTask.Status.PENDING,
                            OmniTask.Status.IN_PROGRESS]).exists():
            return
        OmniTask.objects.create(
            assigner=assigner, assignee=assignee, title=title,
            body=(f'Claim {sub.claim_reference} is flagged recovery-possible. '
                  f'Complete the third-party details and send the demand letter.\n'
                  f'Open: {_case_link(sub)}'),
            priority=OmniTask.Priority.HIGH, status=OmniTask.Status.PENDING,
        )
    except Exception as exc:                                   # noqa: BLE001
        log.warning('Subrogation alert task failed for %s: %s',
                    getattr(sub, 'claim_reference', '?'), exc)


def close_alert_tasks(sub) -> None:
    """The letter went out — take the task off her board."""
    try:
        from core.models import OmniTask
        (OmniTask.objects
         .filter(title=alert_task_title(sub),
                 status__in=[OmniTask.Status.PENDING, OmniTask.Status.IN_PROGRESS])
         .update(status=OmniTask.Status.DONE, completed_at=timezone.now()))
    except Exception as exc:                                   # noqa: BLE001
        log.warning('Subrogation alert close failed: %s', exc)


# ---------------------------------------------------------------------------
# 3. The demand letter to the third party  (CUSTOMER-FACING MAIL)
# ---------------------------------------------------------------------------

def send_demand_letter(sub, *, to_email: str, pdf_bytes: bytes) -> int:
    """Email the demand letter to the third party and clear the alert.

    Plain, humble English. No internal red banner — this leaves the company.
    """
    body = (
        f'Dear {sub.third_party_name or "Sir or Madam"},\n\n'
        f'We have attached a letter of demand about our claim '
        f'{sub.claim_reference}.\n\n'
        f'Alpha Direct Insurance Company (Pty) Ltd has settled this claim for '
        f'our insured, and we are asking you to reimburse us the amount set out '
        f'in the attached letter.\n\n'
        f'Please read the letter and come back to us. If anything in it is '
        f'wrong, or if you would like to discuss how to settle it, please reply '
        f'to this email and we will gladly talk it through.\n\n'
        f'Thank you.\n\n'
        f'Regards,\n'
        f'Recoveries\n'
        f'Alpha Direct Insurance Company (Pty) Ltd\n'
    )
    filename = f'demand-letter-{sub.claim_reference}.pdf'.replace('/', '-')
    sent = send_html_with_cfo_cc(
        subject=f'Letter of demand — claim {sub.claim_reference}',
        html=wrap_plain_as_html(body),
        to=[to_email],
        text_fallback=body,
        attachments=[(filename, pdf_bytes, 'application/pdf')],
        reply_to=[LINDANI_EMAIL],
        cc_cfo=False,        # a third party must never see our internal cc list
        no_reply=False,      # the letter asks for an answer — no red banner
    )
    sub.demand_letter_sent_at = timezone.now()
    sub.demand_letter_sent_to = (to_email or '')[:254]
    sub.save(update_fields=['demand_letter_sent_at', 'demand_letter_sent_to',
                            'updated_at'])
    close_alert_tasks(sub)
    return sent


# ---------------------------------------------------------------------------
# 4. The 48-hour escalation
# ---------------------------------------------------------------------------

def due_for_escalation(qs, *, now=None):
    """Cases that have been sitting flagged and unactioned past the deadline.

    Each condition is load-bearing:

      recovery_flagged_at <= now - 48h   the clock, anchored to the ARRIVAL of
                                         the flag, not to `updated_at`. Editing
                                         the case cannot move it.
      demand_letter_sent_at IS NULL      the only action that counts.
      escalated_at IS NULL               fires exactly once, for ever.
    """
    now = now or timezone.now()
    cutoff = now - timedelta(hours=ESCALATION_HOURS)
    return qs.filter(
        recovery_flagged_at__isnull=False,
        recovery_flagged_at__lte=cutoff,
        demand_letter_sent_at__isnull=True,
        escalated_at__isnull=True,
    )


def escalate(sub, *, now=None) -> int:
    """Email the Finance Manager and stamp the case so it never fires again."""
    now = now or timezone.now()
    flagged = timezone.localtime(sub.recovery_flagged_at)
    hours = int((now - sub.recovery_flagged_at).total_seconds() // 3600)
    body = (
        f'Claim {sub.claim_reference} was flagged as recovery-possible on '
        f'{flagged:%d %b %Y %H:%M} (Gaborone) and no demand letter has gone out '
        f'{hours} hours later.\n\n'
        f'Third party: {sub.third_party_name or "— not yet completed"}\n'
        f'Claim paid: {_money(sub.claim_paid_amount)}\n'
        f'Estimated recovery: {_money(sub.total_recoverable)}\n\n'
        f'Please pick this up or reassign it.\n\n'
        f'Open the case: {_case_link(sub)}\n'
    )
    sent = send_html_with_cfo_cc(
        subject=f'Overdue {ESCALATION_HOURS}h — recovery on claim {sub.claim_reference}',
        html=wrap_plain_as_html(body),
        to=[FINANCE_MANAGER_EMAIL],
        cc=[FINANCIAL_CONTROLLER_EMAIL, LINDANI_EMAIL],
        text_fallback=body,
        cc_cfo=False,
    )
    sub.escalated_at = now
    sub.save(update_fields=['escalated_at', 'updated_at'])
    return sent
