"""Switch off a leaver's Microsoft 365 account — CFO decision 19-Sep-2026.

Timing follows Human Capital's rule: senior staff lose Omni and line-of-business
systems as soon as HR records the exit, but EMAIL STAYS until the last working day;
everyone else keeps access to the end of notice. So Microsoft 365 (email, Teams)
is switched off for every leaver the day AFTER the last working day.

Policy (settings.M365_LEAVER_DISABLE): 'off' does nothing, 'report' tells HR and IT
what would be switched off, 'enforce' switches it off. The Graph app needs
User.EnableDisableAccount.All (admin consent) for 'enforce'; without it Microsoft
answers 403 and the case records that plainly — nothing is marked done.
"""
import logging

import requests
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone

from hris.models import OffboardingCase

logger = logging.getLogger(__name__)

GRAPH = 'https://graph.microsoft.com/v1.0'
COMPANY_DOMAINS = ('@alphadirect.co.bw', '@insurance.co.bw')
# Never switched off automatically, whatever the leaver record says.
REPORTED = 'due to be switched off (Omni is in report mode)'
PROTECTED_EMAILS = {'pganesharajah@alphadirect.co.bw', 'excoboard@alphadirect.co.bw'}


def policy():
    value = (getattr(settings, 'M365_LEAVER_DISABLE', 'off') or 'off').strip().lower()
    return value if value in ('off', 'report', 'enforce') else 'off'


def skip_reason(case):
    """Why this leaver must NOT be switched off automatically, or '' if they may be."""
    employee = case.employee
    email = (employee.email or '').strip().lower()
    if not email:
        return 'no work email on the payroll record'
    if not email.endswith(COMPANY_DOMAINS):
        return 'email is not a company Microsoft account'
    if email in PROTECTED_EMAILS:
        return 'protected account'
    if employee.keep_access_after_exit:
        return 'HR ticked "keep access after exit"'
    if get_user_model().objects.filter(email__iexact=email, is_superuser=True).exists():
        return 'Omni super-administrator'
    return ''


def due_cases(today=None):
    """Leavers whose last working day has passed and whose account is still on."""
    today = today or timezone.localdate()
    return (OffboardingCase.objects
            .filter(status__in=[OffboardingCase.Status.OPEN, OffboardingCase.Status.COMPLETE],
                    last_working_day__lt=today, m365_disabled_at__isnull=True)
            .select_related('employee')
            .order_by('last_working_day'))


def disable_account(email):
    """Block sign-in and end open sessions. Returns (ok, plain-English message)."""
    from licensing.services.graph import GraphError, _token
    try:
        token = _token()
    except GraphError as exc:
        return False, f'could not connect to Microsoft: {exc}'
    headers = {'Authorization': f'Bearer {token}'}
    try:
        r = requests.patch(f'{GRAPH}/users/{email}', headers=headers,
                           json={'accountEnabled': False}, timeout=20)
    except requests.RequestException as exc:
        return False, f'could not reach Microsoft ({exc.__class__.__name__}); will retry tomorrow'
    if r.status_code == 403:
        return False, 'Omni does not yet have Microsoft permission to switch accounts off'
    if r.status_code == 404:
        return False, 'no such Microsoft account'
    if r.status_code not in (200, 204):
        return False, f'Microsoft refused ({r.status_code})'
    # Best effort: sign-in is already blocked; ending live sessions is a bonus.
    try:
        s = requests.post(f'{GRAPH}/users/{email}/revokeSignInSessions', headers=headers, timeout=20)
        revoked = s.status_code in (200, 204)
    except requests.RequestException:
        revoked = False
    if not revoked:
        return True, 'switched off (open sessions end within the hour)'
    return True, 'switched off and signed out everywhere'


def run(*, commit, today=None):
    """Process every due leaver. Returns a list of (case, action, message) rows."""
    mode = policy()
    rows = []
    for case in due_cases(today):
        reason = skip_reason(case)
        if reason:
            rows.append((case, 'skipped', reason))
            continue
        if mode != 'enforce' or not commit:
            # Report mode tells HR + IT once per leaver, not every morning.
            if commit and case.m365_note == REPORTED:
                rows.append((case, 'already reported', REPORTED))
                continue
            if commit:
                case.m365_note = REPORTED
                case.save(update_fields=['m365_note'])
            rows.append((case, 'would switch off', f'policy is {mode}'))
            continue
        ok, message = disable_account(case.employee.email.strip())
        case.m365_note = message[:300]
        fields = ['m365_note']
        if ok:
            case.m365_disabled_at = timezone.now()
            fields.append('m365_disabled_at')
        case.save(update_fields=fields)
        rows.append((case, 'switched off' if ok else 'failed', message))
    return rows
