"""rewards/models.py — Alpha Rewards (Project Nexus).

Telematics-driven customer rewards platform folded into Omni from the Projects
Department's "Project Nexus" (Alpha Rewards Program). Five reward streams:
Premium-Payment, Claims-Free, Driving Points (Kgare Digital telematics),
Health Points (Diagnofirm), redeemable with partners (Choppies/Orange/…).

This is the Omni system-of-record for enrolment, points and telematics scores.
PII (customer_name / policy_number) stays server-side, shown only to authed
staff — never exported to an external model/API.
"""
from __future__ import annotations

from django.db import models

from core.models import BaseModel, Company


class RewardPartner(BaseModel):
    class Kind(models.TextChoices):
        TELEMATICS = 'telematics', 'Telematics'
        HEALTH     = 'health',     'Health'
        VOUCHER    = 'voucher',    'Voucher / Retail'
        AIRTIME    = 'airtime',    'Airtime'
        PHARMACY   = 'pharmacy',   'Pharmacy'

    name   = models.CharField(max_length=120, unique=True)
    kind   = models.CharField(max_length=20, choices=Kind.choices)
    status = models.CharField(max_length=40, default='active',
                              help_text='active / pending / future')
    notes  = models.TextField(blank=True, default='')

    class Meta(BaseModel.Meta):
        verbose_name = 'Reward Partner'
        ordering = ['name']

    def __str__(self):
        return self.name


class RewardProgram(BaseModel):
    class Code(models.TextChoices):
        PREMIUM_PAYMENT = 'premium_payment', 'Premium Payment Rewards'
        CLAIMS_FREE     = 'claims_free',     'Claims-Free Rewards'
        DRIVING         = 'driving',         'Driving Points (Telematics)'
        HEALTH          = 'health',          'Health Points'

    code        = models.CharField(max_length=20, choices=Code.choices, unique=True)
    name        = models.CharField(max_length=120)
    description = models.TextField(blank=True, default='')
    is_active   = models.BooleanField(default=True)
    config      = models.JSONField(default=dict, blank=True,
                                   help_text='Tier weights, discount %, point rules.')

    class Meta(BaseModel.Meta):
        verbose_name = 'Reward Program'
        ordering = ['code']

    def __str__(self):
        return self.name


class RewardMember(BaseModel):
    class Tier(models.TextChoices):
        BRONZE   = 'bronze',   'Bronze'
        SILVER   = 'silver',   'Silver'
        GOLD     = 'gold',     'Gold'
        PLATINUM = 'platinum', 'Platinum'

    company           = models.ForeignKey(Company, on_delete=models.PROTECT,
                                           related_name='reward_members', null=True, blank=True)
    policy_number     = models.CharField(max_length=60, blank=True, default='')
    customer_name     = models.CharField(max_length=200)
    # Customer self-service login (email OTP). Blank until the member enrols /
    # first signs in to the customer app. PII — never sent to an AI/external.
    email             = models.EmailField(blank=True, default='')
    tier              = models.CharField(max_length=12, choices=Tier.choices, default=Tier.BRONZE)
    points_balance    = models.IntegerField(default=0)
    claims_free_months = models.IntegerField(default=0)
    # Derived from the member id (rewards/referral.py make_code) and stored so a
    # friend's typed code resolves in ONE indexed query instead of a table scan.
    referral_code  = models.CharField(max_length=12, blank=True, default='', db_index=True)
    enrolled_at       = models.DateField(null=True, blank=True)
    is_active         = models.BooleanField(default=True)

    class Meta(BaseModel.Meta):
        verbose_name = 'Reward Member'
        ordering = ['-points_balance', 'customer_name']

    def __str__(self):
        return f'{self.customer_name} ({self.get_tier_display()})'


