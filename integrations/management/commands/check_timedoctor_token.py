"""
integrations/management/commands/check_timedoctor_token.py

Daily guard on the Time Doctor API token's expiry.

Time Doctor tokens (JWTs minted via POST /api/1.0/login) expire ~6 months after
minting, and Time Doctor has NO non-expiring company-level key (vendor confirmed
2026-07-14). When a token lapses the daily workforce pull silently 401s and the
reports stop updating — exactly what happened after April 2026.

This command reads the token's own `exp` claim and, starting
TIMEDOCTOR_RENEWAL_WINDOW_DAYS (default 7) before it lapses, emails a renewal
reminder EVERY day to the renewal recipients (CFO + Arjun + Unami by default).
Once the token has already expired it sends an URGENT variant.

  python manage.py check_timedoctor_token            # daily cron
  python manage.py check_timedoctor_token --dry-run  # print, do not email
  python manage.py check_timedoctor_token --force    # email regardless of window (test)

The recipients are sent DIRECT (not via core.notifications) because Arjun is a
named recipient and the notifications helpers' _NEVER_CC blocklist would strip
him. Never raises on an operational miss (no token / flaky mailer) — it logs and
exits clean so the cron stays green.
"""
from __future__ import annotations

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.core.management.base import BaseCommand
from django.utils import timezone

from integrations.timedoctor import TimeDoctorClient, jwt_expiry

_DEFAULT_RENEWAL_TO = [
    'pganesharajah@alphadirect.co.bw',
    'arjuniyer@alphadirect.co.bw',
    'ubutale@alphadirect.co.bw',
]


def _renewal_recipients() -> list[str]:
    to = [a.strip() for a in (getattr(settings, 'TIMEDOCTOR_RENEWAL_TO', []) or []) if a and a.strip()]
    return to or _DEFAULT_RENEWAL_TO


def _body_html(*, expiry_date: str, days_left: int, expired: bool) -> str:
    navy, orange, ink, mut, red = '#0D1B2A', '#F4A623', '#1F2937', '#6B7280', '#DC2626'
    if expired:
        band, headline = red, 'Time Doctor access has EXPIRED'
        sub = (f'The Time Doctor token expired on <b>{expiry_date}</b>. omni has stopped pulling '
               f'Time Doctor data — the daily workforce report will not update until the token is renewed.')
    else:
        band, headline = orange, f'Time Doctor access expires in {days_left} day(s)'
        sub = (f'The Time Doctor token expires on <b>{expiry_date}</b>. Renew it before then, '
               f'or omni will stop pulling Time Doctor data and the daily workforce report will go stale.')
    return f"""<!doctype html><html><body style="margin:0;background:#EEF0F3;font-family:'Segoe UI',Arial,sans-serif">
<div style="max-width:640px;margin:0 auto;background:#fff">
  <div style="background:{navy};padding:16px 24px">
    <div style="color:{orange};font-size:18px;font-weight:700">Alpha Direct — omni</div>
    <div style="color:#AEB6C2;font-size:13px;margin-top:2px">Time Doctor token renewal</div>
  </div>
  <div style="border-left:5px solid {band};padding:18px 24px">
    <div style="font-size:17px;font-weight:700;color:{navy}">{headline}</div>
    <p style="color:{ink};font-size:14px;line-height:1.5;margin:10px 0 4px">{sub}</p>
    <p style="color:{ink};font-size:14px;margin:14px 0 6px"><b>To renew:</b></p>
    <ol style="color:{ink};font-size:14px;line-height:1.6;margin:0;padding-left:20px">
      <li>Log in to Time Doctor with the Alpha Direct service account.</li>
      <li>Generate a fresh API token (valid ~6 months).</li>
      <li>In omni, open <b>Settings &rarr; Vault</b>, update the entry named
          <code>TIMEDOCTOR_TOKEN</code> with the new token, and save.</li>
    </ol>
    <p style="color:{mut};font-size:12px;margin-top:16px">omni will resume the daily pull automatically once the new token is saved.
       You will keep getting this reminder every day until the token is renewed.</p>
  </div>
</div></body></html>"""


def _denied_body_html() -> str:
    navy, orange, ink, mut, red = '#0D1B2A', '#F4A623', '#1F2937', '#6B7280', '#DC2626'
    band, headline = red, 'Time Doctor access has been DENIED / REVOKED'
    sub = ('The live check failed because Time Doctor rejected omni’s API token. '
           'This is not an expiry problem — access has been denied or revoked. '
           'Check the Time Doctor service account’s role and permissions, then update the token in omni.')
    return f"""<!doctype html><html><body style="margin:0;background:#EEF0F3;font-family:'Segoe UI',Arial,sans-serif">
<div style="max-width:640px;margin:0 auto;background:#fff">
  <div style="background:{navy};padding:16px 24px">
    <div style="color:{orange};font-size:18px;font-weight:700">Alpha Direct — omni</div>
    <div style="color:#AEB6C2;font-size:13px;margin-top:2px">Time Doctor token renewal</div>
  </div>
  <div style="border-left:5px solid {band};padding:18px 24px">
    <div style="font-size:17px;font-weight:700;color:{navy}">{headline}</div>
    <p style="color:{ink};font-size:14px;line-height:1.5;margin:10px 0 4px">{sub}</p>
    <p style="color:{ink};font-size:14px;margin:14px 0 6px"><b>To fix:</b></p>
    <ol style="color:{ink};font-size:14px;line-height:1.6;margin:0;padding-left:20px">
      <li>Log in to Time Doctor with the Alpha Direct service account.</li>
      <li>Check the service account’s role and permissions (the token may have been revoked).</li>
      <li>Generate a fresh API token if necessary, then in omni open <b>Settings &rarr; Vault</b>,
          update <code>TIMEDOCTOR_TOKEN</code>, and save.</li>
    </ol>
    <p style="color:{mut};font-size:12px;margin-top:16px">omni will resume the daily pull automatically once access is restored.</p>
  </div>
</div></body></html>"""


