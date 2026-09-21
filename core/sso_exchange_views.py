"""SSO -> Omni-token exchange.

Why this exists (CFO 2026-08-24): Omni's Microsoft sign-in used to depend on a
LIVE Microsoft token in the browser for EVERY API call (acquireApiToken via
MSAL). The moment MSAL's browser cache was lost (a cleared tab, an expired
session, a redirect blip) the app was stranded — "Authentication credentials
were not provided" everywhere — because there was no Omni-side session to fall
back on. Graphite does not have this problem: it uses Microsoft ONCE to prove
identity, then issues its OWN session token.

This endpoint does the same for Omni. After Microsoft has signed the user in,
the browser POSTs the Microsoft access token here exactly once; we verify it
(the same verifier the Azure DRF auth class uses) and hand back Omni's own DRF
token. From then on every API call carries the Omni token (Authorization: Token
<t>) — the identical, robust path the email+password login already uses.

Reuse, not reinvention: token verification + user resolution come straight from
core.azure_auth; the token is minted exactly as core.staff_login_views mints it.
"""
import logging

from django.conf import settings
from rest_framework import exceptions as drf_exc
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.decorators import (api_view, authentication_classes,
                                       permission_classes)
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from core.azure_auth import _user_from_claims, _verify_token
from core.device_session_service import mint_device_session, wants_device_session

log = logging.getLogger(__name__)


@api_view(['POST'])
@authentication_classes([])          # we verify the Microsoft bearer ourselves
@permission_classes([AllowAny])
def sso_exchange(request):
    """POST with `Authorization: Bearer <microsoft-access-token>` -> {token}.

    401 if the Microsoft token is missing/invalid; 403 if the resolved account
    is inactive. Never issues a token without a validly-signed Microsoft token
    for our tenant + audience.
    """
    # Honour the SSO kill switch. azure_auth is documented "DORMANT until
    # AZURE_SSO_ENABLED=True" and its DRF auth class refuses every request when
    # the flag is off; this endpoint calls the verifier directly, so it MUST
    # gate the same way. If SSO is disabled (tenant compromise, decommission,
    # half-configured env), no Microsoft token may mint an Omni token here.
    if not getattr(settings, 'AZURE_SSO_ENABLED', False):
        return Response({'detail': 'Microsoft sign-in is disabled.'},
                        status=status.HTTP_401_UNAUTHORIZED)

    auth = request.META.get('HTTP_AUTHORIZATION', '')
    parts = auth.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != 'bearer' or not parts[1].strip():
        return Response({'detail': 'A Microsoft sign-in token is required.'},
                        status=status.HTTP_401_UNAUTHORIZED)

    try:
        claims = _verify_token(parts[1].strip())
        user = _user_from_claims(claims)
    except drf_exc.AuthenticationFailed as e:
        # Verifier's own message (expired / audience / issuer / missing claim).
        log.warning('SSO exchange rejected: %s', getattr(e, 'detail', e))
        return Response({'detail': str(getattr(e, 'detail', e))},
                        status=status.HTTP_401_UNAUTHORIZED)
    except Exception:
        log.exception('SSO exchange failed to verify a Microsoft token')
        return Response({'detail': 'Could not verify the Microsoft sign-in.'},
                        status=status.HTTP_401_UNAUTHORIZED)

    if not user.is_active:
        return Response({'detail': 'This account is inactive.'},
                        status=status.HTTP_403_FORBIDDEN)

    if wants_device_session(request.data):
        return Response(mint_device_session(user, request))
    token, _ = Token.objects.get_or_create(user=user)
    return Response({'token': token.key})