class PointsTransaction(BaseModel):
    class Kind(models.TextChoices):
        EARN   = 'earn',   'Earn'
        REDEEM = 'redeem', 'Redeem'
        ADJUST = 'adjust', 'Adjustment'

    member      = models.ForeignKey(RewardMember, on_delete=models.CASCADE, related_name='transactions')
    program     = models.ForeignKey(RewardProgram, on_delete=models.PROTECT,
                                    related_name='transactions', null=True, blank=True)
    partner     = models.ForeignKey(RewardPartner, on_delete=models.SET_NULL,
                                    related_name='transactions', null=True, blank=True)
    kind        = models.CharField(max_length=10, choices=Kind.choices)
    points      = models.IntegerField(help_text='+ on earn, - on redeem')
    detail      = models.CharField(max_length=300, blank=True, default='')
    occurred_at = models.DateTimeField()

    class Meta(BaseModel.Meta):
        verbose_name = 'Points Transaction'
        ordering = ['-occurred_at']

    def __str__(self):
        return f'{self.member.customer_name} {self.kind} {self.points}'


class DrivingScore(BaseModel):
    """A telematics period score from Kgare Digital → driving points."""
    member        = models.ForeignKey(RewardMember, on_delete=models.CASCADE, related_name='driving_scores')
    period        = models.CharField(max_length=10, help_text='YYYY-MM')
    vehicle_reg   = models.CharField(max_length=20, blank=True, default='')
    distance_km   = models.DecimalField(max_digits=10, decimal_places=1, default=0)
    idle_minutes  = models.IntegerField(default=0)
    harsh_events  = models.IntegerField(default=0)
    score         = models.IntegerField(default=0, help_text='0-100 driving score')
    points_awarded = models.IntegerField(default=0)
    source        = models.CharField(max_length=60, default='Kgare Digital')

    class Meta(BaseModel.Meta):
        verbose_name = 'Driving Score'
        ordering = ['-period']

    def __str__(self):
        return f'{self.member.customer_name} {self.period} score={self.score}'


class HealthConsent(BaseModel):
    """A member's granular opt-in to share health/activity data with Rewards.

    Consent-first & granular: `data_types` lists EXACTLY the categories the
    member ticked (e.g. ['steps', 'sleep']). The app must never read a type
    that isn't here, and consent can be revoked (set is_active=False / re-post).

    DPA boundary: health activity is special-category data, used for REWARDS
    ONLY — never underwriting, pricing, or claims.
    """
    member     = models.ForeignKey(RewardMember, on_delete=models.CASCADE, related_name='health_consents')
    data_types = models.JSONField(default=list, blank=True,
                                  help_text="List of consented categories, e.g. ['steps','sleep'].")
    granted_at = models.DateTimeField()
    is_active  = models.BooleanField(default=True)

    class Meta(BaseModel.Meta):
        verbose_name = 'Health Consent'
        ordering = ['-granted_at']

    def __str__(self):
        return f'{self.member_id} consent {self.data_types}'


class HealthMetric(BaseModel):
    """One day of consented health activity for a member → reward points.

    Telematics analogue of DrivingScore, but keyed per member+date so a
    re-post for the same day is idempotent (unique_together) and never
    double-awards. The view recomputes the delta against points_awarded.

    DPA boundary: rewards-only special-category data. No name is stored here —
    the member is resolved by FK; the metric payload carries no identifiers.
    """
    member        = models.ForeignKey(RewardMember, on_delete=models.CASCADE, related_name='health_metrics')
    date          = models.DateField()
    steps         = models.IntegerField(default=0)
    sleep_minutes = models.IntegerField(null=True, blank=True)
    points_awarded = models.IntegerField(default=0)
    source        = models.CharField(max_length=60, default='Health App')

    # --- Alpha Thrive vitals (2026-06-26) ---------------------------------
    # Derived NUMBERS only, computed from a finger-PPG scan (HR + HRV-stress +
    # respiration). Wellness, never diagnosis. NO blood pressure. Raw camera
    # frames NEVER leave the device — only these scalars are stored. All
    # nullable so historical step-only rows stay valid.
    resting_hr        = models.IntegerField(null=True, blank=True,
                                            help_text='Beats per minute from the scan.')
    hrv_sdnn          = models.FloatField(null=True, blank=True, help_text='HRV SDNN (ms).')
    hrv_rmssd         = models.FloatField(null=True, blank=True, help_text='HRV RMSSD (ms).')
    hrv_pnn50         = models.FloatField(null=True, blank=True, help_text='HRV pNN50 (%).')
    respiration_rate  = models.FloatField(null=True, blank=True, help_text='Breaths per minute.')
    stress_band       = models.CharField(max_length=12, blank=True, default='',
                                         help_text='calm / moderate / stressed (from RMSSD).')
    scan_confidence   = models.FloatField(null=True, blank=True,
                                          help_text='0-1 signal-quality of the PPG scan.')

    class Meta(BaseModel.Meta):
        verbose_name = 'Health Metric'
        ordering = ['-date']
        unique_together = [('member', 'date')]

    def __str__(self):
        return f'{self.member_id} {self.date} steps={self.steps}'


