"""Monday-morning adoption email to Underwriting (CFO Amendment 3, 2026-09-08).

One email per underwriter, the Underwriting Manager copied. It says what they
renewed last week, how many quotes they actually built in the Quote builder over
the same week, and what the gap between the two cost them in time.

Tone: firm, and it is MANAGEMENT that is watching adoption. The CFO is never
named as the enforcer in an automated Omni email — the point is the standard,
not a person (house rule).

Scope of the figures, all decided 2026-09-08:
  * last 7 days
  * RENEWALS only
  * the Domestic & Commercial book only — MIS / Unicoin and motor are out

Run it by hand first:
    python manage.py underwriting_adoption_email --dry-run
"""
from django.core.management.base import BaseCommand
from django.utils.html import escape

from underwriting import adoption

NAVY = '#0D1B2A'
ORANGE = '#F4A623'


def _n(value) -> str:
    """A whole number with thousands separators — house format."""
    return f'{int(value or 0):,}'


def _when(dt) -> str:
    """'12 Aug 2026' — or plain words when the tool has never been opened."""
    return f'{dt:%d %b %Y}' if dt else 'never'


def subject_for(row, window) -> str:
    end = window['end']
    return f"Underwriting tools — your week to {end:%d %b %Y}"


def body_for(row, team, window) -> str:
    """The HTML body for ONE underwriter.

    THREE shapes, and which one is used matters:

      CHASE   — renewals went out that the Quote builder did not touch, or the
                person has never opened either tool. The firm one.
      CLEAN   — we KNOW the renewals and they went through the tools.
      UNKNOWN — Graphite could not give us the renewal count. Neither of the
                above may be claimed: the first dry-run of this command told a
                person with 2 quotes and no known renewals that "your renewals
                went through the tools this week", which we had no way of
                knowing. So this shape states the standard and states the tool
                figures, and claims nothing about the week.

    All three carry the inputs beside the score, so the number is never
    something to take on trust.
    """
    end = window['end']
    start = window['start']
    renewals_known = bool(team['renewals_available'])
    gap = int(row['gap'] or 0)
    chase = (not row['ever_used']) or (renewals_known and gap > 0)

    head = (
        f"<h2 style='color:{NAVY};border-bottom:2px solid {ORANGE};"
        f"padding-bottom:6px;margin:0 0 14px'>Your underwriting week</h2>"
        f"<p>{escape(row['name'])},</p>"
    )

    if renewals_known:
        facts = (
            f"<p>Between {start:%d %b} and {end:%d %b %Y}:</p>"
            f"<ul>"
            f"<li>You renewed <b>{_n(row['renewals'])}</b> policies "
            f"(Domestic &amp; Commercial).</li>"
            f"<li>You created <b>{_n(row['quotes'])}</b> quotes in the Quote builder.</li>"
            f"<li>You issued <b>{_n(row['documents'])}</b> documents from the "
            f"Document Generator.</li>"
            f"</ul>"
        )
    else:
        # Never print a zero we did not measure — a "0 renewals" line the person
        # knows is wrong is how a whole report loses its authority.
        facts = (
            f"<p>Between {start:%d %b} and {end:%d %b %Y}:</p>"
            f"<ul>"
            f"<li>You created <b>{_n(row['quotes'])}</b> quotes in the Quote builder.</li>"
            f"<li>You issued <b>{_n(row['documents'])}</b> documents from the "
            f"Document Generator.</li>"
            f"</ul>"
            f"<p class='small'>{escape(team['renewals_note'])} Your renewal count "
            f"is not in this email as a result — the tool figures above are exact.</p>"
        )

    score = ('not scored — no renewals to measure against'
             if row['readiness'] is None
             else f"<b>{row['readiness']}</b> out of 100 ({escape(row['readiness_band'])})")
    score_block = (
        f"<p>AI readiness: {score}.</p>"
        f"<p class='small'>Readiness is simply the share of your renewals that went "
        f"through the Quote builder. The two figures it is worked out from are the "
        f"ones listed above — nothing else goes into it.</p>"
    )

    if chase:
        if renewals_known and gap > 0:
            point = (
                f"<p><b>{_n(gap)}</b> of those renewals did not go through the Quote "
                f"builder. That is {_n(gap)} quotes written out by hand. The tool "
                f"produces the same quotation in under a minute, on the one standard "
                f"template, with the VAT worked out for you.</p>"
            )
        else:
            point = (
                f"<p>You have not used the Quote builder or the Document Generator at "
                f"all. Last quote built: {_when(row['quotes_last_used'])}. Last "
                f"document issued: {_when(row['documents_last_used'])}. Both tools do "
                f"in under a minute what is currently being done by hand.</p>"
            )
        close = (
            "<p>Management is monitoring adoption of these tools week by week. Please "
            "use them for your renewals this week. If something in either tool is "
            "getting in your way, reply to your manager and it will be fixed.</p>"
        )
    elif renewals_known:
        point = (
            "<p>Your renewals went through the tools this week. Thank you — that is "
            "exactly what they are for.</p>"
        )
        close = (
            "<p>Management is monitoring adoption of these tools week by week. Nothing "
            "is needed from you.</p>"
        )
    else:
        point = (
            "<p>The standard is that every Domestic &amp; Commercial renewal is "
            "quoted in the Quote builder. It produces the quotation in under a "
            "minute, on the one standard template, with the VAT worked out for you.</p>"
        )
        close = (
            "<p>Management is monitoring adoption of these tools week by week.</p>"
        )

    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'><style>"
        "body{font-family:'Segoe UI',Arial,sans-serif;color:#1F2937;font-size:14px;"
        "line-height:1.5;max-width:640px}"
        f"ul{{margin:8px 0 14px;padding-left:20px}} li{{margin:3px 0}}"
        ".small{color:#6B7280;font-size:12px}"
        "</style></head><body>"
        f"<div style='background:{NAVY};padding:12px 16px'>"
        f"<span style='color:{ORANGE};font-size:16px;font-weight:600'>"
        f"Alpha Direct — Underwriting</span></div>"
        "<div style='padding:16px'>"
        f"{head}{facts}{score_block}{point}{close}"
        "</div></body></html>"
    )


