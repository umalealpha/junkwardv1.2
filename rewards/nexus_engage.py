"""rewards/nexus_engage.py — the three engagement features the CFO approved on
2026-09-08: family/friend teams ("6 is good"), the pulse-check screening
voucher ("10 is good") and referral codes ("7 is good").

This is the DATABASE layer. All the decision logic lives in pure, separately
tested modules and is imported here, never re-implemented:
  - nexus_teams.team_summary / is_team_member
  - screening_unlock.screening_state
  - referral.make_code / normalise_code / check_referral

Two standing rules are enforced here, not in the views:
  🔴 A referral is NEVER paid at signup. provisional_rates records the rule —
     pay on a policy that ACTIVATES and STICKS — so a Referral row is written
     with qualified_at NULL and points_awarded 0, and a separate job pays it.
  🔴 A team is only ever readable by its own members. There is deliberately no
     read-a-team-by-id path; every query starts from the signed-in member.
"""
from __future__ import annotations

import secrets

from django.db import IntegrityError, transaction
from django.db.models import Count, Sum
from django.utils import timezone

from . import nexus_teams, referral, screening_unlock
from .models import (
    CustomerDriveTrip, NexusTeam, NexusTeamMember, Referral, RewardMember,
    ScreeningVoucher,
)
from .nexus_growth import _public_name
from .nexus_standings import COMPETITION_START, competition_points

JOIN_CODE_LENGTH = 6
VOUCHER_CODE_LENGTH = 8
SCREENING_VALID_DAYS = 90          # how long an issued voucher stays usable
_ALPHABET = referral._UNAMBIGUOUS_ALPHABET


def _random_code(length: int) -> str:
    """A short code a human reads off a screen and types on a phone."""
    return ''.join(secrets.choice(_ALPHABET) for _ in range(length))


# ---------------------------------------------------------------------------
# 6. Family and friend teams
# ---------------------------------------------------------------------------

def _trip_totals(members) -> dict:
    """Distance and trip count per member in ONE query, not one per teammate.

    A team is capped at six, so the N+1 was bounded - but it is still six round
    trips for a number a single GROUP BY gives us.
    """
    rows = (CustomerDriveTrip.objects
            .filter(member__in=members, is_valid=True,
                    started_at__date__gte=COMPETITION_START)
            .values('member_id')
            .annotate(km=Sum('distance_km'), trips=Count('id')))
    return {str(r['member_id']): (float(r['km'] or 0), int(r['trips'] or 0)) for r in rows}


def _member_row(member, totals: dict) -> dict:
    """One teammate's contribution, counted only inside the competition window."""
    km, trips = totals.get(str(member.id), (0.0, 0))
    return {
        'id': str(member.id),
        'name': _public_name(member.customer_name),
        'km': round(km, 1),
        'points': competition_points(member),
        'trips': trips,
    }


def _my_team(member) -> NexusTeam | None:
    ms = (NexusTeamMember.objects
          .select_related('team')
          .filter(member=member, team__is_active=True)
          .first())
    return ms.team if ms else None


def team_state(member) -> dict:
    """Everything the team screen needs for the SIGNED-IN member only."""
    team = _my_team(member)
    if team is None:
        return {'inTeam': False, 'maxSize': nexus_teams.MAX_TEAM_SIZE,
                'label': 'Start a team with your family or friends.'}
    people = [ms.member for ms in team.memberships.select_related('member').all()]
    totals = _trip_totals(people)
    members = [_member_row(m, totals) for m in people]
    summary = nexus_teams.team_summary(members)
    return {
        'inTeam': True,
        'teamName': team.name,
        'joinCode': team.join_code,
        'maxSize': nexus_teams.MAX_TEAM_SIZE,
        **summary,
    }


