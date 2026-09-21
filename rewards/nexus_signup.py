"""rewards/nexus_signup.py — Alpha Nexus tester sign-up tracking + reminders.

"Signed up" = the invited email has a RewardMember with at least one
CustomerSession (i.e. they completed the email-OTP login at least once).
Used for the CFO's not-signed-up list and the Monday 08:00 reminder.
"""
from __future__ import annotations

# Canonical invited-tester list (the launch blast recipients).
NEXUS_TESTERS = [
    'leone999nkola@gmail.com', 'cbamusi@insurance.co.bw', 'bbalasubramanian@alphadirect.co.bw',
    'ubutale@alphadirect.co.bw', 'kamyaarun@gmail.com', 'dmotlhabane@alphadirect.co.bw',
    'mmolefe@insurance.co.bw', 'ktshutlhedi@alphadirect.co.bw', 'pkago@alphadirect.co.bw',
    'Mtlagae@alphadirect.co.bw', 'lntabeni@alphadirect.co.bw', 'tchimidza@alphadirect.co.bw',
    'dikgopoleng@alphadirect.co.bw', 'omogomotsi@alphadirect.co.bw', 'rtonkope@alphadirect.co.bw',
    'lkeotlhoboge@alphadirect.co.bw', 'gmakone@alphadirect.co.bw', 'ogalotshoge@alphadirect.co.bw',
    'isechele@alphadirect.co.bw', 'kgaothobogwe@alphadirect.co.bw', 'ckelefatse@alphadirect.co.bw',
    'pphesodi@alphadirect.co.bw', 'bily.p@air-consult.co.za', 'arjuniyer@alphadirect.co.bw',
]

APP_URL = 'https://omni.alphadirect.co.bw/m'
ANDROID_URL = 'https://omni.alphadirect.co.bw/m/get/android'
IOS_URL = 'https://omni.alphadirect.co.bw/m/get/ios'


def signup_status() -> dict:
    """Split the invited testers into signed-up vs not (by login session)."""
    from .models import RewardMember
    signed, pending = [], []
    for email in NEXUS_TESTERS:
        m = RewardMember.objects.filter(email__iexact=email).first()
        if m and m.sessions.exists():
            signed.append(email)
        else:
            pending.append(email)
    return {'signed_up': signed, 'not_signed_up': pending,
            'signed_count': len(signed), 'pending_count': len(pending), 'total': len(NEXUS_TESTERS)}


def _reminder_html(first: str) -> str:
    return f"""\
<div style="font-family:'Book Antiqua',Georgia,serif;max-width:600px;margin:0 auto;background:#F3F4F6;padding:24px;">
  <div style="background:#0D1B2A;border-radius:14px 14px 0 0;padding:24px 28px;">
    <div style="color:#fff;font-size:22px;font-weight:bold;">Alpha&nbsp;Nexus</div>
    <div style="color:#F4A623;font-size:14px;margin-top:4px;">You haven&rsquo;t joined yet &mdash; the leaderboard is moving without you</div>
  </div>
  <div style="background:#fff;border:1px solid #E5E7EB;border-top:none;border-radius:0 0 14px 14px;padding:28px;">
    <p style="margin:0 0 14px;">Hi {first},</p>
    <p style="margin:0 0 16px;font-size:15px;line-height:1.6;">We noticed you haven&rsquo;t signed in to <b>Alpha&nbsp;Nexus</b> yet. Your colleagues are already earning points and climbing toward the prizes (<b>BWP&nbsp;5,000 / 3,000 / 1,000</b>). Install the app and get on the board &mdash; it takes about a minute.</p>
    <p style="margin:0 0 8px;font-weight:bold;color:#0D1B2A;">Install the app</p>
    <ul style="margin:0 0 16px;padding-left:20px;line-height:1.8;font-size:15px;">
      <li>📱 <b>Android:</b> <a href="{ANDROID_URL}" style="color:#C2410C;">download &amp; install here</a> (app + step-by-step).</li>
      <li>🍎 <b>iPhone:</b> <a href="{IOS_URL}" style="color:#C2410C;">install from Safari here</a> (Add to Home Screen).</li>
    </ul>
    <p style="margin:0 0 16px;font-size:14px;">Sign in with <b>any email &mdash; a personal Gmail is fine, no Office&nbsp;365 needed.</b> You get a 6-digit code, and you&rsquo;re in.</p>
    <p style="margin:0;">See you on the leaderboard.<br>Regards,<br>Alpha&nbsp;Nexus</p>
  </div>
  <div style="text-align:center;color:#9CA3AF;font-size:11px;padding:14px;">Alpha Direct Insurance Company (Pty) Ltd &middot; Botswana</div>
</div>"""


def send_signup_reminders() -> dict:
    """Email a reminder (with app-install links) to every not-signed-up tester."""
    from core.notifications import send_html_with_cfo_cc
    st = signup_status()
    sent = 0
    for email in st['not_signed_up']:
        first = email.split('@')[0].split('.')[0].title()
        send_html_with_cfo_cc(
            'Reminder: install Alpha Nexus & sign in',
            _reminder_html(first), to=[email],
            text_fallback=f"You haven't signed in to Alpha Nexus yet. Install: Android {ANDROID_URL} | "
                          f"iPhone {IOS_URL}. Sign in with any email (Gmail fine). Prizes: BWP 5,000 / 3,000 / 1,000.",
            cc_cfo=False)
        sent += 1
    return {'reminded': sent, **st}
