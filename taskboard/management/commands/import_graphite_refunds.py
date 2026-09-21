"""Turn approved Graphite premium refunds into Omni payment requests, overnight.

CFO 2026-08-11, in his words:

  "When people load those refunds and approve it, it should automatically come to
   the payment request tab in Omni. Now after they upload the information into the
   graphite premium refund option, it is not coming into Omni, therefore I am not
   authorizing those payments. So the accountants don't need to type the
   information in graphite premium refund and in Omni as well. When they load those
   payments in graphite, overnight cron at twelve midnight, we'll combine them all,
   and the new ones will be created as a payment request in Omni automatically. It
   will be entered by Keetile, and I will be approving it."

WHY THIS READS GRAPHITE DIRECTLY, rather than waiting to be pushed
Graphite already has a hand-off — `OmniHandoffService` — behind a master switch,
`IntegrationSettings::isEnabled('omni_refunds')`, which defaults OFF and is why
nothing has been arriving. That hand-off feeds `customer_refunds`, which raises a
`payments.Payment` for the FNB money leg — NOT a payment request, which is the tab
the CFO actually authorises in. And he said to leave FNB out of this.

So this pulls instead: one scheduled read of the Graphite read-only replica
(`integrations.graphite_ro`), no Graphite deploy, no dependency on a switch someone
must remember to flip, and nothing touching the money leg. The existing hand-off is
left exactly as it is for when the FNB leg is wanted.

SAFETY
  * Read-only on Graphite. The bridge refuses any host that is not a replica and
    any statement that is not a SELECT.
  * Idempotent on `graphite_ref`, enforced by a UNIQUE constraint in the database
    and not merely by this query. A refund cannot become two payment requests —
    the gap that let eleven payment groups go out twice on 2026-08-09.
  * Only `approved` refunds are taken. A refund still in draft, submitted,
    under review or rejected is left alone, because Finance has not finished with
    it. Approval in Graphite IS the trigger the CFO described.
  * `--dry-run` is the default. Nothing is written until `--commit`.

    manage.py import_graphite_refunds                 # show what would be raised
    manage.py import_graphite_refunds --commit        # raise them
    manage.py import_graphite_refunds --commit --since 2026-08-01
"""
from __future__ import annotations

import datetime
from html import escape
from decimal import Decimal, InvalidOperation

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.notifications import send_html_with_cfo_cc

from taskboard.models import OmniTask, PaymentRequest
from taskboard.premium_refund_requests import (ENTERED_BY_EMAIL, ENTITY,
                                               PremiumRefundRequestError,
                                               raise_premium_refund_request)


#: The FNB leg is still typed in by hand, so the run leaves a task for the two
#: people who do it (CFO 2026-08-11: "someone has to manually enter these payments
#: into FNB, therefore it should put a task or reminder for Keetile and Tlamelo —
#: the night cron should do it"). A payment request nobody is told about is a
#: payment nobody makes.
FNB_TASK_EMAILS = [
    'kmokhendo@alphadirect.co.bw',    # Keetile Mokhendo
    'tchimidza@alphadirect.co.bw',    # Tlamelo Chimidza
]


def _shout(subject: str, body_html: str) -> None:
    """Tell a human the import is blocked.

    The first version of this guard only improved the ERROR TEXT. The text went
    to /var/log/alpha-finance/premium-refund-import.log — the same file the
    1064 syntax error went to every night for six weeks, read by nobody, which
    is exactly how Keetile's refunds stayed invisible. A cron that fails
    silently has not been fixed by failing more articulately.
    """
    try:
        send_html_with_cfo_cc(
            subject=f'Premium refund import BLOCKED — {subject}',
            html=(
                "<div style=\"font-family:'Book Antiqua',Georgia,serif;color:#0D1B2A;max-width:620px\">"
                "<div style='background:#0D1B2A;padding:14px 18px;border-radius:8px 8px 0 0'>"
                "<span style='color:#F4A623;font-weight:700;font-size:16px'>"
                "Approved refunds are not reaching Omni</span></div>"
                "<div style='border:1px solid #e5e7eb;border-top:0;padding:16px 18px;"
                f"border-radius:0 0 8px 8px'><p>{body_html}</p>"
                "<p>The overnight import stopped without raising anything, so nothing "
                "has been double-created. It will pick the waiting refunds up on the "
                "first run after this is put right.</p></div></div>"
            ),
            to=FNB_TASK_EMAILS,
        )
    except Exception:                                          # noqa: BLE001
        import logging
        logging.getLogger(__name__).exception(
            'premium refund import is blocked AND the alert could not be sent')