def create_team(member, name: str) -> tuple[NexusTeam | None, str]:
    name = (name or '').strip()[:40]
    if not name:
        return None, 'Give your team a name.'
    if _my_team(member) is not None:
        return None, 'You are already in a team. Leave it first.'
    for _ in range(6):                      # retry on the astronomically unlikely clash
        code = _random_code(JOIN_CODE_LENGTH)
        try:
            with transaction.atomic():
                team = NexusTeam.objects.create(name=name, join_code=code, created_by=member)
                NexusTeamMember.objects.create(team=team, member=member)
            return team, ''
        except IntegrityError:
            continue
    return None, 'Could not create the team just now — please try again.'


def join_team(member, code: str) -> tuple[NexusTeam | None, str]:
    code = referral.normalise_code(code)
    if not code:
        return None, 'Enter the team code your family or friend gave you.'
    team = NexusTeam.objects.filter(join_code=code, is_active=True).first()
    if team is None:
        return None, 'That team code does not exist.'
    with transaction.atomic():
        # 🔴 Lock the TEAM row, then count. Django strips FOR UPDATE from any
        # aggregate, so `select_for_update().count()` is a plain SELECT COUNT(*)
        # — and locking existing child rows would not block an INSERT anyway.
        # Both friends could take the last seat and the team reached 7.
        # Locking the parent serialises the whole check-then-insert.
        NexusTeam.objects.select_for_update().get(pk=team.pk)
        taken = NexusTeamMember.objects.filter(team=team).count()
        if taken >= nexus_teams.MAX_TEAM_SIZE:
            return None, f'That team is full ({nexus_teams.MAX_TEAM_SIZE} members).'
        try:
            NexusTeamMember.objects.create(team=team, member=member)
        except IntegrityError:
            return None, 'You are already in a team. Leave it first.'
    return team, ''


def leave_team(member) -> bool:
    deleted, _ = NexusTeamMember.objects.filter(member=member).delete()
    return bool(deleted)


# ---------------------------------------------------------------------------
# 10. Free annual screening, unlocked by pulse checks
# ---------------------------------------------------------------------------

def _scan_count(member, since=None) -> int:
    """Completed pulse checks — a HealthMetric row carrying a resting HR.

    Same predicate the Nexus board and the quest already use, so there is one
    rule for "a scan happened".

    `since` implements the CFO's ruling (2026-09-09): the screening must be
    EARNED AGAIN each year. Counting all-time meant one burst of scanning in
    2026 bought a free screening in 2027, 2028 and beyond without a single
    further check — and gave nobody a reason to keep scanning.
    """
    qs = member.health_metrics.filter(resting_hr__isnull=False)
    if since is not None:
        qs = qs.filter(date__gt=since)
    return qs.count()


def screening_state_for(member) -> dict:
    latest = (ScreeningVoucher.objects
              .filter(member=member)
              .exclude(status=ScreeningVoucher.Status.CANCELLED)
              .order_by('-issued_on')
              .first())
    # Only checks done SINCE the last voucher count toward the next one.
    since = latest.issued_on if latest else None
    scans = _scan_count(member, since=since)
    state = screening_unlock.screening_state(
        scan_count=scans,
        last_unlock=latest.issued_on if latest else None,
        today=timezone.localdate(),
    )
    state['scansDone'] = scans
    state['required'] = screening_unlock.REQUIRED_SCANS
    state['countingSince'] = since.isoformat() if since else None
    state['voucher'] = None
    if latest and latest.status == ScreeningVoucher.Status.ISSUED \
            and latest.expires_on >= timezone.localdate():
        state['voucher'] = {'code': latest.code, 'expiresOn': latest.expires_on.isoformat()}
    return state


