"""rewards/nexus_standings.py — Alpha Nexus tester competition.

Single source of truth for the tester ranking (used by the staff leaderboard
and the standings emails), plus the rich HTML "where you stand" email that
pushes testers to use every feature to win the cash prizes.

Competition metric — **Nexus Score** (transparent, rewards ALL features):
    Nexus Score = reward points + 15 x Click&Drive trips + 15 x wellness scans
This does NOT change stored points (the wellness-scan-never-awards invariant
stays); the trip/scan bonuses are computed at ranking time only.

The competition RUNS FROM `COMPETITION_START` (CFO 2026-09-08: the real
competition began the day before, everything before it was us testing). Only
activity dated on/after that day counts, so the board starts everyone at zero
without deleting a single row — points, trips and scans earned while testing
still exist, they simply do not score. Change the date, the board re-bases;
nothing is ever lost.
"""
from __future__ import annotations

from datetime import date

from core.date_format import format_dt

PRIZES = [('1st', 'BWP 5,000', '🥇'), ('2nd', 'BWP 3,000', '🥈'), ('3rd', 'BWP 1,000', '🥉')]
# Verified, sensor/camera-backed actions carry the most weight (hard to fake);
# self-reported actions earn little (see customer_views.customer_activity).
TRIP_BONUS = 25   # Click & Drive — GPS-verified
SCAN_BONUS = 20   # Wellness pulse scan — camera-verified

# Day the real competition started. Nothing dated before it scores.
COMPETITION_START = date(2026, 9, 7)

# App-store / Play review + internal test logins. They must keep working in the
# app (Apple's reviewer signs in with one) but they never appear on the board —
# the demo account was sitting #1 on 4,200 test points.
HIDDEN_EMAILS = {
    'appreview@alphadirect.co.bw',
    'hide my emailappreview@alphadirect.co.bw',
    'nexus.tester@gmail.com',
}
# Any member whose name starts with one of these is a demo/seed record.
HIDDEN_NAME_PREFIXES = ('demo',)


def _is_hidden(member) -> bool:
    """True for demo / app-review / seed accounts — hidden from the board only."""
    import os
    email = ((getattr(member, 'email', '') or '').strip().lower())
    if email and email in HIDDEN_EMAILS:
        return True
    # The live App-Store review login is configured by env var, not hardcoded.
    rev = (os.environ.get('NEXUS_REVIEW_EMAIL', '') or '').strip().lower()
    if rev and email == rev:
        return True
    name = (getattr(member, 'customer_name', '') or '').strip().lower()
    return name.startswith(HIDDEN_NAME_PREFIXES)


def competition_points(member) -> int:
    """Reward points EARNED INSIDE the competition window (never negative).

    Deliberately NOT `member.points_balance` — that is the member's lifetime
    wallet and still holds everything earned while we were testing. The board
    only counts what was earned since COMPETITION_START.
    """
    from django.db.models import Sum
    from .models import PointsTransaction
    total = (PointsTransaction.objects
             .filter(member=member, occurred_at__date__gte=COMPETITION_START)
             .aggregate(t=Sum('points'))['t'] or 0)
    return max(0, int(total))


def ranked_testers() -> list[dict]:
    """Alpha Nexus testers ranked by Nexus Score (highest first).

    Counts only activity on/after COMPETITION_START; demo / app-review logins
    are left off the board entirely.
    """
    from .models import RewardMember, CustomerDriveTrip
    from .alpha_score import alpha_score
    from .thrive_core import metrics_for_score
    from . import drive_score

    rows = []
    for m in RewardMember.objects.filter(is_active=True):
        if _is_hidden(m):
            continue
        # is_valid only — a "start, don't move, stop" recording must never buy
        # TRIP_BONUS on the leaderboard or dilute anyone's harsh-event rate.
        trips = list(CustomerDriveTrip.objects.filter(
            member=m, started_at__date__gte=COMPETITION_START, is_valid=True))
        scans_qs = m.health_metrics.filter(
            resting_hr__isnull=False, date__gte=COMPETITION_START)
        scan = scans_qs.order_by('-date').first()
        scan_count = scans_qs.count()
        if not trips and scan_count == 0 and not m.email:
            continue
        prof = drive_score.driving_profile([{
            'score': t.score, 'distance_km': t.distance_km, 'duration_min': t.duration_min,
            'idle_minutes': t.idle_minutes, 'harsh_events': t.harsh_events, 'max_speed': t.max_speed,
        } for t in trips])
        wellness = alpha_score(metrics_for_score(m, scan))['score'] if scan else None
        points = competition_points(m)
        nexus = points + len(trips) * TRIP_BONUS + scan_count * SCAN_BONUS
        rows.append({
            'memberId': str(m.id), 'name': m.customer_name, 'email': m.email,
            'tier': m.get_tier_display(), 'points': points,
            'trips': prof['trips'], 'totalKm': prof['totalKm'],
            'avgDriveScore': prof['avgScore'], 'harshPer100km': prof['harshPer100km'],
            'driveBand': prof['band'] if prof['trips'] else '—',
            'wellnessScans': scan_count, 'wellnessScore': wellness,
            'nexusScore': nexus,
        })
    rows.sort(key=lambda r: (-r['nexusScore'], -(r['avgDriveScore'] or 0), -r['points']))
    return rows