class Command(BaseCommand):
    help = ("Monday adoption email to each underwriter (UW manager copied). "
            "Use --dry-run to print the emails instead of sending them.")

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Print the rendered emails; send nothing.')
        parser.add_argument('--days', type=int, default=adoption.WINDOW_DAYS,
                            help='Length of the window in days (default 7).')

    def handle(self, *args, **opts):
        dry = bool(opts['dry_run'])
        rep = adoption.weekly_report(days=int(opts['days']))
        all_rows, team, window = rep['rows'], rep['team'], rep['window']
        managers = adoption.manager_emails()

        # The screen shows the whole department; the email does NOT chase the
        # manager for not building quotes — she is the COPY recipient, which is
        # what the CFO decided ("each underwriter individually, with the UW
        # manager copied"). The first dry-run addressed her to herself.
        rows = [r for r in all_rows if not r['is_manager']]

        if not rows:
            self.stdout.write(self.style.WARNING(
                'No Underwriting staff with an Omni login on the register — '
                'nothing to send. Check payroll department names.'))
            return

        if team['renewals_unattributed']:
            # Pre-authorised fallback (CFO Amendment 3): Graphite could not tie
            # these renewals to a person, so they are reported as a team total
            # rather than guessed onto somebody.
            self.stdout.write(self.style.WARNING(
                f"{_n(team['renewals_unattributed'])} of "
                f"{_n(team['renewals_total'])} renewals could not be attributed "
                f"to a person — reported as a team total only."))

        sent = 0
        for row in rows:
            subject = subject_for(row, window)
            html = body_for(row, team, window)
            to = [row['email']] if row['email'] else []
            # Same cc list the send uses, so --dry-run shows the real envelope.
            cc = [a for a in managers if a.lower() != (row['email'] or '').lower()]

            if dry:
                self.stdout.write('=' * 72)
                self.stdout.write(f"To:      {', '.join(to) or '(no address — skipped)'}")
                self.stdout.write(f"Cc:      {', '.join(cc) or '(no UW manager on the register)'}")
                self.stdout.write(f"Subject: {subject}")
                self.stdout.write(_as_text(html))
                continue

            if not to:
                self.stdout.write(self.style.WARNING(
                    f"{row['name']}: no email address on the register — skipped."))
                continue

            from core.notifications import send_html_with_cfo_cc
            # cc_cfo=False: the decision was "the UW manager copied". A weekly
            # copy of every underwriter's chase mail is not an EXCO matter
            # (CFO directive 2026-06-17, same as the lapsing-quote reminder).
            sent += send_html_with_cfo_cc(subject, html, to, cc=cc, cc_cfo=False)

        verb = 'would send' if dry else 'sent'
        self.stdout.write(self.style.SUCCESS(
            f"underwriting adoption: {len(rows)} underwriter(s), "
            f"{team['never_used']} who have never used either tool, "
            f"{_n(team['quotes_total'])} quote(s) built, "
            f"{_n(team['renewals_total'])} renewal(s) in the window "
            f"({'from Graphite' if team['renewals_available'] else 'GRAPHITE UNAVAILABLE'}) "
            f"— {verb} {len(rows) if dry else sent} email(s)"))


def _as_text(html: str) -> str:
    """Flatten the HTML so --dry-run prints something a person can read.

    Console-only; the real email is the HTML above. Kept dumb on purpose — a
    real HTML-to-text library would be a dependency for a debug printout.
    """
    import re
    from html import unescape
    s = re.sub(r'(?s)<(style|head)\b.*?</\1>', '', html)
    s = re.sub(r'<li\b[^>]*>', '\n  - ', s)
    s = re.sub(r'</(p|h2|ul|div)>', '\n', s)
    s = re.sub(r'<[^>]+>', '', s)
    s = unescape(s)
    return '\n'.join(line.strip() for line in s.splitlines() if line.strip())
