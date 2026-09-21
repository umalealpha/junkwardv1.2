"""payroll/offboard_access.py — close a leaver's Omni login once they have gone.

CFO directive 2026-09-18. Human Capital announced that IT closes the mailbox,
Time Doctor licence and "Omni access" on the last working day. Two beliefs in
that thread were wrong, and this module exists because of the gap between them:

  * Omni does NOT sign in through Office 365. AZURE_SSO_ENABLED is False and
    core.azure_auth is dormant; the real door is core.staff_login_views — the
    person's own Omni password plus a code emailed to their mailbox. Suspending
    the mailbox blocks a NEW sign-in but ends nothing that is already open.
  * Recording a termination date did nothing at all to the login. On 2026-09-18
    fourteen employees carried a past termination date and SEVEN still had an
    active Omni account, the oldest 84 days after their last day. Omni went on
    counting them as working staff, so their unfinished work was still scored
    against their manager.

Closing the account is the only action that ends BOTH live session types: the
15-hour browser token (core.token_auth) and the 30-day phone session
(core.device_auth checks `user.is_active` on every request). Revoking the
sessions as well is belt and braces, and makes the audit row honest about what
was actually ended.

Timing is END OF THE LAST WORKING DAY (CFO's choice, 2026-09-18): HR often
records the exit on the morning of the last day while the person is still
handing over and returning their laptop. So a termination dated TODAY is left
alone until the nightly sweep; anything dated BEFORE today is closed at once.

`keep_access_after_exit` on the employee is the one exception — an external
contractor who keeps working with us after their employment ends.
"""
from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from core.models import AuditLog
from core.session_teardown import (browser_token_count, clear_browser_tokens,
                                   live_device_session_count, revoke_device_sessions)


def should_close(employee) -> bool:
    """True when this leaver's Omni login ought to be shut.

    Four gates, and every one of them has a live reason:
      * a linked login must exist at all (most payroll rows have none);
      * the last working day must have PASSED (see the timing note above);
      * the contractor tick wins over everything;
      * the account must still be open, so a re-run is a no-op.
    """
    user = getattr(employee, 'user', None)
    if user is None or not user.is_active:
        return False
    if employee.keep_access_after_exit:
        return False
    if not employee.termination_date:
        return False
    return employee.termination_date < timezone.localdate()


def close_omni_access(employee, *, actor=None, dry_run: bool = False) -> dict:
    """Shut the leaver's Omni login and end every live session.

    Returns a small dict describing what was (or would be) done, so the sweep
    command can print a truthful report in dry-run without touching anything.

    A SUPERUSER is never closed automatically. A wrong termination date on the
    wrong record would otherwise lock the business out of its own system, and
    that failure is far more expensive than a leaver staying open one more day.
    The sweep reports them instead, by name, for a person to deal with.
    """
    user = employee.user
    result = {
        'employee': employee.full_name,
        'email': employee.email or getattr(user, 'email', ''),
        'termination_date': employee.termination_date,
        'closed': False,
        'skipped_superuser': False,
        'changed_under_us': False,
        'device_sessions_revoked': 0,
        'browser_tokens_cleared': 0,
    }

    if user.is_superuser:
        result['skipped_superuser'] = True
        return result

    if dry_run:
        result['closed'] = True          # "would close"
        result['device_sessions_revoked'] = live_device_session_count(user)
        result['browser_tokens_cleared'] = browser_token_count(user)
        return result

    now = timezone.now()
    with transaction.atomic():
        # Re-read the employee under a row lock and re-check. Without this the
        # sweep decides on a row it read at the top of the loop: HR ticking
        # "keep access" while the sweep is part-way through would be ignored and
        # a working contractor locked out anyway. The flag is the whole safety
        # net here, so it is read as late as possible.
        fresh = type(employee).objects.select_for_update().get(pk=employee.pk)
        if not should_close(fresh):
            result['closed'] = False
            result['changed_under_us'] = True
            return result

        result['device_sessions_revoked'] = revoke_device_sessions(user, now)
        result['browser_tokens_cleared'] = clear_browser_tokens(user)
        user.is_active = False
        user.save(update_fields=['is_active'])

        # Switch the profile off too, so the Users screen agrees with reality
        # and the leaver's Omni powers go with their login. Closing only the
        # login left five of the first six leavers still showing "Active" on
        # screen — the same screen-says-one-thing-login-says-another problem
        # Unami Butale reported, just created from the other side.
        # UserProfile.save() syncs is_active back onto the user, which is
        # already False here, so this cannot loop.
        profile = getattr(user, 'profile', None)
        if profile is None:
            from core.models import UserProfile
            profile = UserProfile.objects.filter(user=user).first()
        if profile is not None and profile.is_active:
            profile.is_active = False
            profile.save(update_fields=['is_active'])

        result['closed'] = True

        AuditLog.objects.create(
            table_name='auth.User',
            record_id=str(user.pk),
            action=AuditLog.Action.UPDATE,
            user=actor if getattr(actor, 'is_authenticated', False) else None,
            new_values={'is_active': False,
                        'reason': 'leaver — exit date passed',
                        'termination_date': str(employee.termination_date),
                        'device_sessions_revoked': result['device_sessions_revoked'],
                        'browser_tokens_cleared': result['browser_tokens_cleared']},
            description=(
                f'Closed Omni access for {employee.full_name} — last working day '
                f'{employee.termination_date} has passed. Ended '
                f'{result["device_sessions_revoked"]} phone session(s) and '
                f'{result["browser_tokens_cleared"]} browser session(s).'
            ),
        )
    return result


# Session teardown lives in core.session_teardown — the Users admin screen needs
# exactly the same thing, and a shared helper kept inside one caller is how
# eight copies of the same function happened before.
