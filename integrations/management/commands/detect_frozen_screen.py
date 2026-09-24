"""
integrations/management/commands/detect_frozen_screen.py

The REAL fake-activity catcher (Fable 5, 2026-09-01): flags the weight-on-a-key /
frozen-screen cheat from Time Doctor screenshot metadata — heavy typing on a
screen that never changes. Detection logic: integrations.td_screenshot_integrity
(unit-tested offline). Reads only counts/hashes; NO images downloaded, NO window
titles or image URLs stored or printed (AD-POL-AI-GOV-001).

  # shadow mode — one day, whole company, print only (default):
  python manage.py detect_frozen_screen --date 2026-08-29

  # evidence pack — one person, back-scan N days (a row per day):
  python manage.py detect_frozen_screen --user "<full name>" --date 2026-08-29 --days 90

Flags for the human "explain your day" step; never docks. Read-only.
"""
from __future__ import annotations

import datetime

from django.core.management.base import BaseCommand
from django.utils import timezone

from integrations.timedoctor import TimeDoctorClient, TimeDoctorError
from integrations.td_screenshot_integrity import analyze_day, flagged
from integrations.td_matching import canonical_identity

SAST = datetime.timezone(datetime.timedelta(hours=2))   # Botswana, no DST


def _sast_day_bounds(day):
    """A Botswana calendar day as UTC datetimes, so a person's evening work lands
    on the right day in an evidence row (not leaked to the next UTC day)."""
    start = datetime.datetime.combine(day, datetime.time(0, 0), SAST).astimezone(datetime.timezone.utc)
    return start, start + datetime.timedelta(days=1)


# Time Doctor's /api/1.0/files truncates the RESPONSE, not just the people list —
# and `limit=100000` does not lift it. Measured on prod against one staff day of
# 21-Sep-2026 (Time Doctor's own UI reported 187 screenshots for it):
#     one user per call  -> 187 of their 187 shots (complete)
#     10 users per call  ->  37 of their 187 shots (20%)
#     whole 115 roster   ->   5 of their 187 shots (3%)
# The cap is on total records returned and is SHARED across everyone named in the
# call, so the more people you ask for, the less of each you get — silently, with
# a 200 and no error. Batching to 10 (the earlier fix, when a single whole-company
# call returned only 42 of 113 people) stopped whole people vanishing but left
# every remaining person truncated to a fifth of their day. Every percentage and
# every credited-hours figure the sweep produced was computed on that fifth, which
# is why the daily company sweep found almost nothing while a focused
# single-person scan of the SAME day found plenty.
# ONE USER PER CALL is the only pull that comes back whole. Verified by splitting
# a solo day in half and checking the halves sum exactly — 349 = 136 + 213,
# 290 = 101 + 189, 187 = 90 + 97 — so a solo pull is not silently truncating at
# least up to 349 shots, and a 450-shot solo day was returned whole, above the
# ~398-record ceiling a multi-user call hits. If a single person ever exceeds
# that on ONE call, split their day into halves and sum; the half-day probe above
# is the test for it. ~115 calls for a daily sweep, sequential, the same order as
# the 60-day back-scan already runs. Do not "optimise" this back into batches.
_USER_BATCH = 1


def _pull_files(client, d_from, d_to, ids):
    files = []
    for i in range(0, len(ids), _USER_BATCH):
        files.extend(client.files(d_from, d_to, user_ids=ids[i:i + _USER_BATCH]) or [])
    return files