def start_label() -> str:
    """The competition start date in the plain form the CFO reads."""
    return format_dt(COMPETITION_START, '%-d %B %Y')


def _first(name: str) -> str:
    return (name or 'there').strip().split(' ')[0] or 'there'


def standings_email(row: dict, rank: int, total: int, ranked: list[dict]) -> tuple[str, str]:
    """Build (subject, html) of the personalised standings email for `row`."""
    me = row
    leader = ranked[0] if ranked else me
    above = ranked[rank - 2] if rank >= 2 else None  # person directly ahead

    if rank == 1:
        chaser = ranked[1] if total >= 2 else None
        headline = "You're in the lead 🥇"
        gap_line = (f"But <b>{_first(chaser['name'])}</b> is right behind on "
                    f"<b>{chaser['nexusScore']:,}</b> — only {me['nexusScore'] - chaser['nexusScore']:,} "
                    f"behind you. Do not slow down.") if chaser else "Keep building your lead."
    else:
        gap = (above['nexusScore'] - me['nexusScore']) if above else 0
        headline = f"You're #{rank} of {total}"
        gap_line = (f"<b>{_first(above['name'])}</b> is ahead of you on "
                    f"<b>{above['nexusScore']:,}</b> Nexus points — you're on "
                    f"<b>{me['nexusScore']:,}</b>, just <b>{gap:,}</b> behind. "
                    f"The leader, <b>{_first(leader['name'])}</b>, is on {leader['nexusScore']:,}. Close the gap.")

    top_rows = ''.join(
        f"<tr style='background:{'#FFFBEB' if i == 0 else '#fff'}'>"
        f"<td style='padding:8px 10px;font-weight:bold;color:{'#F4A623' if i == 0 else '#9CA3AF'}'>{i + 1}</td>"
        f"<td style='padding:8px 10px;'>{_first(r['name'])}{' 🏆' if i == 0 else ''}</td>"
        f"<td style='padding:8px 10px;text-align:right;font-weight:bold;'>{r['nexusScore']:,}</td></tr>"
        for i, r in enumerate(ranked[:3]))

    prize_cells = ''.join(
        f"<td style='padding:10px;text-align:center;'>"
        f"<div style='font-size:22px;'>{emo}</div>"
        f"<div style='font-weight:bold;color:#0D1B2A;'>{amt}</div>"
        f"<div style='font-size:11px;color:#6B7280;'>{pos} place</div></td>"
        for pos, amt, emo in PRIZES)

    html = f"""\
<div style="font-family:'Book Antiqua',Georgia,serif;max-width:600px;margin:0 auto;background:#F3F4F6;padding:24px;">
  <div style="background:#0D1B2A;border-radius:14px 14px 0 0;padding:24px 28px;">
    <div style="color:#fff;font-size:22px;font-weight:bold;">Alpha&nbsp;Nexus &mdash; the race is on</div>
    <div style="color:#F4A623;font-size:14px;margin-top:4px;">{headline}</div>
  </div>
  <div style="background:#fff;border:1px solid #E5E7EB;border-top:none;border-radius:0 0 14px 14px;padding:28px;">
    <p style="margin:0 0 12px;">Hi {_first(me['name'])},</p>
    <p style="margin:0 0 16px;font-size:15px;line-height:1.55;">{gap_line}</p>

    <p style="margin:0 0 6px;font-weight:bold;color:#0D1B2A;">Top of the board</p>
    <table style="width:100%;border-collapse:collapse;font-size:14px;border:1px solid #E5E7EB;border-radius:8px;overflow:hidden;margin-bottom:18px;">
      {top_rows}
    </table>

    <div style="background:#FFF7ED;border:1px solid #F4A623;border-radius:10px;padding:6px;margin:0 0 18px;">
      <table style="width:100%;border-collapse:collapse;"><tr>{prize_cells}</tr></table>
    </div>

    <p style="margin:0 0 6px;font-weight:bold;color:#0D1B2A;">How to climb &mdash; use every feature</p>
    <ul style="margin:0 0 18px;padding-left:20px;line-height:1.7;font-size:14px;">
      <li><b>Click &amp; Drive</b> &mdash; record trips. Smooth, safe driving scores high and adds <b>+{TRIP_BONUS}</b> Nexus points per trip.</li>
      <li><b>Wellness scan</b> &mdash; do the 30-second pulse scan daily: <b>+{SCAN_BONUS}</b> per scan.</li>
      <li><b>Rewards</b> &mdash; your reward points all count toward your Nexus Score.</li>
    </ul>
    <p style="margin:0 0 18px;font-size:13px;color:#6B7280;">Nexus Score = reward points + {TRIP_BONUS}&times;trips + {SCAN_BONUS}&times;wellness scans, counted from <b>{start_label()}</b> &mdash; the day the competition started. Anything earned while we were testing does not score, and your reward points are untouched. You: <b>{me['nexusScore']:,}</b> ({me['trips']} trips, {me['wellnessScans']} scans, {me['points']:,} pts).</p>

    <p style="text-align:center;margin:0 0 8px;">
      <a href="https://omni.alphadirect.co.bw/m" style="display:inline-block;background:#F4A623;color:#0D1B2A;font-weight:bold;text-decoration:none;padding:14px 30px;border-radius:999px;font-size:16px;">Open Alpha&nbsp;Nexus &amp; climb</a>
    </p>
    <p style="margin:18px 0 0;font-size:13px;">Drive safe, scan daily, win the cash. Good luck.</p>
    <p style="margin:8px 0 0;">Regards,<br>Alpha&nbsp;Nexus</p>
  </div>
  <div style="text-align:center;color:#9CA3AF;font-size:11px;padding:14px;">Alpha Direct Insurance Company (Pty) Ltd &middot; Botswana</div>
</div>"""
    subject = (f"You're leading Alpha Nexus 🥇" if rank == 1
               else f"You're #{rank} of {total} on Alpha Nexus — close the gap")
    return subject, html


