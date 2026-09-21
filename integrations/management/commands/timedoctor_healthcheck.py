"""
integrations/management/commands/timedoctor_healthcheck.py

Daily watchdog on the whole Time Doctor pipeline (CFO 2026-07-21: "do a daily
check on Time Doctor ... I don't want messups"). Emails a PASS/FAIL heartbeat to
excoboard@alphadirect.co.bw every day so a silent breakage (like the daily brief
that showed 0h because it ran before the pull) is caught the next morning.

It is a HEARTBEAT: it emails EVERY day, PASS or FAIL — the absence of the email
is itself a signal. Never raises (cron stays green); a crash is reported as FAIL.

What it checks:
  A. Live Time Doctor reachability + token — actually calls the API now.
  B. Latest stored snapshot is FRESH (<= 2 days old) and NON-EMPTY.
  C. Token days-to-expiry.
  D. Matching health — how many Time Doctor accounts link to a real employee.
  E. Sample test — a few tracked employees and their latest hours.

  python manage.py timedoctor_healthcheck              # email excoboard@
  python manage.py timedoctor_healthcheck --dry-run    # print, no email
  python manage.py timedoctor_healthcheck --to me@x    # override recipient (test)
"""
from __future__ import annotations

import datetime
import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.core.management.base import BaseCommand
from django.utils import timezone

log = logging.getLogger(__name__)

DEFAULT_TO = ['excoboard@alphadirect.co.bw']
MIN_ROWS = 50            # a healthy pull covers the whole roster (~100+); < 50 is suspect
STALE_DAYS = 2           # newest snapshot older than this = the pull has stopped
TOKEN_WARN_DAYS = 14     # warn when the API token is within this many days of expiry
SAMPLE_N = 5

NAVY, ORANGE, INK, MUT, RED, GREEN, AMBER = (
    '#0D1B2A', '#F4A623', '#1F2937', '#6B7280', '#DC2626', '#059669', '#B45309')


class Check:
    """One line item in the report."""
    def __init__(self, name, level, detail):
        self.name, self.level, self.detail = name, level, detail   # level: PASS|WARN|FAIL