class CustomerLoginCode(BaseModel):
    """A one-time email login code for the customer app (Nexus/Rewards/Thrive).

    Only the SHA-256 hash of the 6-digit code is stored — never the code
    itself. Short-lived, attempt-limited, single-use. DPA: this is the
    customer authentication path; codes are emailed to the member only and
    never logged.
    """
    member     = models.ForeignKey(RewardMember, on_delete=models.CASCADE, related_name='login_codes')
    code_hash  = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    attempts   = models.IntegerField(default=0)
    consumed   = models.BooleanField(default=False)

    class Meta(BaseModel.Meta):
        verbose_name = 'Customer Login Code'
        ordering = ['-created_at']

    def __str__(self):
        return f'login code for {self.member_id} (consumed={self.consumed})'


class CustomerDriveTrip(BaseModel):
    """A single 'Click & Drive' trip recorded in the customer app.

    The phone tracks GPS locally and sends only AGGREGATE NUMBERS — distance,
    duration, idle, harsh-event count, max speed. Raw coordinates never leave
    the device (DPA: no location track stored). The server re-derives the score
    from these aggregates (drive_score.py) so a client cannot fake it.
    """
    member         = models.ForeignKey(RewardMember, on_delete=models.CASCADE, related_name='drive_trips')
    started_at     = models.DateTimeField()
    distance_km    = models.FloatField(default=0)
    duration_min   = models.FloatField(default=0)
    idle_minutes   = models.FloatField(default=0)
    harsh_events   = models.IntegerField(default=0)
    max_speed      = models.FloatField(default=0, help_text='km/h as reported by the phone')
    score          = models.IntegerField(default=0, help_text='0-100 trip safety score')
    points_awarded = models.IntegerField(default=0)
    # Data-quality verdict from drive_score.trip_quality() — the ONE parser.
    # A row is never deleted (audit); an invalid row is simply never graded.
    is_valid       = models.BooleanField(default=True, db_index=True,
                                         help_text='False = not a real drive (GPS spike / no distance); excluded from every aggregate')
    invalid_reason = models.CharField(max_length=32, blank=True, default='')
    speed_reliable = models.BooleanField(default=True,
                                         help_text='False = the peak-speed reading is not supported by the distance covered')

    class Meta(BaseModel.Meta):
        verbose_name = 'Customer Drive Trip'
        ordering = ['-started_at']

    def __str__(self):
        return f'{self.member_id} trip {self.distance_km:.1f}km score={self.score}'


class NexusTeam(BaseModel):
    """A family-or-friends team on the Nexus board (CFO 2026-09-08, "6 is good").

    Teams exist because solo streaks only motivate the already-motivated: a
    weekly target shared with your spouse or three friends is a different kind
    of pressure, and each team recruits its own members for us.

    A member belongs to at most ONE team (enforced on NexusTeamMember), and a
    team is only ever readable BY ITS OWN MEMBERS — there is deliberately no
    read-a-team-by-id endpoint, so one family cannot see another's data.
    """
    name       = models.CharField(max_length=40)
    join_code  = models.CharField(max_length=12, unique=True, db_index=True,
                                  help_text='Short code a friend types to join')
    created_by = models.ForeignKey('RewardMember', on_delete=models.SET_NULL, null=True,
                                   blank=True, related_name='teams_created')
    is_active  = models.BooleanField(default=True)

    class Meta(BaseModel.Meta):
        verbose_name = 'Nexus Team'

    def __str__(self):
        return f'{self.name} ({self.join_code})'


