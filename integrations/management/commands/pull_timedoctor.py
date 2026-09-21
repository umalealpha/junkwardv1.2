"""
integrations/management/commands/pull_timedoctor.py

Daily pull of Time Doctor workforce data into omni + a daily HTML email.

  python manage.py pull_timedoctor                  # yesterday (SAST), email on
  python manage.py pull_timedoctor --date 2026-06-15
  python manage.py pull_timedoctor --days 1 --no-email
  python manage.py pull_timedoctor --dry-run        # pull + aggregate, no write/email

Requires (else exits SKIPPED, non-fatal for crons):
  TIMEDOCTOR_TOKEN, TIMEDOCTOR_COMPANY_ID

Privacy: stores + emails per-user hours + productivity % + attendance only.
Raw window/app titles are aggregated away in integrations.timedoctor.aggregate
and never persisted or sent (AD-POL-AI-GOV-001).
"""
from __future__ import annotations

import datetime

from django.core.management.base import BaseCommand
from django.utils import timezone

from integrations.timedoctor import TimeDoctorClient, TimeDoctorError, aggregate, build_email_html
from integrations.models import TimeDoctorDailySnapshot


class Command(BaseCommand):
    help = 'Pull Time Doctor workforce data for a day, store an omni snapshot, and email the summary.'

    def add_arguments(self, parser):
        parser.add_argument('--date', dest='date', help='Day to pull, YYYY-MM-DD (default: yesterday).')
        parser.add_argument('--days', type=int, help='Pull the last N days as one window (default: 1).')
        parser.add_argument('--no-email', action='store_true', help='Store the snapshot but do not email.')
        parser.add_argument('--dry-run', action='store_true', help='Pull + aggregate, print, no write/email.')
        parser.add_argument('--slot', dest='slot', default=None,
                            help='Settle-gate pull label, e.g. 0300/0400/0830 (default: local HHMM). '
                                 'Records a per-user tracked-seconds sample without wiping earlier slots.')
        parser.add_argument('--intraday', action='store_true',
                            help="Pull TODAY so far (00:00 SAST -> now) for the midday/afternoon hours "
                                 "reminders. Implies today's date and no daily email; the snapshot is "
                                 "upserted for today so a later full pull still overwrites it cleanly.")

    def handle(self, *args, **opts):
        client = TimeDoctorClient.from_settings()
        if not client.configured:
            self.stdout.write(self.style.WARNING(
                'SKIPPED: TIMEDOCTOR_TOKEN / TIMEDOCTOR_COMPANY_ID not set. '
                'Mint a token via POST /api/1.0/login and set it in the env.'))
            return

        intraday = bool(opts.get('intraday'))
        if intraday:
            # Today, up to right now — partial hours for the same-day reminders.
            # _fmt_bound turns a datetime into a full UTC ISO instant, so `to=now`
            # gives Time Doctor "today so far" rather than the whole day.
            day = timezone.localtime().date()
            day_from = day                       # date -> midnight (start of today)
            day_to   = timezone.now()            # datetime -> this instant
        elif opts.get('date'):
            day = datetime.datetime.strptime(opts['date'], '%Y-%m-%d').date()
            span = max(int(opts.get('days') or 1), 1)
            day_from = day - datetime.timedelta(days=span - 1)
            day_to   = day + datetime.timedelta(days=1)   # API `to` is exclusive of the next day's start
        else:
            day = (timezone.localtime().date() - datetime.timedelta(days=1))
            span = max(int(opts.get('days') or 1), 1)
            day_from = day - datetime.timedelta(days=span - 1)
            day_to   = day + datetime.timedelta(days=1)   # API `to` is exclusive of the next day's start

        try:
            users    = client.users()
            ids      = [u.get('id') for u in users if u.get('id')]
            worklog  = client.worklog(day_from, day_to, user_ids=ids)
            timeuse  = client.timeuse(day_from, day_to, user_ids=ids)
            projects = client.projects()
            tasks    = client.tasks()
        except TimeDoctorError as exc:
            self.stderr.write(self.style.ERROR(f'Time Doctor pull failed: {exc}'))
            raise SystemExit(1)

        agg = aggregate(users, worklog, timeuse, projects, tasks, as_of=day, td_user_ids=ids)
        totals, members = agg['totals'], agg['members']
        self.stdout.write(f"Aggregated {day}: {totals['active_users']} active / {totals['user_count']} users, "
                          f"{totals['total_hours']}h, productive={totals['productive_pct']}%")

        if opts.get('dry_run'):
            self.stdout.write(self.style.WARNING('DRY-RUN: not stored, not emailed.'))
            return

        snap, _ = TimeDoctorDailySnapshot.objects.get_or_create(
            company_id=client.company_id, as_of=day,
        )
        snap.totals = totals
        snap.payload = members
        if intraday:
            # An intraday pull is a PARTIAL day and must NEVER enter settle_samples.
            # people_data_guard sorts slot labels lexicographically as if that were
            # chronological, so '1140'/'1555' would masquerade as the day's OLDEST
            # slots and corrupt the settle / last-seconds reads that the 09:00
            # exceptions report, the morning brief and the Saturday explain all
            # depend on. The reminders read `payload` only, so store just that.
            snap.save()
            self.stdout.write(f"Intraday snapshot for {day} updated (payload only, no settle sample).")
        else:
            # Record THIS pull's per-user tracked seconds under its slot label, merging
            # into any earlier slots for the same day (get_or_create keeps prior pulls).
            # Only the LAST pull of a day emails; earlier slots are settle samples only.
            slot = (opts.get('slot') or timezone.localtime().strftime('%H%M'))
            samples = dict(snap.settle_samples or {})
            samples[slot] = {m.get('user_id'): int(round((m.get('hours_tracked') or 0) * 3600))
                             for m in members if m.get('user_id')}
            snap.settle_samples = samples
            snap.save()
            self.stdout.write(f"Recorded settle sample slot '{slot}' "
                              f"({len(samples[slot])} users); slots so far: {sorted(samples)}")

        from django.conf import settings as _dj_settings
        consolidated = getattr(_dj_settings, 'CONSOLIDATED_EMAILS_ENABLED', False)
        if intraday:
            # An intraday pull is data-only; the reminder commands do the emailing.
            self.stdout.write(self.style.WARNING('Intraday pull: snapshot updated, no email.'))
        elif not opts.get('no_email') and consolidated:
            # Consolidated emails ON (CFO 2026-08-05): the daily workforce figures
            # now ride inside the 07:05 Morning Brief / exceptions send, so this
            # standalone report is one email too many. Snapshot + settle samples
            # are still saved above; only the email is suppressed.
            self.stdout.write(self.style.WARNING(
                'Email SKIPPED: consolidated emails ON (folded into the 07:05 brief).'))
        elif not opts.get('no_email'):
            try:
                from core.notifications import send_html_with_cfo_cc
                recips = list(getattr(__import__('django.conf', fromlist=['settings']).settings,
                                      'TIMEDOCTOR_EMAIL_TO', []) or [])
                n = send_html_with_cfo_cc(
                    subject=f'Daily Workforce Report — {day}',
                    html=build_email_html(totals, members),
                    to=recips or ['pganesharajah@alphadirect.co.bw'],
                )
                snap.emailed = True
                snap.save(update_fields=['emailed', 'updated_at'])
                self.stdout.write(self.style.SUCCESS(f'Emailed daily report ({n} message(s)).'))
            except Exception as exc:    # noqa: BLE001
                self.stderr.write(self.style.WARNING(f'Snapshot stored but email failed: {exc}'))

        self.stdout.write(self.style.SUCCESS(f'Stored Time Doctor snapshot for {day}.'))