def run_checks():
    """Run every Time Doctor pipeline check and return the report data.

    Returns ``(worst, as_of_txt, checks, sample_rows, td_user_count)`` where
    ``worst`` is the overall PASS/WARN/FAIL status. Never raises — each check
    is individually guarded and a crash is recorded as a FAIL/WARN line — so the
    heartbeat's never-raise contract holds and the consolidated send_ops_digest
    can call this safely to decide whether the Time Doctor section appears.
    """
    checks: list[Check] = []
    sample_rows: list[tuple] = []

    # -- A. Live API reachability + roster ------------------------------
    td_user_count = None
    td_users = []
    try:
        from integrations.timedoctor import TimeDoctorClient
        client = TimeDoctorClient.from_settings()
        if not client.configured:
            checks.append(Check('Time Doctor API', 'FAIL', 'Not configured (no token / company id).'))
        else:
            td_users = client.users() or []
            td_user_count = len(td_users)
            if td_user_count == 0:
                checks.append(Check('Time Doctor API', 'FAIL', 'API reachable but returned 0 users.'))
            else:
                checks.append(Check('Time Doctor API', 'PASS',
                                    f'Reachable — {td_user_count} accounts returned.'))
    except Exception as exc:    # noqa: BLE001
        checks.append(Check('Time Doctor API', 'FAIL', f'Live call failed: {exc}'))

    # -- C. Token expiry -------------------------------------------------
    try:
        from integrations.timedoctor import TimeDoctorClient, jwt_expiry
        token = TimeDoctorClient.from_settings().token
        expiry = jwt_expiry(token) if token else None
        if expiry is None:
            checks.append(Check('API token expiry', 'WARN', 'No readable expiry on the token.'))
        else:
            days_left = (expiry - timezone.now()).days
            if expiry <= timezone.now():
                checks.append(Check('API token expiry', 'FAIL',
                                    f'EXPIRED on {expiry:%d %b %Y} — pull is dead until renewed.'))
            elif days_left <= TOKEN_WARN_DAYS:
                checks.append(Check('API token expiry', 'WARN',
                                    f'Expires in {days_left} day(s) ({expiry:%d %b %Y}) — renew soon.'))
            else:
                checks.append(Check('API token expiry', 'PASS',
                                    f'{days_left} days left ({expiry:%d %b %Y}).'))
    except Exception as exc:    # noqa: BLE001
        checks.append(Check('API token expiry', 'WARN', f'Could not read token expiry: {exc}'))

    # -- B. Latest snapshot freshness + size ----------------------------
    snap = None
    try:
        from integrations.models import TimeDoctorDailySnapshot
        snap = TimeDoctorDailySnapshot.objects.order_by('-as_of').first()
        today = timezone.localtime().date()
        if not snap:
            checks.append(Check('Stored data (pull)', 'FAIL', 'No Time Doctor snapshot has ever been stored.'))
        else:
            age = (today - snap.as_of).days
            rows = len(snap.payload or [])
            totals = snap.totals or {}
            if age > STALE_DAYS:
                checks.append(Check('Stored data (pull)', 'FAIL',
                                    f'Newest data is for {snap.as_of} ({age} days old) — the daily pull has stopped.'))
            elif rows < MIN_ROWS:
                checks.append(Check('Stored data (pull)', 'FAIL',
                                    f'Newest snapshot ({snap.as_of}) has only {rows} people — pull under-reporting.'))
            else:
                checks.append(Check('Stored data (pull)', 'PASS',
                                    f'Newest {snap.as_of} ({age}d old): {rows} people, '
                                    f'{totals.get("total_hours", "?")}h tracked, '
                                    f'{totals.get("active_users", "?")} active, '
                                    f'productive {totals.get("productive_pct", "?")}%.'))
            # informational: zero activity may just be a weekend/holiday → WARN not FAIL
            if snap and float(totals.get('total_hours') or 0) == 0:
                checks.append(Check('Activity on newest day', 'WARN',
                                    f'{snap.as_of} shows 0 total hours (weekend/holiday, or a bad pull — check).'))
    except Exception as exc:    # noqa: BLE001
        checks.append(Check('Stored data (pull)', 'FAIL', f'Could not read stored snapshot: {exc}'))

    # -- C. Nightly settle pull actually ran ----------------------------
    # The intraday hours-reminder pulls (2026-08-17) upsert TODAY's snapshot with
    # `payload` only and NO settle_samples, so they keep the newest `as_of` fresh
    # even if the nightly pull has died — which would leave section B green while
    # the 07:05 brief and 09:00 report read a partial day. Guard: at least one of
    # the last 3 completed days must carry a nightly settle slot.
    try:
        from integrations.models import TimeDoctorDailySnapshot
        today = timezone.localtime().date()
        nightly = {'0300', '0400', '0430', '0830'}
        recent = list(TimeDoctorDailySnapshot.objects
                      .filter(as_of__lt=today, as_of__gte=today - datetime.timedelta(days=3))
                      .order_by('-as_of'))
        with_slots = [s for s in recent if set((s.settle_samples or {}).keys()) & nightly]
        if not recent:
            checks.append(Check('Nightly settle pull', 'WARN',
                                'No completed-day snapshots in the last 3 days to check.'))
        elif with_slots:
            latest = with_slots[0]
            checks.append(Check('Nightly settle pull', 'PASS',
                                f'{latest.as_of} carries nightly slots '
                                f'{sorted(set((latest.settle_samples or {}).keys()) & nightly)}.'))
        else:
            checks.append(Check('Nightly settle pull', 'FAIL',
                                'No nightly settle slots in the last 3 completed days — the nightly '
                                'pull may have stopped while intraday pulls keep the date looking fresh.'))
    except Exception as exc:    # noqa: BLE001
        checks.append(Check('Nightly settle pull', 'WARN', f'Could not check settle slots: {exc}'))

    # -- D. Matching health ---------------------------------------------
    try:
        if td_users:
            from integrations.td_matching import TDMatcher, active_td_users
            from hris import eligibility
            emps = [p.employee for p in eligibility.tracking_profiles()
                    if getattr(p, 'employee', None)]
            m = TDMatcher(active_td_users(td_users), emps)
            matched = len(m.employee_for_uid)
            unmatched_td = len(m.unmatched_td)
            lvl = 'PASS' if unmatched_td <= max(5, int(len(td_users) * 0.15)) else 'WARN'
            checks.append(Check('Name/account matching', lvl,
                                f'{matched} TD accounts linked to staff; {unmatched_td} still unlinked '
                                f'(review in Who-tracks).'))
        else:
            checks.append(Check('Name/account matching', 'WARN', 'Skipped — no roster from the API.'))
    except Exception as exc:    # noqa: BLE001
        checks.append(Check('Name/account matching', 'WARN', f'Matching check failed: {exc}'))

    # -- E. Sample test --------------------------------------------------
    try:
        if snap and (snap.payload or []):
            rows = sorted((snap.payload or []),
                          key=lambda m: float(m.get('hours_tracked') or 0), reverse=True)
            for m in rows[:SAMPLE_N]:
                sample_rows.append((m.get('name') or '—',
                                    m.get('hours_tracked'),
                                    m.get('productivity', m.get('productive_pct', '—'))))
    except Exception as exc:    # noqa: BLE001
        log.warning('healthcheck sample failed: %s', exc)

    worst = 'FAIL' if any(c.level == 'FAIL' for c in checks) else \
            ('WARN' if any(c.level == 'WARN' for c in checks) else 'PASS')
    as_of_txt = timezone.localtime().strftime('%Y-%m-%d %H:%M SAST')
    return worst, as_of_txt, checks, sample_rows, td_user_count