class NexusTeamMember(BaseModel):
    """Membership of exactly one team. The unique constraint IS the rule."""
    team      = models.ForeignKey(NexusTeam, on_delete=models.CASCADE, related_name='memberships')
    member    = models.ForeignKey('RewardMember', on_delete=models.CASCADE, related_name='team_memberships')
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta(BaseModel.Meta):
        verbose_name = 'Nexus Team Member'
        constraints = [
            # One team per member — the double-count and the "which team am I
            # in" ambiguity are both impossible if this holds.
            models.UniqueConstraint(fields=['member'], name='uniq_member_one_team'),
        ]

    def __str__(self):
        return f'{self.member_id} in {self.team_id}'


class ScreeningVoucher(BaseModel):
    """A free annual health screening earned with pulse checks (CFO, "10 is good").

    Turns the 30-second pulse check into something real. The COST of the
    screening is a CFO commercial term and is not stored here — this row is an
    entitlement, not a payment. Issued at most once per WINDOW_DAYS
    (rewards/screening_unlock.py), which the issuing endpoint enforces.
    """
    class Status(models.TextChoices):
        ISSUED   = 'issued',   'Issued'
        USED     = 'used',     'Used'
        EXPIRED  = 'expired',  'Expired'
        CANCELLED = 'cancelled', 'Cancelled'

    member     = models.ForeignKey('RewardMember', on_delete=models.CASCADE,
                                   related_name='screening_vouchers')
    code       = models.CharField(max_length=16, unique=True, db_index=True,
                                  help_text='Shown to the member and read at the clinic')
    issued_on  = models.DateField()
    expires_on = models.DateField()
    status     = models.CharField(max_length=12, choices=Status.choices, default=Status.ISSUED)
    scans_at_issue = models.IntegerField(default=0, help_text='Pulse checks the member had when this was earned')
    used_at    = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        verbose_name = 'Screening Voucher'

    def __str__(self):
        return f'{self.code} ({self.status})'


class Referral(BaseModel):
    """One friend referred by one member (CFO 2026-09-08, "7 is good").

    🔴 The reward is NOT paid at signup. provisional_rates.py records the rule:
    pay on a policy that ACTIVATES and STICKS (REFERRAL_QUALIFY_AFTER_DAYS),
    because paying at signup invites fake referrals. So this row is created at
    signup with qualified_at NULL and points_awarded 0, and a separate job
    awards it later — nothing pays out until the CFO's qualification data
    (a linked, active policy) is available.
    """
    referrer       = models.ForeignKey('RewardMember', on_delete=models.CASCADE,
                                       related_name='referrals_made')
    referred       = models.ForeignKey('RewardMember', on_delete=models.CASCADE,
                                       related_name='referral_received')
    code_used      = models.CharField(max_length=12)
    qualified_at   = models.DateTimeField(null=True, blank=True,
                                          help_text='When the referred policy met the waiting period')
    points_awarded = models.IntegerField(default=0, help_text='0 until qualified — never paid at signup')

    class Meta(BaseModel.Meta):
        verbose_name = 'Referral'
        constraints = [
            # A member can only ever be referred ONCE, by one person.
            models.UniqueConstraint(fields=['referred'], name='uniq_referred_once'),
        ]

    def __str__(self):
        return f'{self.referrer_id} -> {self.referred_id}'


class CustomerActivity(BaseModel):
    """A wellness/lifestyle activity a customer logs in the app to earn points.

    Mirrors the staff rewards programme (Fitness Activity / Healthy Eating /
    Steps) on the customer side so members can earn beyond driving + the pulse
    scan. Points feed the Nexus Score. One EARNING per kind per day (anti-abuse);
    extra logs are still recorded but award 0.
    """
    class Kind(models.TextChoices):
        FITNESS        = 'fitness',        'Fitness Activity'
        HEALTHY_EATING = 'healthy_eating', 'Healthy Eating'
        STEPS          = 'steps',          'Daily Steps'

    member         = models.ForeignKey(RewardMember, on_delete=models.CASCADE, related_name='activities')
    kind           = models.CharField(max_length=20, choices=Kind.choices)
    detail         = models.CharField(max_length=200, blank=True, default='')
    value          = models.IntegerField(default=0, help_text='e.g. step count')
    points_awarded = models.IntegerField(default=0)
    occurred_at    = models.DateTimeField()

    class Meta(BaseModel.Meta):
        verbose_name = 'Customer Activity'
        ordering = ['-occurred_at']

    def __str__(self):
        return f'{self.member_id} {self.kind} +{self.points_awarded}'


