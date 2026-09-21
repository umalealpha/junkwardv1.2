"""
purge_old_cvs — data-minimisation for candidate CVs (CFO 2026-08-26).

A CV is PII (name, address, ID, work history) sitting on local disk in
media/recruitment/cvs/. Once a candidate is stale we should not keep it: keep
CVs for 6 months, then delete the file + the locally-extracted cv_text. The
Candidate row itself is kept (name/email) for the hiring history — only the
heavy PII (the CV file and its extracted text) is removed.

Clock: Candidate.created_at. A candidate who was HIRED is exempt — their CV
becomes part of the employee file and is kept longer (the retention clock for a
hired person is their employment, not this 6-month advert window).

Dry-run by default (mirrors procurement.expire_stale_pos); --commit writes.
Run from cron daily after the nightly backup:
    0 5 * * * /opt/alpha-finance/.../manage.py purge_old_cvs --commit
"""
from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from recruitment.models import Candidate

DEFAULT_MONTHS = 6
DAYS_PER_MONTH = 30.44   # avg — 6 months ≈ 183 days


class Command(BaseCommand):
    help = ('Delete CV files + extracted cv_text for candidates older than the '
            'retention window (default 6 months). Hired candidates are kept. '
            'Dry-run unless --commit.')

    def add_arguments(self, parser):
        parser.add_argument('--commit', action='store_true',
                            help='Actually delete. Default = dry-run (count only).')
        parser.add_argument('--months', type=int, default=DEFAULT_MONTHS,
                            help=f'Retention window in months (default {DEFAULT_MONTHS}).')

    def handle(self, *args, **opts):
        months = max(1, int(opts['months']))
        cutoff = timezone.now() - timedelta(days=round(months * DAYS_PER_MONTH))

        # Old candidates who are NOT hired and still carry CV data to purge.
        stale = (Candidate.objects
                 .filter(created_at__lt=cutoff)
                 .exclude(applications__stage='hired')
                 .exclude(cv='', cv_text='')
                 .distinct())

        n = stale.count()
        if not opts['commit']:
            sample = ', '.join(c.full_name for c in stale[:5])
            self.stdout.write(
                f'[dry-run] {n} candidate CV(s) older than {months} month(s) '
                f'(before {cutoff.date()}) would be purged (hired kept).'
                + (f' e.g. {sample}' if sample else ''))
            return

        purged = 0
        for c in stale.iterator():
            if c.cv:
                c.cv.delete(save=False)   # removes the file from storage
            c.cv_text = ''
            c.save(update_fields=['cv', 'cv_text'])
            purged += 1
        self.stdout.write(self.style.SUCCESS(
            f'Purged {purged} candidate CV(s) older than {months} month(s) '
            f'(hired kept).'))