MAX_PER_DAY = 2  # standings emails per registered tester per day (anti-spam)


def send_standings_to_all() -> dict:
    """Email every registered tester their standings, capped at MAX_PER_DAY each."""
    from django.utils import timezone
    from core.notifications import send_html_with_cfo_cc
    from .models import RewardMember, NexusEmailQuota

    ranked = ranked_testers()
    total = len(ranked)
    today = timezone.localdate()
    sent = 0
    skipped = 0
    for i, row in enumerate(ranked):
        if not row['email']:
            continue
        quota, _ = NexusEmailQuota.objects.get_or_create(
            member_id=row['memberId'], day=today, defaults={'count': 0})
        if quota.count >= MAX_PER_DAY:
            skipped += 1
            continue
        subject, html = standings_email(row, i + 1, total, ranked)
        send_html_with_cfo_cc(
            subject, html, to=[row['email']],
            text_fallback=f"You're #{i + 1} of {total} on Alpha Nexus (Nexus Score {row['nexusScore']}). "
                          f"Open https://omni.alphadirect.co.bw/m and use Click & Drive + the wellness "
                          f"scan to climb. Prizes: BWP 5,000 / 3,000 / 1,000.",
            cc_cfo=False)
        quota.count += 1
        quota.save(update_fields=['count', 'updated_at'])
        sent += 1
    return {'sent': sent, 'skipped': skipped, 'total': total, 'capPerDay': MAX_PER_DAY}