def claim_screening(member) -> tuple[ScreeningVoucher | None, str]:
    """Issue a voucher, but only if the pure rule says it is unlocked.

    The read-then-write is inside one transaction with the member row locked,
    so two taps cannot mint two vouchers.
    """
    today = timezone.localdate()
    with transaction.atomic():
        RewardMember.objects.select_for_update().filter(pk=member.pk).first()
        state = screening_state_for(member)
        if not state['unlocked']:
            return None, state['label']
        for _ in range(6):
            code = _random_code(VOUCHER_CODE_LENGTH)
            try:
                # Each attempt gets its own savepoint: after a caught
                # IntegrityError the outer transaction is broken, and the next
                # create() would raise TransactionManagementError instead of
                # retrying. create_team already does it this way.
                with transaction.atomic():
                    voucher = ScreeningVoucher.objects.create(
                        member=member, code=code, issued_on=today,
                        expires_on=today + timezone.timedelta(days=SCREENING_VALID_DAYS),
                        scans_at_issue=state['scansDone'],
                    )
                return voucher, ''
            except IntegrityError:
                continue
    return None, 'Could not issue the voucher just now — please try again.'


# ---------------------------------------------------------------------------
# 7. Refer a friend
# ---------------------------------------------------------------------------

def code_for(member) -> str:
    """The member's referral code, stored on first use so lookups stay indexed."""
    code = referral.make_code(member.id)
    if code and member.referral_code != code:
        member.referral_code = code
        member.save(update_fields=['referral_code', 'updated_at'])
    return code


def referral_state(member) -> dict:
    from . import provisional_rates
    made = list(Referral.objects.filter(referrer=member).select_related('referred'))
    return {
        'code': code_for(member),
        'friendsJoined': len(made),
        'friendsQualified': sum(1 for r in made if r.qualified_at),
        'maxPerYear': provisional_rates.REFERRAL_MAX_PER_MEMBER_PER_YEAR,
        'pointsYou': provisional_rates.REFERRAL_POINTS_REFERRER,
        'pointsFriend': provisional_rates.REFERRAL_POINTS_FRIEND,
        'qualifyAfterDays': provisional_rates.REFERRAL_QUALIFY_AFTER_DAYS,
        # Said plainly so nobody expects points the moment a friend signs up.
        # Both sides read their OWN constant: they are equal today, and the
        # sentence would become a lie the day only one of them changes.
        'label': (
            f'You earn {provisional_rates.REFERRAL_POINTS_REFERRER} points and your '
            f'friend earns {provisional_rates.REFERRAL_POINTS_FRIEND}, once their policy '
            f'has been active for {provisional_rates.REFERRAL_QUALIFY_AFTER_DAYS} days.'
            if provisional_rates.REFERRAL_POINTS_REFERRER != provisional_rates.REFERRAL_POINTS_FRIEND
            else f'Your friend and you each earn '
                 f'{provisional_rates.REFERRAL_POINTS_REFERRER} points once their policy '
                 f'has been active for {provisional_rates.REFERRAL_QUALIFY_AFTER_DAYS} days.'),
        'friends': [{'name': _public_name(r.referred.customer_name),
                     'joined': r.created_at.date().isoformat(),
                     'qualified': bool(r.qualified_at)} for r in made],
    }


def record_referral(new_member, code: str) -> bool:
    """Link a brand-new member to whoever referred them. Never pays anything.

    Returns True only when a Referral row was written. Every refusal is silent
    by design: a bad code must not tell a stranger whether an account exists,
    and it must never block the sign-up itself.
    """
    from . import provisional_rates
    code = referral.normalise_code(code)
    if not code:
        return False
    owner = RewardMember.objects.filter(referral_code=code, is_active=True).first()
    check = referral.check_referral(
        code_owner_id=str(owner.id) if owner else None,
        new_member_id=str(new_member.id),
        existing_pairs=[(str(r.referrer_id), str(r.referred_id))
                        for r in Referral.objects.filter(referred=new_member)],
    )
    if not check['ok']:
        return False
    if provisional_rates.REFERRAL_MAX_PER_MEMBER_PER_YEAR:
        year_ago = timezone.now() - timezone.timedelta(days=365)
        if Referral.objects.filter(referrer=owner, created_at__gte=year_ago).count() \
                >= provisional_rates.REFERRAL_MAX_PER_MEMBER_PER_YEAR:
            return False
    try:
        Referral.objects.create(referrer=owner, referred=new_member, code_used=code)
    except IntegrityError:
        return False                        # already referred by someone else
    return True
