"""Off-peak: pre-write the AI claim summary + soft PAY/HOLD opinion onto OPEN
Graphite claims (CFO 2026-08-31, Claim-Description-Spec), so a claims payment
request shows the summary the moment the claim number is typed.

Local-first AI over PII-redacted facts (integrations/claim_summary_ai.py);
advisory only — the figures and hard flags are always computed by rule, never by
the AI, and the AI never decides a claim. Idempotent and safe to re-run: it skips
claims summarised within --max-age-days, and any AI outage simply leaves the
summary empty (the deterministic facts still show). Never touches money."""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Pre-generate AI claim summaries for OPEN Graphite claims (off-peak).'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=200,
                            help='Max claims to summarise this run (default 200).')
        parser.add_argument('--max-age-days', type=int, default=14,
                            help='Re-summarise a claim only if its summary is older '
                                 'than this many days (default 14).')
        parser.add_argument('--claim', type=str, default='',
                            help='Summarise ONE claim number now, ignoring freshness.')

    def handle(self, *args, **opts):
        from datetime import timedelta
        from django.db.models import F, Q
        from django.utils import timezone
        from integrations.models import GraphiteClaim
        from integrations.claim_summary_ai import summarise_claim

        one = (opts.get('claim') or '').strip()
        if one:
            qs = GraphiteClaim.objects.filter(claim_number__iexact=one)
        else:
            cutoff = timezone.now() - timedelta(days=opts['max_age_days'])
            qs = GraphiteClaim.objects.all()
            # OPEN = not paid/closed/settled/repudiated/declined/rejected.
            for kw in ('paid', 'settled', 'closed', 'repudiat', 'declin', 'reject'):
                qs = qs.exclude(status__icontains=kw)
            # Never-summarised first, then the stalest; only OPEN claims. Explicit
            # NULLs FIRST — on Postgres a bare 'ai_summary_at' ASC puts NULLs LAST,
            # which would starve never-summarised claims when backlog > --limit (H96).
            qs = qs.filter(Q(ai_summary_at__isnull=True) | Q(ai_summary_at__lt=cutoff))
            qs = qs.order_by(F('ai_summary_at').asc(nulls_first=True),
                             F('detail_synced_at').desc(nulls_last=True))[:opts['limit']]

        wrote = skipped = 0
        for c in qs:
            summary, suggestion, reason, engine = summarise_claim(c)
            if not summary:
                skipped += 1  # AI unavailable or facts unsafe — leave it for next run
                continue
            GraphiteClaim.objects.filter(pk=c.pk).update(
                ai_summary=summary, ai_suggestion=suggestion, ai_reason=reason,
                ai_engine=engine, ai_summary_at=timezone.now(),
            )
            wrote += 1

        self.stdout.write(self.style.SUCCESS(
            f'presummarise_open_claims: wrote {wrote}, skipped {skipped} '
            f'(AI unavailable or facts unsafe).'))