class CustomerDevicePairingCode(BaseModel):
    """One-time code that binds a phone to a member for verified STEP sync (v10).

    The logged-in web page (a valid CustomerSession) asks the backend to mint a
    code; the code is carried by an `intent://` link into the native
    PairingActivity, which swaps it for a device token. The backend knows which
    member the code belongs to because IT minted the code under that member's
    authenticated session — the native side never needs the web session.

    Only the SHA-256 hash is stored. Short-lived, single-use, attempt-limited —
    the same discipline as CustomerLoginCode.
    """
    member     = models.ForeignKey(RewardMember, on_delete=models.CASCADE, related_name='pairing_codes')
    code_hash  = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    attempts   = models.IntegerField(default=0)
    consumed   = models.BooleanField(default=False)

    class Meta(BaseModel.Meta):
        verbose_name = 'Customer Device Pairing Code'
        ordering = ['-created_at']

    def __str__(self):
        return f'pairing code for {self.member_id} (consumed={self.consumed})'


class CustomerDeviceToken(BaseModel):
    """A device-bound token that authorises ONE thing only: posting synced steps.

    Deliberately SEPARATE from CustomerSession. A CustomerSession (the 30-day
    email-OTP bearer) can reach policy/points/profile data; a device token must
    NOT — it is honoured only by the step-sync endpoint. So a token lifted off a
    phone can, at worst, post step counts (which are capped and de-duplicated),
    never read a customer's data. Revoked on logout / account delete.

    Only the SHA-256 hash of the token is stored.
    """
    member     = models.ForeignKey(RewardMember, on_delete=models.CASCADE, related_name='device_tokens')
    token_hash = models.CharField(max_length=64, unique=True)
    device_label = models.CharField(max_length=80, blank=True, default='')
    revoked    = models.BooleanField(default=False)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        verbose_name = 'Customer Device Token'
        ordering = ['-created_at']

    def __str__(self):
        return f'device token for {self.member_id} (revoked={self.revoked})'


class CustomerStepDay(BaseModel):
    """The per-(member, day) synced-step ledger — the anti-replay heart of v10.

    Health Connect / Apple Health steps are posted here from the paired device.
    Points are awarded on the GROWTH of the day's total, not on each POST:
      * a re-sync with a higher total tops up the points (the delta),
      * an exact replay of the same total awards 0,
      * a lower total (clock skew, another source) awards 0 and never subtracts.
    One row per member per day (unique) makes double-counting structurally
    impossible.

    COMPLIANCE (CFO 16-Aug-2026): these points are HEALTH-derived and may fund
    REWARDS ONLY (perks/vouchers). They must NEVER feed a premium discount —
    Google Play forbids using health data for insurance pricing. See
    rewards/provisional_rates.py.
    """
    member         = models.ForeignKey(RewardMember, on_delete=models.CASCADE, related_name='step_days')
    day            = models.DateField()
    steps_total    = models.IntegerField(default=0, help_text='Highest synced total seen for the day')
    points_awarded = models.IntegerField(default=0, help_text='Cumulative points paid for the day')
    source         = models.CharField(max_length=24, default='health_connect')

    class Meta(BaseModel.Meta):
        verbose_name = 'Customer Step Day'
        ordering = ['-day']
        constraints = [
            models.UniqueConstraint(fields=['member', 'day'], name='uniq_member_step_day'),
        ]

    def __str__(self):
        return f'{self.member_id} {self.day} {self.steps_total} steps (+{self.points_awarded})'