#: Graphite's own word for a refund Finance has finished with.
APPROVED = 'approved'


def _dec(v) -> Decimal:
    try:
        return Decimal(str(v or 0)).quantize(Decimal('0.01'))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal('0.00')


class Command(BaseCommand):
    help = ('Raise an Omni payment request for each newly APPROVED premium refund '
            'in Graphite. Read-only on Graphite; idempotent on graphite_ref.')

    def add_arguments(self, parser):
        parser.add_argument('--commit', action='store_true',
                            help='Actually create the payment requests.')
        parser.add_argument('--since', default=None,
                            help='Only refunds approved on/after this date (YYYY-MM-DD).')
        parser.add_argument('--limit', type=int, default=200)

    def handle(self, *args, **opts):
        from integrations import graphite_ro as gro

        if not gro.is_configured():
            raise CommandError(
                'No Graphite read replica configured — set GRAPHITE_RO_DB_HOST / '
                '_USER / _PASSWORD (the pair Graphite Aware already uses).')

        keetile = User.objects.filter(email__iexact=ENTERED_BY_EMAIL).first()
        if keetile is None:
            raise CommandError(
                f'{ENTERED_BY_EMAIL} has no Omni login, so nothing can be entered '
                'in their name. Create the account first.')

        seen = set(PaymentRequest.objects.filter(graphite_ref__gt='')
                   .values_list('graphite_ref', flat=True))
        # A manual dry run must not email Finance; only the real (cron) run shouts.
        self._alert = bool(opts.get('commit'))
        rows = self._fetch(gro, opts.get('since'), opts['limit'], already=seen)
        self.stdout.write(self.style.MIGRATE_HEADING(
            f'Approved premium refunds in Graphite: {len(rows)}'))

        already = set(
            PaymentRequest.objects.filter(graphite_ref__gt='')
            .values_list('graphite_ref', flat=True))

        # A blank reference defeats the dedupe entirely: it passes the "not seen
        # before" test every night, and the UNIQUE constraint is PARTIAL on
        # non-blank, so nothing stops it either. That is one fresh payment request
        # for the same refund every midnight — the P399,338.10 shape. Refuse it and
        # say so; a refund with no reference is a Graphite data fault, not ours.
        blank = [r for r in rows if not (r.get('graphite_ref') or '').strip()]
        for r in blank:
            self.stdout.write(self.style.ERROR(
                f'   SKIPPED a refund with no graphite_ref — amount '
                f'{_dec(r.get("refund_amount"))}. Fix it in Graphite; it cannot be '
                f'de-duplicated without a reference.'))

        # A parse failure must never become a real 0.00 payment request: it would
        # also burn this refund's one-per-reference slot, so the corrected figure
        # could never import. Skip, and it comes in clean the night after Graphite
        # is fixed. The create endpoint refuses total <= 0; so does this.
        zero = [r for r in rows
                if (r.get('graphite_ref') or '').strip() and _dec(r.get('refund_amount')) <= 0]
        for r in zero:
            self.stdout.write(self.style.ERROR(
                f'   SKIPPED {r.get("graphite_ref")} — amount reads as zero or '
                f'unparseable. Not raising a 0.00 payment request.'))

        bad = {id(r) for r in blank} | {id(r) for r in zero}
        fresh = [r for r in rows
                 if id(r) not in bad
                 and (r.get('graphite_ref') or '') not in already]
        self.stdout.write(f'   already raised in Omni : {len(rows) - len(fresh)}')
        self.stdout.write(f'   new to raise           : {len(fresh)}')

        if not fresh:
            self.stdout.write(self.style.SUCCESS('Nothing to do.'))
            return

        total = sum(_dec(r.get('refund_amount')) for r in fresh)
        self.stdout.write('')
        for r in fresh:
            # Reference and amount only. This goes to
            # /var/log/alpha-finance/premium-refund-import.log via the cron, and a
            # policy number in a log file is exactly what AD-POL-AI-GOV-001 forbids.
            self.stdout.write(
                f"   {r.get('graphite_ref'):14s} "
                f"{_dec(r.get('refund_amount')):>10} {str(r.get('currency') or 'BWP')}")
        self.stdout.write('')
        self.stdout.write(f'   TOTAL {total} BWP across {len(fresh)} refund(s)')

        if not opts['commit']:
            self.stdout.write(self.style.WARNING(
                '\nDRY RUN — nothing written. Re-run with --commit.'))
            return

        made, skipped = 0, 0
        for r in fresh:
            try:
                with transaction.atomic():
                    pr = self._raise(r, keetile)
                self.stdout.write(self.style.SUCCESS(
                    f"   raised {pr.ref} for {r.get('graphite_ref')}"))
                made += 1
            except IntegrityError:
                # The unique constraint caught a refund another run already took.
                # Expected under a concurrent or retried run; never an error.
                self.stdout.write(
                    f"   skipped {r.get('graphite_ref')} — already raised")
                skipped += 1
        raised_refs = [r for r in fresh
                       if PaymentRequest.objects.filter(
                           graphite_ref=(r.get('graphite_ref') or '')).exists()]
        # Never tell anyone to key a refund into FNB that the direct route has
        # ALREADY loaded (Fable, 2026-09-11). The two pipes are joined on
        # graphite_ref, but the direct route can raise the request and load the
        # bank while its own request step fails — and this task would then send
        # Keetile to type a payment that is already sitting in FNB. That is the
        # double payment this whole change exists to prevent.
        raised_refs = [r for r in raised_refs
                       if not self._already_loaded_to_fnb(r.get('graphite_ref') or '')]
        tasks = self._raise_fnb_tasks(raised_refs) if made else 0

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'{made} payment request(s) raised, {skipped} already present. '
            f'They are awaiting the CFO on the Premium refunds tab.'))
        if tasks:
            self.stdout.write(self.style.SUCCESS(
                f'{tasks} FNB task(s) raised so the batch actually gets paid.'))

    def _already_loaded_to_fnb(self, graphite_ref: str) -> bool:
        """Has the direct Graphite hand-off already put this refund in FNB?

        Read off the FNB batch link, not a status flag: the batch is set only
        by an actual submission, so it cannot say yes for a refund that merely
        reached APPROVED.
        """
        if not graphite_ref:
            return False
        from customer_refunds.models import CustomerRefund
        hit = CustomerRefund.objects.filter(
            graphite_ref=graphite_ref, fnb_batch__isnull=False).exists()
        if hit:
            self.stdout.write(self.style.WARNING(
                f'   {graphite_ref} is already loaded in FNB by the direct route '
                f'— no hand-keying task raised'))
        return hit

    def _raise_fnb_tasks(self, refunds: list[dict]) -> int:
        """Tell Keetile and Tlamelo there is a batch to load into FNB.

        One task each rather than one shared task: OmniTask has a single assignee,
        and a task addressed to one of them is a task the other cannot see. The
        body lists every refund so neither has to go hunting for the batch.
        """
        from taskboard.models import OmniTask

        if not refunds:
            return 0
        total = sum(_dec(r.get('refund_amount')) for r in refunds)
        lines = '\n'.join(
            f"  {r.get('graphite_ref')}  policy {r.get('policy_number')}  "
            f"{r.get('currency') or 'BWP'} {_dec(r.get('refund_amount'))}"
            for r in refunds)
        body = (
            f'{len(refunds)} premium refund(s) totalling BWP {total} came across from '
            f'the Graphite refund platform overnight and are now payment requests in '
            f'Omni, awaiting the CFO.\n\n'
            f'{lines}\n\n'
            'Once the CFO authorises them, these still have to be loaded into FNB by '
            'hand — Omni does not move the money for premium refunds. Nothing else to '
            're-type: the payment requests already carry the amounts and the policy '
            'numbers straight from Graphite.')

        made = 0
        for email in FNB_TASK_EMAILS:
            who = User.objects.filter(email__iexact=email).first()
            if who is None:
                self.stdout.write(self.style.WARNING(
                    f'   no Omni login for {email} — no FNB task raised for them'))
                continue
            OmniTask.objects.create(
                assigner=who, assignee=who,
                title=f'Load {len(refunds)} premium refund(s) into FNB — BWP {total}',
                body=body[:5000],
                priority=OmniTask.Priority.HIGH,
                status=OmniTask.Status.PENDING,
                due_at=timezone.now() + datetime.timedelta(days=1),
                source='premium_refund_import',
            )
            made += 1
        return made

    # ------------------------------------------------------------------ helpers
    def _fetch(self, gro, since, limit, already=()) -> list[dict]:
        """Approved refunds from Graphite. Explicit columns, parameterised."""
        # Only what is actually used. bank_name / account_last4 were fetched and
        # never read — customer bank details have no business leaving Graphite.
        cols = ['graphite_ref', 'policy_number', 'customer_name', 'refund_amount',
                'currency', 'reason', 'reason_code', 'area', 'status', 'approved_at']
        available = set(gro.columns_of('refund_requests'))
        # An empty column list is NOT "the table has no columns we want" — it is
        # information_schema telling us this connection cannot SEE the table at
        # all, because information_schema is filtered by privilege. That is the
        # live state: `SELECT command denied to user 'brain_ro' for table
        # Graphite_live.refund_requests` (1142), proven 2026-09-18.
        #
        # Left unchecked it built `SELECT  FROM refund_requests` — an empty
        # select list — and MySQL answered with a SYNTAX error (1064). So every
        # night since August the log has blamed our SQL for a missing GRANT, and
        # Keetile's refunds silently never arrived. A failed lookup must not be
        # allowed to produce a confident next step.
        if not available:
            if getattr(self, '_alert', True):
                _shout(
                    'Graphite will not show Omni the refund table',
                    'Omni cannot read <code>Graphite_live.refund_requests</code> at all: '
                    'information_schema returned no columns for it, which is what MySQL '
                    'does when the read-only user has no SELECT grant (error 1142). '
                    'This is a GRANT on the Graphite side, not an Omni fault. '
                    '<b>No approved refund can reach Omni until it is applied.</b>')
            raise CommandError(
                'Graphite will not show Omni the `refund_requests` table: '
                "information_schema returned no columns for it, which means the "
                "read-only user has no SELECT grant on it (MySQL denies it as "
                "1142). This is a GRANT on the Graphite side, not an Omni bug — "
                "no refund can be imported until it is applied.")
        # The columns the SQL below actually NAMES, not merely "at least one of
        # the ten we would like". `status` is in the WHERE and `graphite_ref` in
        # the NOT IN and the ORDER BY fallback, so a table carrying neither
        # produces MySQL 1054 Unknown column — a failed lookup once again
        # yielding a confident next step, which is the thing this guard exists
        # to stop.
        required = {'graphite_ref', 'status'}
        missing = sorted(required - available)
        use = [c for c in cols if c in available]
        if missing:
            if getattr(self, '_alert', True):
                _shout(
                    'Graphite refund table no longer has the columns we read',
                    f'Missing: <b>{escape(", ".join(missing))}</b>.<br>It has: '
                    f'{escape(", ".join(sorted(available)))}.<br>The table was renamed or '
                    'reshaped. Do not guess at the new names — confirm with Graphite.')
            raise CommandError(
                'Graphite\'s `refund_requests` table is missing the columns this '
                f'import reads: {missing}. It has: {sorted(available)}. '
                'The table was renamed or reshaped — do not guess, confirm with Graphite.')
        sel = ', '.join(gro.safe_identifier(c) for c in use)
        sql = (f'SELECT {sel} FROM {gro.safe_identifier("refund_requests")} '
               f'WHERE {gro.safe_identifier("status")} = %s')
        params = [APPROVED]
        if since and 'approved_at' in available:
            sql += f' AND {gro.safe_identifier("approved_at")} >= %s'
            params.append(since)
        if 'deleted_at' in available:
            sql += f' AND {gro.safe_identifier("deleted_at")} IS NULL'
        # Exclude what Omni already has BEFORE the limit. With the limit applied
        # first and an ascending sort, the fetch would return the same oldest 200
        # for ever once history passed 200 — every new refund beyond the window,
        # the log cheerfully reporting "Nothing to do".
        if already:
            marks = ', '.join(['%s'] * len(already))
            sql += f' AND {gro.safe_identifier("graphite_ref")} NOT IN ({marks})'
            params.extend(sorted(already))
        sql += f' ORDER BY {gro.safe_identifier("approved_at")} DESC' \
            if 'approved_at' in available else \
            f' ORDER BY {gro.safe_identifier("graphite_ref")} DESC'
        return gro.query(sql, params, limit=limit)

    def _raise(self, r: dict, keetile: User) -> PaymentRequest:
        """Delegate to the shared builder the live hand-off also uses.

        The request shape moved to taskboard/premium_refund_requests.py on
        2026-09-11 so the overnight pipe and the direct Graphite hand-off cannot
        render the same refund two different ways.
        """
        try:
            return raise_premium_refund_request(r, keetile, origin='overnight')
        except PremiumRefundRequestError as exc:
            raise CommandError(str(exc)) from exc
