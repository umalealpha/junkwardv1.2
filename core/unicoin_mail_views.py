"""core/unicoin_mail_views.py — Omni sends UniCoin's sign-in codes.

CFO decision 2026-08-19: UniCoin needs to email a six-digit sign-in code, and
Amazon's mail service in Cape Town has no verified sender — a code email would
fail, and because a code nobody receives is a locked door, the failure blocks the
sign-in. Omni already sends staff their codes reliably every day, so UniCoin asks
Omni rather than a second mail system being stood up and kept alive.

DELIBERATELY NOT A GENERAL "SEND EMAIL" ENDPOINT. That is the whole design.
A key that can send arbitrary text to arbitrary addresses is a phishing tool the
moment it leaks, and it would leak eventually. This endpoint accepts only an
address and a code, renders the message ITSELF, and refuses any address that is
not an individual Alpha Direct human mailbox. The worst a stolen key can do is
send a staff member a sign-in code they did not ask for.

  POST /api/v1/mail/unicoin-login-code   {email, code, minutes}
  Auth: an ApiKey carrying the `unicoin-mail` scope. Nothing else reaches it.

The code is NEVER logged, here or in OutboundEmailLog — a log a developer can read
is not a second factor.
"""
from __future__ import annotations

import logging
import re

from django.core.cache import cache
from rest_framework import status
from rest_framework.decorators import (api_view, authentication_classes,
                                       permission_classes)
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.notifications import send_html_with_cfo_cc, wrap_plain_as_html
from core.staff_login_views import _is_human_ad_email, _norm

log = logging.getLogger('unicoin-mail')

SCOPE = 'unicoin-mail'
_CODE_RE = re.compile(r'^\d{4,8}$')
#: One code per address per 30s. A code emailed twice a second is a mail bomb
#: aimed at a colleague's inbox, not a login.
_MIN_SECONDS_BETWEEN = 30


def _key_has_scope(request) -> bool:
    api_key = getattr(request, 'auth', None)
    if api_key is None or not hasattr(api_key, 'allowed_scopes'):
        return False
    scopes = list(api_key.allowed_scopes or [])
    return SCOPE in scopes or 'admin' in scopes


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def unicoin_login_code(request):
    """Email one UniCoin sign-in code. Rendered here, so the caller cannot
    choose the words."""
    if not _key_has_scope(request):
        # Same wording whether the key is wrong or absent — never help a caller
        # work out which scope would have worked.
        return Response({'detail': 'This endpoint is restricted.'},
                        status=status.HTTP_403_FORBIDDEN)

    email = _norm(request.data.get('email'))
    raw_code = request.data.get('code')
    code = ('' if raw_code is None else str(raw_code)).strip()

    # Validated as a string rather than wrapped in try/except int(): a parse guarded
    # by an exception handler is a place a future edit can quietly start swallowing
    # something else.
    #
    # `is None` rather than `or`, because `0 or '10'` is '10' in Python — so
    # minutes=0 was silently treated as "not supplied" and became the 10-minute
    # default. A caller asking for a zero-minute expiry has made a mistake and must
    # be told, not quietly given something else. My own test caught this.
    raw_minutes = request.data.get('minutes')
    # Absent means "use the default". An explicitly sent blank does NOT — that is a caller
    # mistake and it gets told so, rather than quietly receiving ten minutes.
    raw_minutes = '10' if raw_minutes is None else str(raw_minutes).strip()
    if not raw_minutes.isdigit():
        return Response({'detail': 'minutes must be a whole number.'},
                        status=status.HTTP_400_BAD_REQUEST)
    minutes = int(raw_minutes)

    if not _CODE_RE.match(code):
        # Never echo the value back — an error message is a log line too.
        return Response({'detail': 'code must be 4 to 8 digits.'},
                        status=status.HTTP_400_BAD_REQUEST)
    if not 1 <= minutes <= 60:
        return Response({'detail': 'minutes must be between 1 and 60.'},
                        status=status.HTTP_400_BAD_REQUEST)
    if not _is_human_ad_email(email):
        # Shared boxes, service accounts and outside domains are all refused. This
        # is what stops a stolen key mailing the public.
        log.warning('unicoin-mail REFUSED a non-staff address')
        return Response({'detail': 'That address is not an Alpha Direct staff mailbox.'},
                        status=status.HTTP_400_BAD_REQUEST)

    throttle_key = f'unicoin-mail:{email}'
    if cache.get(throttle_key):
        return Response({'detail': 'A code was just sent — wait a moment before asking again.'},
                        status=status.HTTP_429_TOO_MANY_REQUESTS)

    body = (f"Your UniCoin sign-in code is {code}\n\n"
            f"It expires in {minutes} minutes and can be used once.\n\n"
            f"If you did not try to sign in, ignore this email — and tell IT, because "
            f"somebody has your password.\n")
    # No try/except around the send, deliberately.
    #
    # A mail failure must not be turned into a quiet success, and it must not be
    # swallowed either. The helper returns the number of messages accepted, so a
    # refusal it reports is checked below; anything it RAISES is left to propagate,
    # which Omni records in full and returns as a 500. Either way the caller gets a
    # non-2xx and fails the sign-in, which is the only safe outcome — telling
    # somebody a code is on its way when it is not leaves them waiting for mail
    # that will never arrive.
    sent = send_html_with_cfo_cc(
        subject='Your UniCoin sign-in code',
        html=wrap_plain_as_html(body),
        to=[email],
        text_fallback=body,
        # NEVER cc the CFO/EXCO inbox on a sign-in code. The house rule copies
        # excoboard@ on outbound mail; copying a second factor into a shared inbox
        # would hand a working code to everyone who reads that box.
        cc_cfo=False,
        # No do-not-reply banner: the message tells the reader to contact IT if it
        # was not them, so it must not also tell them not to reply.
        no_reply=False,
    )
    if not sent:
        # Reported, never the code — an error line is a log line too.
        log.error('unicoin-mail: the mail layer accepted 0 messages')
        return Response({'detail': 'Could not send the code email.'},
                        status=status.HTTP_502_BAD_GATEWAY)

    cache.set(throttle_key, 1, _MIN_SECONDS_BETWEEN)
    return Response({'sent': True})