# ---- rendering ------------------------------------------------------------
def render_text(worst, as_of_txt, checks, sample_rows):
    lines = [f'Time Doctor daily check: {worst}  ({as_of_txt})', '']
    for c in checks:
        lines.append(f'  [{c.level}] {c.name}: {c.detail}')
    if sample_rows:
        lines += ['', 'Sample (top trackers on the newest day):']
        for nm, hrs, prod in sample_rows:
            lines.append(f'  - {nm}: {hrs}h  (productive {prod})')
    return '\n'.join(lines)


def render_section(worst, as_of_txt, checks, sample_rows):
    """The health block only (status band + checks table + sample) — the piece
    that can be embedded inside a larger email such as the consolidated ops
    digest. render_html() wraps this verbatim in the full standalone document,
    so the standalone heartbeat's output is unchanged."""
    from django.utils.html import escape
    band = {'PASS': GREEN, 'WARN': AMBER, 'FAIL': RED}[worst]
    head = {'PASS': 'All good — Time Doctor is healthy',
            'WARN': 'Working, but check the warnings',
            'FAIL': 'PROBLEM — Time Doctor needs attention'}[worst]
    rows = ''
    for c in checks:
        dot = {'PASS': GREEN, 'WARN': AMBER, 'FAIL': RED}[c.level]
        rows += (f'<tr><td style="padding:8px 10px;border-bottom:1px solid #EEF0F3;white-space:nowrap">'
                 f'<span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:{dot}"></span> '
                 f'<b style="color:{INK}">{escape(c.name)}</b></td>'
                 f'<td style="padding:8px 10px;border-bottom:1px solid #EEF0F3;color:{INK};font-size:13px">'
                 f'{escape(c.detail)}</td></tr>')
    sample = ''
    if sample_rows:
        srows = ''.join(
            f'<tr><td style="padding:4px 10px;color:{INK};font-size:13px">{escape(str(nm))}</td>'
            f'<td style="padding:4px 10px;color:{INK};font-size:13px">{escape(str(hrs))}h</td>'
            f'<td style="padding:4px 10px;color:{MUT};font-size:13px">productive {escape(str(prod))}</td></tr>'
            for nm, hrs, prod in sample_rows)
        sample = (f'<h3 style="color:{NAVY};font-size:14px;margin:16px 0 4px">Sample — top trackers on the newest day</h3>'
                  f'<table style="border-collapse:collapse;width:100%">{srows}</table>')
    return f"""<div style="border-left:6px solid {band};padding:16px 24px">
    <div style="font-size:17px;font-weight:700;color:{NAVY}">{head}</div>
    <div style="color:{MUT};font-size:12px;margin-top:2px">{escape(as_of_txt)}</div>
    <table style="border-collapse:collapse;width:100%;margin-top:12px">{rows}</table>
    {sample}
    <p style="color:{MUT};font-size:11px;margin-top:16px">Automatic daily check. If this email stops arriving, the check itself has stopped — investigate.</p>
  </div>"""