class Command(BaseCommand):
    help = 'Detect frozen-screen / weight-on-a-key faked activity from Time Doctor screenshots.'

    def add_arguments(self, parser):
        parser.add_argument('--date', dest='date', help='End day, YYYY-MM-DD (default: yesterday SAST).')
        parser.add_argument('--user', dest='user', help='Focus on one person (name substring).')
        parser.add_argument('--days', type=int, default=1, help='Back-scan this many days ending at --date.')
        parser.add_argument('--persist', action='store_true',
                            help='Store the result so the Omni Screen-Integrity screen can pull it instantly.')

    def handle(self, *args, **opts):
        client = TimeDoctorClient.from_settings()
        if not client.configured:
            self.stdout.write(self.style.WARNING('SKIPPED: Time Doctor not configured.'))
            return

        users = client.users()
        roster_ids = [u.get('id') for u in users if u.get('id')]

        focus = (opts.get('user') or '').strip().lower()
        days = max(int(opts.get('days') or 1), 1)
        if focus:
            matched = [u for u in users if focus in (u.get('name') or '').lower()]
            if not matched:
                self.stdout.write(self.style.ERROR(f'No Time Doctor user matches "{opts["user"]}".'))
                return
            # Fold to canonical people; a back-scan evidence pack must be ONE person
            # (else a 90-day pack could interleave two people — Fable 5).
            canon = {(canonical_identity(u.get('id'), u.get('name'))[0] or (u.get('name') or '').lower())
                     for u in matched}
            if days > 1 and len(canon) > 1:
                self.stdout.write(self.style.ERROR(
                    f'"{opts["user"]}" matches {len(canon)} people '
                    f'({", ".join(sorted(u.get("name") or "?" for u in matched))}). '
                    f'Be more specific for a back-scan.'))
                return
            self.stdout.write('Focusing on: ' + ', '.join(f'{u.get("name")} ({u.get("id")})' for u in matched))
            call_ids = [u.get('id') for u in matched]
        else:
            call_ids = roster_ids

        end_day = (datetime.datetime.strptime(opts['date'], '%Y-%m-%d').date()
                   if opts.get('date') else timezone.localtime().date() - datetime.timedelta(days=1))

        persist = bool(opts.get('persist'))
        if focus and days > 1:
            self._backscan(client, users, call_ids, end_day, days)
        else:
            for i in range(days):
                self._one_day(client, users, call_ids, end_day - datetime.timedelta(days=i),
                              focus=focus, persist=persist)

    def _one_day(self, client, users, call_ids, day, *, focus, persist=False):
        from integrations.models import ScreenIntegrityScan
        from integrations.screen_integrity_store import persist_day
        d_from, d_to = _sast_day_bounds(day)
        try:
            files = _pull_files(client, d_from, d_to, call_ids)
        except TimeDoctorError as exc:
            self.stderr.write(self.style.ERROR(f'{day}: files pull failed: {exc}'))
            if persist and not focus:
                persist_day(day, [], status=ScreenIntegrityScan.Status.FAILED,
                            note=str(exc)[:180])
            return
        sigs = analyze_day(files, users)
        flags = flagged(sigs)
        if persist and not focus:
            status = (ScreenIntegrityScan.Status.OK if sigs
                      else ScreenIntegrityScan.Status.NO_DATA)
            persist_day(day, sigs, status=status)
        self.stdout.write(f'{day}: {len(sigs)} people with screenshots, {len(flags)} to review.')
        rows = sigs if focus else flags
        for s in rows:
            self.stdout.write(
                f"  [{s.suspicion.upper():10}] {(s.name or s.user_id or '?')[:32]:32} "
                f"shots={s.shots:>3} frozen_typing={s.frozen_typing_pct*100:>4.0f}% "
                f"({s.frozen_typing_hours:.1f}h) idle_frozen={s.idle_frozen_pct*100:>4.0f}% "
                f"({s.idle_frozen_hours:.1f}h) mouse_dead={s.mouse_dead_pct*100:>4.0f}% "
                f"identical={s.identical_pct*100:>4.0f}%")
            for r in s.reasons:
                self.stdout.write(f"               - {r}")

    def _backscan(self, client, users, call_ids, end_day, days):
        self.stdout.write(f'Back-scan {days} days ending {end_day}:')
        self.stdout.write(f'{"date":12} {"shots":>5} {"frozen%":>8} {"hours":>6} '
                          f'{"idle%":>6} {"idlehrs":>8} '
                          f'{"mousedead%":>10} {"identical%":>10}  verdict')
        n_susp = n_watch = 0
        for i in range(days):
            day = end_day - datetime.timedelta(days=i)
            d_from, d_to = _sast_day_bounds(day)
            try:
                files = _pull_files(client, d_from, d_to, call_ids)
            except TimeDoctorError as exc:
                self.stdout.write(f'{day.isoformat():12}  (pull failed: {str(exc)[:40]})')
                continue
            sigs = analyze_day(files, users)
            if not sigs:
                continue
            s = sigs[0]      # single focused user
            if s.shots == 0:
                continue
            if s.suspicion == 'suspicious':
                n_susp += 1
            elif s.suspicion == 'watch':
                n_watch += 1
            self.stdout.write(
                f'{day.isoformat():12} {s.shots:>5} {s.frozen_typing_pct*100:>7.0f}% '
                f'{s.frozen_typing_hours:>6.1f} {s.idle_frozen_pct*100:>5.0f}% '
                f'{s.idle_frozen_hours:>8.1f} {s.mouse_dead_pct*100:>9.0f}% '
                f'{s.identical_pct*100:>9.0f}%  {s.suspicion}')
        self.stdout.write(f'\nSUMMARY: {n_susp} day(s) SUSPICIOUS, {n_watch} day(s) watch, '
                          f'over {days} days scanned.')
