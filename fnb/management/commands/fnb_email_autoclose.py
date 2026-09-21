"""
fnb_email_autoclose — close paid payment requests straight from FNB's confirmation
emails (CFO 2026-08-23).

FNB emails the CFO mailbox a per-payment result for every OnceOff payment; the ones
that say "Fully Processed" are genuinely paid. This command reads those emails
(reusing the Mail.Read reader app the auto-reply command already uses), and for each
one that UNAMBIGUOUSLY matches an open payment request (exact amount + a reference
overlap), marks that request PAID via _mark_paid_from_bank — an audited terminal
state (NOT 'cancelled', so the paid request still counts in the duplicate-payment
control). Anything ambiguous or unmatched is left for a human.

Safety:
  * Gated on FNB_EMAIL_AUTOCLOSE_ENABLED (default OFF) — ships dormant.
  * Only acts on status='Fully Processed'; only on PENDING_CFO requests.
  * Only an EXACT amount + reference match; never amount alone; a shared-amount
    clash is reported, not closed.
  * --dry-run shows what it would mark paid and writes nothing.
  * Re-run safe: an email is re-read across the look-back window, but once its
    request is PAID the matcher sees the terminal match and returns "none" — it
    never falls through to an open same-vendor/same-amount sibling.

Schedule (CFO): twice a day, 09:00 and 16:30 Botswana time, via
infra/cron/fnb-email-autoclose.cron (install with infra/install-crons.sh).

Usage:
    python manage.py fnb_email_autoclose --dry-run
    python manage.py fnb_email_autoclose            # apply (needs the setting ON)
    python manage.py fnb_email_autoclose --hours 72 # widen the look-back window
"""
from __future__ import annotations

import logging
from datetime import timedelta

import requests
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from core.management.commands.auto_reply_omni_mail import _reader_token, GRAPH_BASE
from fnb.email_reconcile import parse_result_email, match_paid_email
from taskboard.models import PaymentRequest
from taskboard.payment_views import _mark_paid_from_bank, _cfo_user

log = logging.getLogger(__name__)

_FNB_SENDER = 'noreply@fnb.co.za'


def _fetch_fnb_emails(mailbox: str, hours: int) -> list[dict]:
    """FNB result emails (with body) received in the last `hours`, newest first."""
    since = (timezone.now() - timedelta(hours=hours)).strftime('%Y-%m-%dT%H:%M:%SZ')
    url = (
        f'{GRAPH_BASE}/users/{mailbox}/messages'
        f'?$filter=receivedDateTime ge {since}'
        f'&$select=id,subject,from,sender,receivedDateTime,body,bodyPreview'
        f'&$top=100&$orderby=receivedDateTime desc'
    )
    token = _reader_token()
    # Ask Graph for the PLAIN-TEXT body — FNB sends HTML, and interleaved tags
    # would break the plain-text markers _RESULT_RE looks for (a silent no-match).
    headers = {'Authorization': f'Bearer {token}',
               'Prefer': 'outlook.body-content-type="text"'}
    out: list[dict] = []
    while url and len(out) < 400:
        r = requests.get(url, headers=headers, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f'Graph read {r.status_code}: {r.text[:250]}')
        body = r.json()
        out.extend(body.get('value', []))
        url = body.get('@odata.nextLink')
    return out


def _sender(msg: dict) -> str:
    box = (msg.get('from') or msg.get('sender') or {}).get('emailAddress', {})
    return (box.get('address') or '').strip().lower()


def _body_text(msg: dict) -> str:
    b = msg.get('body') or {}
    # We only ever regex plain markers out of it, so HTML vs text does not matter;
    # bodyPreview is a safe fallback if the full body is absent.
    return b.get('content') or msg.get('bodyPreview') or ''


def _matchable_requests() -> list[dict]:
    """PENDING_CFO requests (closeable) PLUS recently-terminal ones (paid/cancelled,
    last 120 days), as the plain dicts the matcher expects.

    The terminal rows matter for safety: an FNB email is re-read across ~3 runs
    (36h lookback), and once the right request is closed a re-read must NOT fall
    through to a same-vendor/same-amount SIBLING that is still open. Seeing the
    already-terminal match lets the matcher say "already reconciled" instead."""
    from datetime import timedelta
    cutoff = timezone.now() - timedelta(days=120)
    rows = []
    qs = (PaymentRequest.objects
          .select_related('fnb_batch')
          .filter(Q(status=PaymentRequest.Status.PENDING_CFO)
                  | Q(status__in=[PaymentRequest.Status.PAID, PaymentRequest.Status.CANCELLED],
                      created_at__gte=cutoff)))
    for p in qs:
        rows.append({
            'id':                 p.id,
            'ref':                p.ref,
            'total':              p.total,
            'bank_our_reference': p.bank_our_reference,
            'bank_narration':     p.bank_narration,
            'payee':              p.payee,
            'subject':            p.subject,
            'line_items':         p.line_items,
            # The unique FNB batch id Omni loaded this under — the email echoes it
            # as its reference when the payee ref wasn't stored (getattr: fnb_batch
            # is a nullable FK).
            'batch_key':          getattr(p.fnb_batch, 'idempotency_key', '') or '',
            'is_open':            p.status == PaymentRequest.Status.PENDING_CFO,
        })
    return rows