class CustomerWorkoutSession(BaseModel):
    """The per-session verified-workout ledger — Alpha Nexus v11.

    Exercise sessions are read from Health Connect (READ_EXERCISE) by the paired
    phone and posted with the same steps-only device token. Each row is ONE
    Health Connect session, unique per (member, session_id), so a replay of the
    same session can never pay twice. The server decides the points — a client
    value is never trusted — at +2 per real session, at most 2 paid sessions a
    day (what the retired one-tap "Log a workout" used to pay).

    Only aggregate facts are kept (type code, start/end, minutes) — never a
    route, GPS trace or heart-rate sample.

    COMPLIANCE (CFO 16-Aug-2026): these points are HEALTH-derived and may fund
    REWARDS ONLY (perks/vouchers). They must NEVER feed a premium discount —
    Google Play forbids using health data for insurance pricing.
    """
    member         = models.ForeignKey(RewardMember, on_delete=models.CASCADE, related_name='workout_sessions')
    session_id     = models.CharField(max_length=128, help_text='Health Connect ExerciseSessionRecord id')
    day            = models.DateField(help_text='Local day the session started')
    exercise_type  = models.IntegerField(default=0, help_text='Health Connect exercise type code')
    started_at     = models.DateTimeField()
    ended_at       = models.DateTimeField()
    duration_minutes = models.IntegerField(default=0)
    points_awarded = models.IntegerField(default=0)
    source         = models.CharField(max_length=24, default='health_connect')

    class Meta(BaseModel.Meta):
        verbose_name = 'Customer Workout Session'
        ordering = ['-started_at']
        constraints = [
            models.UniqueConstraint(fields=['member', 'session_id'], name='uniq_member_workout_session'),
        ]

    def __str__(self):
        return f'{self.member_id} {self.day} workout {self.duration_minutes}m (+{self.points_awarded})'


class NexusEmailQuota(BaseModel):
    """Per-member, per-day counter for Alpha Nexus standings emails.

    Caps the competition standings nudge at 2 per registered tester per day, so
    repeated button clicks / crons can never spam people.
    """
    member = models.ForeignKey(RewardMember, on_delete=models.CASCADE, related_name='email_quota')
    day    = models.DateField()
    count  = models.IntegerField(default=0)

    class Meta(BaseModel.Meta):
        verbose_name = 'Nexus Email Quota'
        unique_together = [('member', 'day')]
        ordering = ['-day']

    def __str__(self):
        return f'{self.member_id} {self.day} x{self.count}'


class CustomerSession(BaseModel):
    """A bearer-token session for a logged-in customer.

    Stores only the SHA-256 hash of the token. The customer app sends the raw
    token as `Authorization: Bearer <token>`; customer_auth.resolve_member()
    maps it back to exactly one RewardMember, so a customer can only ever see
    their own data. Revocable + expiring.
    """
    member     = models.ForeignKey(RewardMember, on_delete=models.CASCADE, related_name='sessions')
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    revoked    = models.BooleanField(default=False)

    class Meta(BaseModel.Meta):
        verbose_name = 'Customer Session'
        ordering = ['-created_at']

    def __str__(self):
        return f'session for {self.member_id} (revoked={self.revoked})'


class CustomerFeedback(BaseModel):
    """In-app feedback / short survey — 'shape Nexus around what members value'
    (Medu 2026-07). A 1-5 rating + which part of the app + optional comment and
    a 'what would you want next' note. No points; pure product signal so the
    team builds Drive/Wellness/Rewards around real demand, not assumptions.
    """
    class Area(models.TextChoices):
        OVERALL  = 'overall',  'Overall app'
        DRIVE    = 'drive',    'Nexus Drive'
        WELLNESS = 'wellness', 'Wellness'
        REWARDS  = 'rewards',  'Rewards'
        OTHER    = 'other',    'Something else'

    member  = models.ForeignKey(RewardMember, on_delete=models.CASCADE, related_name='feedback')
    rating  = models.PositiveSmallIntegerField(default=0, help_text='1-5 stars')
    area    = models.CharField(max_length=20, choices=Area.choices, default=Area.OVERALL)
    comment = models.TextField(blank=True, default='')
    wants   = models.TextField(blank=True, default='', help_text='what the member wants next')

    class Meta(BaseModel.Meta):
        verbose_name = 'Customer Feedback'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.member_id} {self.area} {self.rating}★'