class Command(BaseCommand):
    help = 'Email the CFO / Arjun / Unami before the Time Doctor API token expires.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Print what would send, do not email.')
        parser.add_argument('--force', action='store_true', help='Email regardless of the reminder window (test).')
        parser.add_argument('--window-days', type=int, default=None, help='Override the reminder window (days).')

    def handle(self, *args, **opts):
        token = TimeDoctorClient.from_settings().token
        if not token:
            self.stdout.write(self.style.WARNING('SKIPPED: no TIMEDOCTOR_TOKEN set — nothing to check.'))
            return

        expiry = jwt_expiry(token)
        # The live probe runs whatever the token looks like: a token with a readable
        # exp can still be denied (account blocked, 17-Sep-2026), and the hourly
        # infra/timedoctor-watch.sh reads the probe lines below as its only signal.
        try:
            client = TimeDoctorClient.from_settings()
            client.users()
        except Exception as exc:    # noqa: BLE001
            exc_msg = str(exc)
            if '401' in exc_msg or '403' in exc_msg or 'denied' in exc_msg:
                self.stderr.write(self.style.WARNING(f'Time Doctor rejected the live probe: {exc}'))
                recipients = _renewal_recipients()
                subject = 'URGENT: Time Doctor access DENIED/REVOKED — omni workforce reports are down'
                html = _denied_body_html()

                if opts.get('dry_run'):
                    self.stdout.write(self.style.WARNING(f'DRY-RUN: would email {recipients}: "{subject}"'))
                    return

                try:
                    frm = (getattr(settings, 'OMNI_FROM_EMAIL', '')
                           or getattr(settings, 'DEFAULT_FROM_EMAIL', 'Omni ERP <omni@alphadirect.co.bw>'))
                    msg = EmailMultiAlternatives(subject=subject, body='(see the HTML version of this email)',
                                                 from_email=frm, to=recipients)
                    msg.attach_alternative(html, 'text/html')
                    sent = msg.send()
                    self.stdout.write(self.style.SUCCESS(
                        f'Urgent denied-access alert emailed to {len(recipients)} recipient(s) '
                        f'[{", ".join(recipients)}]: send()={sent}.'))
                except Exception as send_exc:    # noqa: BLE001
                    self.stderr.write(self.style.WARNING(f'Urgent denied-access email send failed (non-fatal): {send_exc}'))
                return

            self.stderr.write(self.style.WARNING(f'Live probe failed unexpectedly (non-fatal): {exc}'))
            if expiry is None:
                return
        else:
            self.stdout.write(self.style.SUCCESS(
                'OK — token carries no readable exp claim but the live probe succeeded.'
                if expiry is None else 'OK — the live probe succeeded.'))
            if expiry is None:
                return

        now       = timezone.now()
        days_left = (expiry - now).days
        expired   = expiry <= now
        window    = opts.get('window_days')
        if window is None:
            window = int(getattr(settings, 'TIMEDOCTOR_RENEWAL_WINDOW_DAYS', 7) or 7)

        self.stdout.write(f'Token expiry {expiry:%Y-%m-%d %H:%M} UTC; days_left={days_left}; window={window}.')

        if not (opts.get('force') or expired or days_left <= window):
            self.stdout.write(self.style.SUCCESS('OK — outside the reminder window, no email sent.'))
            return

        recipients  = _renewal_recipients()
        expiry_date = f'{expiry:%d %b %Y}'
        subject = ('URGENT: Time Doctor token EXPIRED — omni workforce reports are down'
                   if expired else
                   f'ACTION: Time Doctor token expires in {days_left} day(s) — renew to keep omni running')
        html = _body_html(expiry_date=expiry_date, days_left=max(days_left, 0), expired=expired)

        if opts.get('dry_run'):
            self.stdout.write(self.style.WARNING(f'DRY-RUN: would email {recipients}: "{subject}"'))
            return

        try:
            frm = (getattr(settings, 'OMNI_FROM_EMAIL', '')
                   or getattr(settings, 'DEFAULT_FROM_EMAIL', 'Omni ERP <omni@alphadirect.co.bw>'))
            msg = EmailMultiAlternatives(subject=subject, body='(see the HTML version of this email)',
                                         from_email=frm, to=recipients)
            msg.attach_alternative(html, 'text/html')
            sent = msg.send()
            self.stdout.write(self.style.SUCCESS(
                f'Reminder emailed to {len(recipients)} recipient(s) [{", ".join(recipients)}]: send()={sent}.'))
        except Exception as exc:    # noqa: BLE001
            self.stderr.write(self.style.WARNING(f'Reminder send failed (non-fatal): {exc}'))