class Command(BaseCommand):
    help = 'Close paid payment requests from FNB "Fully Processed" emails.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Show what would be closed; write nothing.')
        parser.add_argument('--hours', type=int, default=None,
                            help='Look back this many hours '
                                 '(default: FNB_EMAIL_AUTOCLOSE_LOOKBACK_HOURS, else 36).')

    def handle(self, *args, **opts):
        dry = opts['dry_run']
        # Refresh the list the /payment-requests screen reads, whether or not
        # auto-close itself is switched on — the screen must never read the
        # mailbox on a page load (19-Sep-2026: 8.5-11s loads).
        try:
            from taskboard.fnb_email_view_helpers import refresh_paid_fnb_emails_store
            n = refresh_paid_fnb_emails_store(
                getattr(settings, 'FNB_EMAIL_CATCHUP_LOOKBACK_HOURS', 720))
            self.stdout.write(f'payment-screen FNB list refreshed: {n} paid email(s)')
        except (ValueError, RuntimeError, OSError) as exc:
            self.stderr.write(f'payment-screen FNB list NOT refreshed: {exc}')
        if not dry and not getattr(settings, 'FNB_EMAIL_AUTOCLOSE_ENABLED', False):
            self.stdout.write('FNB_EMAIL_AUTOCLOSE_ENABLED is off — nothing done '
                              '(use --dry-run to preview).')
            return

        hours = opts['hours'] or getattr(settings, 'FNB_EMAIL_AUTOCLOSE_LOOKBACK_HOURS', 36)
        mailbox = getattr(settings, 'FNB_EMAIL_READ_MAILBOX', '') or \
            getattr(settings, 'AUTO_REPLY_READ_MAILBOX', '')
        if not mailbox:
            self.stderr.write('No mailbox configured (FNB_EMAIL_READ_MAILBOX / '
                              'AUTO_REPLY_READ_MAILBOX).')
            return

        messages = _fetch_fnb_emails(mailbox, hours)
        fnb = [m for m in messages if _sender(m) == _FNB_SENDER]
        self.stdout.write(f'read {len(messages)} msg(s) in {hours}h; {len(fnb)} from FNB')

        # Parse the PAID ones once; the open-request set is re-read after each close
        # so a second email can't re-match a request the first one just cleared.
        paid = []
        for m in fnb:
            parsed = parse_result_email(_body_text(m))
            if parsed and parsed['paid']:
                paid.append(parsed)
        self.stdout.write(f'{len(paid)} "Fully Processed" email(s) parsed')

        user = _cfo_user()
        closed = ambiguous = nomatch = 0
        for parsed in paid:
            decision = match_paid_email(parsed, _matchable_requests())
            action = decision['action']
            if action == 'close':
                if dry:
                    closed += 1
                    self.stdout.write(f'  → WOULD mark paid {decision["request_ref"]} '
                                      f'(BWP{parsed["amount"]:,.2f}, {parsed["ref"]})')
                    continue
                pr = PaymentRequest.objects.filter(pk=decision['request_id']).first()
                # automatic=True: this is the one caller with no human in it, so
                # it is the one that must refuse to close a request whose bank
                # batches were ALL rejected (CFO's FNB brief, control 3).
                res = (_mark_paid_from_bank(pr, user, decision['reason'],
                                            automatic=True)
                       if pr else None)
                if res is not None:
                    closed += 1
                    self.stdout.write(self.style.SUCCESS(
                        f'  ✓ marked paid {decision["request_ref"]} '
                        f'(BWP{parsed["amount"]:,.2f})'))
                else:
                    # Either it left PENDING_CFO in a parallel action between
                    # read and close, or every batch behind it was rejected and
                    # the guard held it open on purpose.
                    self.stdout.write(f'  · {decision["request_ref"]} not closed '
                                      f'— no longer pending, or its bank batches '
                                      f'were all rejected; left open for a person')
            elif action == 'ambiguous':
                ambiguous += 1
                self.stdout.write(self.style.WARNING(
                    f'  ! ambiguous BWP{parsed["amount"]:,.2f} ({parsed["ref"]}) '
                    f'→ {decision["candidates"]} — left for a human'))
            else:
                nomatch += 1

        verb = 'would-mark-paid' if dry else 'marked-paid'
        self.stdout.write(self.style.SUCCESS(
            f'{verb}={closed} ambiguous={ambiguous} no-match={nomatch}'))
