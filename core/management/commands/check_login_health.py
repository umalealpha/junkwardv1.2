"""
core/management/commands/check_login_health.py

Synthetic login-health monitor (CFO directive 2026-06-16 — permanent SSO
solution, part 4 of 4). Runs on a cron and alerts excoboard@ + pganesharajah@
the MOMENT the login path looks broken — so we hear it from monitoring, not
from a locked-out colleague.

Checks (server-side, no credentials needed):
  1. The /login page returns HTTP 200.
  2. Backend SSO config is present (Azure/M365 client id + tenant id).
  3. Microsoft's JWKS endpoint (token-signing keys) is reachable — if that is
     down, no SSO sign-in can complete.

HONEST SCOPE: this catches the "a deploy or config broke login" and "Microsoft
is down" classes (exactly what hit us 2026-06-15/16). It does NOT drive a full
browser MSAL round-trip — a purely client-side regression won't trip it; the
login E2E gate (permanent-fix part 2) is what covers that.

Idempotent + state-aware: only emails on a transition (healthy->broken or
recovery), tracked via a tiny cache key, so a sustained outage doesn't spam.
"""
from __future__ import annotations

import logging

import requests
from django.conf import settings
from django.core.cache import cache
from django.core.management.base import BaseCommand

log = logging.getLogger('login-health')

_LOGIN_URL = 'https://omni.alphadirect.co.bw/login'
_STATE_KEY = 'login_health_last_state'   # 'ok' | 'broken'
_ALERT_TO  = ['excoboard@alphadirect.co.bw', 'pganesharajah@alphadirect.co.bw']


class Command(BaseCommand):
    help = "Synthetic login-health check; alerts on breakage transitions."

    def add_arguments(self, parser):
        parser.add_argument('--force-alert', action='store_true',
                            help='Send the status email even with no state change (test).')

    def handle(self, *args, **opts):
        problems = self._run_checks()
        state = 'broken' if problems else 'ok'
        prev = cache.get(_STATE_KEY)
        cache.set(_STATE_KEY, state, None)

        self.stdout.write(f"[login-health] state={state} prev={prev} problems={problems or 'none'}")

        # Alert on transition (or --force-alert): broken->fixed and ok->broken.
        changed = prev is not None and prev != state
        if not (changed or opts['force_alert']):
            return
        try:
            self._alert(state, problems)
            self.stdout.write(f"[login-health] alert sent ({prev} -> {state})")
        except Exception:    # noqa: BLE001
            log.exception('[login-health] alert email failed')

    # --------------------------------------------------------------- checks
    def _run_checks(self) -> list[str]:
        problems: list[str] = []

        # 1. /login reachable
        try:
            r = requests.get(_LOGIN_URL, timeout=10)
            if r.status_code != 200:
                problems.append(f'/login returned HTTP {r.status_code}')
        except requests.RequestException as exc:
            problems.append(f'/login unreachable: {exc}')

        # 2. SSO config present
        tenant = getattr(settings, 'AZURE_TENANT_ID', '') or getattr(settings, 'M365_TENANT_ID', '')
        client = getattr(settings, 'M365_CLIENT_ID', '')
        if not tenant:
            problems.append('Azure/M365 tenant id missing from backend config')
        if not client:
            problems.append('M365 client id missing from backend config')

        # 3. Microsoft JWKS reachable (token-signing keys)
        if tenant:
            jwks = f'https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys'
            try:
                rk = requests.get(jwks, timeout=10)
                if rk.status_code != 200 or not (rk.json() or {}).get('keys'):
                    problems.append(f'Microsoft JWKS not OK (HTTP {rk.status_code})')
            except requests.RequestException as exc:
                problems.append(f'Microsoft JWKS unreachable: {exc}')
            except ValueError:
                problems.append('Microsoft JWKS returned non-JSON')

        return problems

    # ---------------------------------------------------------------- alert
    def _alert(self, state: str, problems: list[str]) -> None:
        from core.notifications import send_html_with_cfo_cc
        if state == 'broken':
            items = ''.join(f'<li>{p}</li>' for p in problems)
            html = (
                "<div style=\"font-family:'Book Antiqua',Georgia,serif;color:#0D1B2A;max-width:620px\">"
                "<div style='background:#C1121F;padding:14px 18px;border-radius:8px 8px 0 0'>"
                "<span style='color:#fff;font-weight:700;font-size:16px'>⚠ Omni login looks BROKEN</span></div>"
                "<div style='border:1px solid #e5e7eb;border-top:0;padding:16px 18px;border-radius:0 0 8px 8px'>"
                "<p>The automated login-health check just failed. Staff may be unable to sign in.</p>"
                f"<ul>{items}</ul>"
                "<p>Use the break-glass admin login if SSO itself is down. Investigating priority.</p>"
                "</div></div>"
            )
            subject = '⚠ Omni login health: BROKEN'
        else:
            html = (
                "<div style=\"font-family:'Book Antiqua',Georgia,serif;color:#0D1B2A;max-width:620px\">"
                "<div style='background:#0A9396;padding:14px 18px;border-radius:8px 8px 0 0'>"
                "<span style='color:#fff;font-weight:700;font-size:16px'>✓ Omni login recovered</span></div>"
                "<div style='border:1px solid #e5e7eb;border-top:0;padding:16px 18px;border-radius:0 0 8px 8px'>"
                "<p>The login-health check is passing again. Sign-in is back to normal.</p>"
                "</div></div>"
            )
            subject = '✓ Omni login health: recovered'
        send_html_with_cfo_cc(subject=subject, html=html, to=_ALERT_TO)