def render_html(worst, as_of_txt, checks, sample_rows, td_user_count=None):
    # td_user_count is accepted for call-signature compatibility; the layout
    # does not surface it (pre-existing — left untouched per surgical rule).
    section = render_section(worst, as_of_txt, checks, sample_rows)
    return f"""<!doctype html><html><body style="margin:0;background:#EEF0F3;font-family:'Segoe UI',Arial,sans-serif">
<div style="max-width:660px;margin:0 auto;background:#fff">
  <div style="background:{NAVY};padding:16px 24px">
    <div style="color:{ORANGE};font-size:18px;font-weight:700">Alpha Direct — omni</div>
    <div style="color:#AEB6C2;font-size:13px;margin-top:2px">Time Doctor daily health check</div>
  </div>
  {section}
</div></body></html>"""


class Command(BaseCommand):
    help = 'Daily Time Doctor pipeline health check → emails a PASS/FAIL heartbeat to EXCO.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Print the report, do not email.')
        parser.add_argument('--to', dest='to', default='', help='Override recipient(s), comma-separated (test).')

    def handle(self, *args, **opts):
        # Consolidated ops email (CFO 2026-08-05): when ON, send_ops_digest owns
        # the daily Time Doctor health line (and shows it ONLY when not PASS), so
        # this standalone heartbeat self-skips. Flag OFF ⇒ unchanged, incl. the
        # daily PASS heartbeat whose absence is itself a signal. A --dry-run still
        # computes/prints so the check stays testable by hand.
        worst, as_of_txt, checks, sample_rows, td_user_count = run_checks()

        if (getattr(settings, 'CONSOLIDATED_EMAILS_ENABLED', False)
                and not opts.get('dry_run')):
            if worst != 'FAIL':
                self.stdout.write('CONSOLIDATED_EMAILS_ENABLED on — send_ops_digest '
                                  'covers the Time Doctor health line; skipping heartbeat.')
                return
            self.stdout.write('CONSOLIDATED_EMAILS_ENABLED on but check FAILED — sending heartbeat.')

        subject = f'Time Doctor daily check — {worst} — {timezone.localtime().date()}'

        html = render_html(worst, as_of_txt, checks, sample_rows, td_user_count)
        text = render_text(worst, as_of_txt, checks, sample_rows)

        if opts.get('dry_run'):
            self.stdout.write(text)
            self.stdout.write(self.style.WARNING('DRY-RUN: not emailed.'))
            return

        to = ([a.strip() for a in opts['to'].split(',') if a.strip()]
              if opts.get('to') else list(getattr(settings, 'TIMEDOCTOR_HEALTHCHECK_TO', []) or DEFAULT_TO))
        try:
            frm = (getattr(settings, 'OMNI_FROM_EMAIL', '')
                   or getattr(settings, 'DEFAULT_FROM_EMAIL', 'Omni ERP <omni@alphadirect.co.bw>'))
            msg = EmailMultiAlternatives(subject=subject, body=text, from_email=frm, to=to)
            msg.attach_alternative(html, 'text/html')
            msg.send()
            self.stdout.write(self.style.SUCCESS(f'{worst}: emailed check to {", ".join(to)}.'))
        except Exception as exc:    # noqa: BLE001
            self.stderr.write(self.style.WARNING(f'Health check computed {worst} but email failed: {exc}'))
