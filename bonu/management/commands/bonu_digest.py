"""
Monday morning: five things to ask this week, and the money at stake.

CFO recommendation 9: a dashboard only works for the person who remembers to open it. The
digest is the opposite — it arrives, it is short, and every line is something a person can
act on today. No logging in, no navigation, no "the data is available on the portal".

    python manage.py bonu_digest                     # print it, send nothing
    python manage.py bonu_digest --send               # email the CFO
    python manage.py bonu_digest --send --to a@b.c    # and whoever else needs it

It sends nothing unless `--send` is passed, so a cron can be added later without any risk
of surprise mail while the module is still being set up.
"""
from __future__ import annotations

from decimal import Decimal

from collections import defaultdict

from django.core.management.base import BaseCommand
from django.utils import timezone

Z = Decimal('0')
TOP_N = 5


class Command(BaseCommand):
    help = 'The weekly BONU digest: what to ask this week and what it is worth.'

    def add_arguments(self, parser):
        parser.add_argument('--send', action='store_true',
                            help='Actually email it. Without this the digest only prints.')
        parser.add_argument('--to', default='', help='Extra recipients, comma separated.')
        # Every outbound email gets looked at before it goes (prat-skill §14). Without this the
        # only way to see the digest was to send it to somebody.
        parser.add_argument('--preview-file', dest='preview_file',
                            help='Write the email to a file instead of sending it.')

    def handle(self, *args, **opts):
        from bonu import panel as P
        from bonu import queries as Q
        from bonu.models import (ApprovalThreshold, BonuFinding, BonuInvoiceLine,
                                 IngestedDocument, LawFirm, LegalCase, QueryLetter,
                                 RetainerAgreement)
        from bonu.retainer import scorecard

        today = timezone.localdate()
        lines = list(BonuInvoiceLine.objects.select_related('invoice', 'invoice__firm'))
        thresholds = list(ApprovalThreshold.objects.all())
        letters = list(QueryLetter.objects.select_related('firm'))

        asks = []          # (money_at_stake, one plain sentence)

        # 1. Retainers not earning their fee — the biggest single number in the module.
        for r in RetainerAgreement.objects.filter(is_active=True).select_related('firm'):
            card = scorecard(r, list(LegalCase.objects.filter(retainer=r)), today)
            unearned = Decimal(str(card.get('retainer_not_earned') or 0))
            if unearned > Z:
                asks.append((unearned,
                             f"{r.firm.name} is carrying {card.get('cases_open')} cases against "
                             f"{r.committed_cases} committed. That is P{unearned:,.0f} of this "
                             f"month's fee not earned — ask them for the case list."))
            slip = card.get('slippage') or {}
            quiet = len(slip.get('stale') or [])
            if quiet:
                asks.append((Z, f'{r.firm.name} has {quiet} case(s) with no movement for over '
                                f'{r.max_days_no_activity} days. Ask why.'))

        # 2. What the checks find RIGHT NOW.
        #
        # This used to read only SAVED findings — and the rules compute on the fly, so nothing
        # was ever in that table. The digest printed "0 things to ask, P0 at stake" while two
        # members sat P544,000 over the benefit limit. A weekly email that reassures you while
        # the screen is red is worse than no email, so the digest now runs the same checks the
        # screen runs, and still adds anything already saved.
        from bonu.forensics import run_rules
        from bonu.member_rules import run_member_rules
        from bonu.models import RetainerAgreement as _RA
        live_lines = list(BonuInvoiceLine.objects.select_related('invoice', 'invoice__firm')[:5000])
        fees = defaultdict(list)
        for r in _RA.objects.filter(is_active=True):
            fees[str(r.firm_id)].append(r.monthly_fee)
        live = [f for f in run_rules(live_lines, retainer_fees=fees)
                if (f.get('amount_at_risk') or Z) > Z]
        live += [f for f in run_member_rules(live_lines)
                 if (f.get('amount_at_risk') or Z) > Z]
        for f in sorted(live, key=lambda x: -(x.get('amount_at_risk') or Z))[:TOP_N]:
            firm_name = getattr(f.get('firm'), 'name', 'A firm')
            # No amount in the sentence — the table prints it in its own column, and twice in
            # one row reads like a mistake.
            asks.append((f['amount_at_risk'], f"{firm_name}: {f['title']}"))

        open_findings = list(BonuFinding.objects.filter(resolved=False, queries=None)
                             .select_related('firm')[:200])
        for f in sorted(open_findings, key=lambda x: -(x.amount_at_risk or Z))[:TOP_N]:
            asks.append((f.amount_at_risk or Z,
                         f'{getattr(f.firm, "name", "A firm")}: {f.title} — no query raised yet.'))

        # 3. Letters that are past their reply date.
        for c in Q.chase_list(letters, today)[:TOP_N]:
            asks.append((Decimal(str(c['amount_queried'] or 0)),
                         f"{c['firm']} has not answered {c['reference']} — "
                         f"{c['days_overdue']} day(s) late"
                         + (' — escalate to the CFO.' if c['escalate_to_cfo'] else '.')))

        # 4. Cases running hot against their own type.
        for w in P.case_early_warnings(lines, thresholds)[:TOP_N]:
            if w.get('running_hot') or w.get('over_approval'):
                asks.append((w.get('spend') or Z, w['message']))

        # 5. Bills sitting unconfirmed.
        waiting = IngestedDocument.objects.filter(status='parsed').count()
        needs_ocr = IngestedDocument.objects.filter(status='needs_ocr').count()
        if waiting:
            asks.append((Z, f'{waiting} bill(s) have been read and are waiting for the '
                            f'accountant to confirm.'))
        if needs_ocr:
            asks.append((Z, f'{needs_ocr} bill(s) arrived as scans and cannot be read. Ask those '
                            f'firms to send a spreadsheet or a proper PDF.'))

        asks.sort(key=lambda a: -a[0])
        top = asks[:TOP_N]
        at_stake = sum(a[0] for a in asks)

        recovery = Q.recovery_summary(letters)
        header = (f'BONU — week of {today:%d %B %Y}: {len(asks)} thing(s) to ask, '
                  f'P{at_stake:,.0f} at stake.')

        self.stdout.write(self.style.MIGRATE_HEADING(header))
        if not top:
            self.stdout.write('  Nothing to chase. Either the panel is behaving or no detail is '
                              'loaded yet.')
        for i, (amount, line) in enumerate(top, start=1):
            self.stdout.write(f'  {i}. {line}')
        self.stdout.write(f"  Recovered so far: P{recovery['amount_recovered']:,.0f} of "
                          f"P{recovery['amount_queried']:,.0f} queried.")

        if not opts['send'] and not opts.get('preview_file'):
            self.stdout.write(self.style.WARNING('\nNot sent — pass --send to email it.'))
            return

        rows = ''.join(
            f'<tr><td style="padding:7px 10px;border:1px solid #EAEEF3;width:34px">{i}</td>'
            f'<td style="padding:7px 10px;border:1px solid #EAEEF3">{line}</td>'
            f'<td style="padding:7px 10px;border:1px solid #EAEEF3;text-align:right;'
            f'white-space:nowrap">{("P%s" % f"{amount:,.0f}") if amount else "&mdash;"}</td></tr>'
            for i, (amount, line) in enumerate(top, start=1)) or (
            '<tr><td colspan="3" style="padding:10px;border:1px solid #EAEEF3">Nothing to chase '
            'this week.</td></tr>')

        body = f"""
<p style="margin:0 0 12px">Five things worth asking this week on the BONU legal benefit.
Total at stake: <b>P{at_stake:,.0f}</b>.</p>
<table style="width:100%;font-size:13px;border-collapse:collapse;margin:0 0 14px">{rows}</table>
<p style="margin:0 0 12px;font-size:13px">Queried to date <b>P{recovery['amount_queried']:,.0f}</b>
&middot; recovered <b>P{recovery['amount_recovered']:,.0f}</b>
&middot; still waiting on {recovery['still_waiting']} letter(s).</p>
<p style="margin:0;font-size:12px;color:#6B7280">Read-only. Nothing in this email posts a journal
or pays anything.</p>"""

        if opts.get('preview_file'):
            with open(opts['preview_file'], 'w') as fh:
                fh.write(_house(header, body))
            self.stdout.write(self.style.SUCCESS(f"\nWrote preview → {opts['preview_file']}"))
            return

        to = ['cfo@alphadirect.co.bw'] + [a.strip() for a in (opts['to'] or '').split(',')
                                          if a.strip()]
        try:
            from core.notifications import send_html_with_cfo_cc
            sent = send_html_with_cfo_cc(subject=header, html=_house(header, body), to=to)
            self.stdout.write(self.style.SUCCESS(f'\nSEND RESULT: {sent} → {", ".join(to)}'))
        except Exception as exc:                      # noqa: BLE001
            self.stderr.write(f'Could not send: {type(exc).__name__}: {exc}')


def _house(title, body_html):
    """The house email shell — navy header, orange title, Book Antiqua."""
    return (
        '<!doctype html><html><head><meta charset="utf-8"></head>'
        '<body style="margin:0;background:#EEF2F7;font-family:\'Book Antiqua\',Palatino,Georgia,'
        'serif"><div style="max-width:700px;margin:16px auto;background:#fff;border-radius:14px;'
        'overflow:hidden"><div style="background:#0D1B2A;padding:20px 24px">'
        '<div style="font-size:11px;color:#FFD98A;letter-spacing:.06em;text-transform:uppercase">'
        'Alpha Direct &middot; BONU legal benefit</div>'
        f'<div style="font-size:19px;font-weight:800;color:#F4A623;margin-top:4px">{title}</div>'
        '</div><div style="padding:20px 24px;color:#1F2937;font-size:14px;line-height:1.65">'
        f'{body_html}</div></div></body></html>')
