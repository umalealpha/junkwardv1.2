"""Your data — the staff member's own controls over what Omni holds about them.

Google Play requires an app that holds a user account to offer, IN the app, a
clear way to remove that account and its data, plus a web page saying the same
(Alpha Nexus was rejected for the App Store equivalent, 5.1.1(v), in July 2026 —
we are not repeating that here).

A staff app cannot honestly offer a one-tap wipe: an employment record is one
Alpha Direct is legally required to keep, and deleting a person's approvals
would tear holes in the audit trail behind real payments. So this gives the two
things that ARE honest — everything the staff member can do for themselves right
now, and a real request that reaches the people who can act on the rest.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from core.device_session_models import StaffDeviceSession

log = logging.getLogger(__name__)

DATA_CONTACT = 'it@alphadirect.co.bw'


class DataRequestThrottle(UserRateThrottle):
    scope = 'app-data-request'


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def sign_out_everywhere(request):
    """Revoke every phone session this staff member holds, including this one."""
    live = StaffDeviceSession.objects.filter(user=request.user, revoked_at__isnull=True,
                                             expires_at__gt=timezone.now())
    count = 0
    for sess in live:
        sess.revoke(by=request.user)
        count += 1
    return Response({'signed_out': count})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([DataRequestThrottle])
def request_data_deletion(request):
    """Send the staff member's deletion request to the people who can act on it.

    Deliberately does NOT delete anything itself. Payroll, leave and approval
    records are employment records and part of the audit trail behind real
    money; removing them is a decision for HR and IT, not a button.
    """
    if str(request.data.get('confirm') or '').strip().upper() != 'DELETE':
        return Response({'detail': 'Type DELETE to confirm.'}, status=400)
    note = str(request.data.get('note') or '').strip()[:1000]
    user = request.user
    who = (user.get_full_name() or user.username or '').strip()
    body = (
        f'{who} ({user.email or "no email on file"}) has asked, from the Omni staff app, '
        f'for their personal data to be removed.\n\n'
        f'Requested: {timezone.now():%d %b %Y %H:%M} UTC\n'
        f'Username: {user.username}\n\n'
        f'Their note:\n{note or "(none)"}\n\n'
        f'What they have already done themselves: signing out a phone is available to them in the app.\n'
        f'What needs a person: deciding what may be removed and what Alpha Direct must keep as an '
        f'employment record. Please reply to them directly.\n'
    )
    sent = False
    try:
        send_mail(subject=f'Data deletion request — {who or user.username}',
                  message=body,
                  from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None),
                  recipient_list=[DATA_CONTACT], fail_silently=False)
        sent = True
    except Exception:
        # The request still counts as made — it is in the log either way, and the
        # staff member is told to email directly rather than being told "done".
        log.exception('data deletion request email failed for %s', user.username)
    log.info('data-deletion-request user=%s emailed=%s', user.username, sent)
    return Response({'received': True, 'emailed': sent, 'contact': DATA_CONTACT})
