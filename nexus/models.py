"""Nexus data model — drivers, scored trips, and the rewards audit ledger (P2)."""
from __future__ import annotations

import uuid

from django.contrib.auth.models import User
from django.db import models

from core.models import AuditableMixin


# ---------------------------------------------------------------------------
# Vehicle Register choices (pool-car checkout / check-in — CFO/EXCO 2026-07-16)
# ---------------------------------------------------------------------------
class VehicleStatus(models.TextChoices):
    AVAILABLE   = 'available',   'Available'
    OUT         = 'out',         'Out'
    MAINTENANCE = 'maintenance', 'Under maintenance'
    RETIRED     = 'retired',     'Retired'


class TripPurpose(models.TextChoices):
    # Original approved list
    EVENTS            = 'events',            'Events'
    CUSTOMER_VISITS   = 'customer_visits',   'Customer visits'
    CUSTOMER_GIFTS    = 'customer_gifts',    'Buying customer gifts'
    DELIVER_GOODS     = 'deliver_goods',     'Delivery of goods to customers'
    PICKUP_CUSTOMERS  = 'pickup_customers',  'Picking up customers'
    PICKUP_BROKERS    = 'pickup_brokers',    'Picking up brokers'
    PICKUP_COMPUTERS  = 'pickup_computers',  'Picking up computers'
    PICKUP_STATIONERY = 'pickup_stationery', 'Picking up stationery'
    PICKUP_WATER      = 'pickup_water',      'Picking up water'
    # Ten additions (CFO/EXCO 2026-07-16)
    BANK_DEPOSIT      = 'bank_deposit',      'Bank / cash deposits'
    NBFIRA_SUBMISSION = 'nbfira_submission', 'NBFIRA / regulator document submission'
    COURIER_POST      = 'courier_post',      'Courier / post collection or drop-off'
    AIRPORT_TRANSFER  = 'airport_transfer',  'Staff airport transfers (business)'
    VEHICLE_SERVICING = 'vehicle_servicing', 'Vehicle servicing / fuel / car wash'
    MARKETING_TRANSPORT = 'marketing_transport', 'Marketing / branding material transport'
    CHOPPIES_POS      = 'choppies_pos',      'Choppies point-of-sale support visits'
    CLAIMS_INSPECTION = 'claims_inspection', 'Claims site / salvage inspection'
    SUPPLIER_COLLECT  = 'supplier_collect',  'Supplier or vendor collections'
    INTERBRANCH_DOCS  = 'interbranch_docs',  'Inter-branch document transfer'
    # Escape hatch — forces a note and flags the trip for review.
    OTHER             = 'other',             'Other (explain — flagged for review)'


class FuelLevel(models.TextChoices):
    EMPTY   = 'empty',   'Empty'
    QUARTER = 'quarter', '¼'
    HALF    = 'half',    '½'
    THREE_Q = 'three_q', '¾'
    FULL    = 'full',    'Full'


class TripState(models.TextChoices):
    CHECKED_OUT       = 'checked_out',       'Checked out'
    RETURNED_PENDING  = 'returned_pending',  'Returned — pending sign-off'
    CLOSED            = 'closed',            'Closed'


class PhotoKind(models.TextChoices):
    PRE_TRIP      = 'pre_trip',      'Pre-trip condition'
    RETURN_DAMAGE = 'return_damage', 'Damage on return'


class NexusDriver(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    full_name = models.CharField(max_length=120)
    external_ref = models.CharField(max_length=64, blank=True, default="")
    total_points = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["full_name"]
        verbose_name = "Nexus Driver"

    def __str__(self):
        return self.full_name


class NexusTrip(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    driver = models.ForeignKey(NexusDriver, on_delete=models.CASCADE, related_name="trips")
    started_at = models.DateTimeField()
    label = models.CharField(max_length=120, blank=True, default="")
    distance_km = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    duration_minutes = models.IntegerField(default=0)
    idle_minutes = models.IntegerField(default=0)
    harsh_brakes = models.IntegerField(default=0)
    speeding_events = models.IntegerField(default=0)
    score = models.IntegerField(default=0)      # computed by the engine
    points = models.IntegerField(default=0)     # computed reward points
    external_trip_id = models.CharField(max_length=64, blank=True, default="", db_index=True)  # WebFleet tripid (dedupe)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-started_at"]
        verbose_name = "Nexus Trip"

    def __str__(self):
        return f"{self.driver.full_name} — {self.label or self.started_at:%Y-%m-%d} ({self.score})"


class NexusRewardLedger(models.Model):
    """Append-only audit trail of every points movement (P2 — accrual + audit)."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    driver = models.ForeignKey(NexusDriver, on_delete=models.CASCADE, related_name="ledger")
    trip = models.ForeignKey(NexusTrip, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    description = models.CharField(max_length=200)
    points = models.IntegerField(default=0)     # +accrual / -deduction
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Nexus Reward Ledger Entry"

    def __str__(self):
        return f"{self.driver.full_name}: {self.points:+d} — {self.description}"


class FleetVehicle(models.Model):
    """A company vehicle tracked by Cartrack — the latest known state, refreshed
    by `manage.py pull_cartrack`. This is Alpha Direct's OWN fleet (not the
    insurance-telematics customer vehicles). One row per Cartrack unit."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cartrack_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    registration = models.CharField(max_length=32, db_index=True)   # number plate
    make = models.CharField(max_length=60, blank=True, default="")
    model = models.CharField(max_length=60, blank=True, default="")
    description = models.CharField(max_length=120, blank=True, default="")

    # Register attributes — manual data-entry (populated from Wame's car list;
    # left blank until the reg numbers + odometer readings are received).
    year = models.PositiveIntegerField(null=True, blank=True)
    vin = models.CharField(max_length=40, blank=True, default="")
    colour = models.CharField(max_length=30, blank=True, default="")
    home_yard = models.CharField(max_length=80, blank=True, default="Head Office")
    # Where the keys are physically held (reception / Exco office / a branch).
    # `home_yard` above doubles as this in the register UI ("Keys held at").
    # Existing damage / condition on intake. A dedicated field (NOT `description`,
    # which the Cartrack sync overwrites) so the intake note survives a fleet pull.
    condition_notes = models.CharField(max_length=300, blank=True, default="")
    # Register availability state (drives the checkout gate + the live board).
    # Distinct from the Cartrack live telemetry below; `odometer_km` doubles as
    # the current odometer (Cartrack-refreshed once keys land; manual until then).
    status = models.CharField(max_length=16, choices=VehicleStatus.choices,
                              default=VehicleStatus.AVAILABLE, db_index=True)

    # Latest status (from GET /vehicles/status)
    last_lat = models.FloatField(null=True, blank=True)
    last_lng = models.FloatField(null=True, blank=True)
    last_speed_kmh = models.FloatField(null=True, blank=True)
    odometer_km = models.FloatField(null=True, blank=True)
    ignition_on = models.BooleanField(null=True, blank=True)
    moving = models.BooleanField(null=True, blank=True)
    where = models.CharField(max_length=200, blank=True, default="")   # address text
    driver_name = models.CharField(max_length=120, blank=True, default="")
    last_seen = models.CharField(max_length=40, blank=True, default="")  # provider timestamp (as given)

    is_demo = models.BooleanField(default=False)   # sample row for UI preview before the key lands
    raw = models.JSONField(default=dict, blank=True)   # raw status row, for field reconciliation
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["registration"]
        verbose_name = "Fleet Vehicle"
        constraints = [
            models.UniqueConstraint(fields=["registration"], name="fleet_vehicle_reg_uniq"),
        ]

    def __str__(self):
        return f"{self.registration} ({self.make} {self.model})".strip()


class PhonePing(models.Model):
    """A live location ping from a phone telematics app (e.g. GPSLogger on the
    CFO's Samsung POSTing to /api/v1/nexus/phone-ping/). Lets a personal phone
    feed Nexus live, alongside the WebFleet vehicle feed. Token-gated, not SSO."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    device = models.CharField(max_length=80, db_index=True)   # device serial / name from the app
    lat = models.FloatField(null=True, blank=True)
    lng = models.FloatField(null=True, blank=True)
    speed_kmh = models.FloatField(default=0)
    recorded_at = models.DateTimeField(null=True, blank=True)  # device timestamp
    received_at = models.DateTimeField(auto_now_add=True, db_index=True)
    raw = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-received_at"]
        verbose_name = "Nexus Phone Ping"
        indexes = [models.Index(fields=["device", "-received_at"], name="nexus_ping_dev_rcv_idx")]

    def __str__(self):
        return f"{self.device} @ {self.received_at:%Y-%m-%d %H:%M}"


class VehicleTrip(AuditableMixin, models.Model):
    """A single pool-car checkout / check-in register record (CFO/EXCO
    2026-07-16). Replaces the paper access books: dual sign-off (driver on
    collection + driver on return + receptionist on return) and every trip
    tied to an approved business purpose. Immutable audit via AuditableMixin.

    Forward-compatible with Cartrack: the nullable gps_* / cartrack_trip_id
    fields let `pull_cartrack` later auto-populate odometer + confirm the
    physical return, replacing manual odometer entry / the reception sign-off.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    vehicle = models.ForeignKey(FleetVehicle, on_delete=models.PROTECT, related_name="trips")

    # Driver — SSO staff account when we have one, plus a name snapshot so the
    # record stays readable even if the account is later deactivated.
    driver_user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="vehicle_trips")
    driver_name = models.CharField(max_length=120)

    # Every trip MUST map to an approved reason. `purpose_notes` is mandatory
    # when purpose == OTHER (enforced in the view) and OTHER auto-flags.
    purpose = models.CharField(max_length=24, choices=TripPurpose.choices, db_index=True)
    purpose_notes = models.TextField(blank=True, default="")
    destination = models.CharField(max_length=200)

    # Checkout leg (collection)
    checkout_at = models.DateTimeField(null=True, blank=True)
    odometer_out = models.PositiveIntegerField(null=True, blank=True)
    fuel_level_out = models.CharField(max_length=8, choices=FuelLevel.choices, blank=True, default="")
    driver_condition_confirm = models.BooleanField(default=False)   # driver signs: saw car, took it
    pre_trip_notes = models.TextField(blank=True, default="")       # pre-existing damage notes
    expected_return_at = models.DateTimeField(null=True, blank=True)

    # Check-in leg (return)
    checkin_at = models.DateTimeField(null=True, blank=True)
    odometer_in = models.PositiveIntegerField(null=True, blank=True)
    fuel_level_in = models.CharField(max_length=8, choices=FuelLevel.choices, blank=True, default="")
    driver_return_confirm = models.BooleanField(default=False)      # driver signs: returned in good condition
    damage_on_return = models.BooleanField(default=False)
    damage_notes = models.TextField(blank=True, default="")

    # Receptionist (Wame) sign-off — confirms condition on return.
    receptionist_confirm = models.BooleanField(default=False)
    receptionist_user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                          related_name="+")
    receptionist_signoff_at = models.DateTimeField(null=True, blank=True)
    receptionist_notes = models.TextField(blank=True, default="")

    status = models.CharField(max_length=20, choices=TripState.choices,
                              default=TripState.CHECKED_OUT, db_index=True)
    flagged = models.BooleanField(default=False, db_index=True)     # damage / Other / anomaly → review
    flag_reason = models.CharField(max_length=200, blank=True, default="")

    # Cartrack forward-compat (nullable now; auto-populated once keys land).
    gps_checkout_lat = models.FloatField(null=True, blank=True)
    gps_checkout_lng = models.FloatField(null=True, blank=True)
    gps_checkin_lat = models.FloatField(null=True, blank=True)
    gps_checkin_lng = models.FloatField(null=True, blank=True)
    cartrack_trip_id = models.CharField(max_length=64, blank=True, default="", db_index=True)

    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-checkout_at", "-created_at"]
        verbose_name = "Vehicle Trip"
        indexes = [
            models.Index(fields=["vehicle", "status"], name="vehtrip_veh_status_idx"),
            models.Index(fields=["status", "expected_return_at"], name="vehtrip_status_due_idx"),
        ]

    def __str__(self):
        return f"{self.vehicle.registration} — {self.driver_name} ({self.get_purpose_display()})"

    @property
    def distance_km(self):
        if self.odometer_in is not None and self.odometer_out is not None:
            return max(0, self.odometer_in - self.odometer_out)
        return None

    @property
    def is_overdue(self) -> bool:
        """Open trip whose expected return time has passed with no check-in."""
        from django.utils import timezone
        return bool(
            self.status == TripState.CHECKED_OUT
            and self.expected_return_at
            and timezone.now() > self.expected_return_at
        )


class VehicleTripPhoto(models.Model):
    """Condition photo attached to a trip — pre-trip (checkout) or damage on
    return. Streamed only through the auth-gated download view (never a raw
    FileField.url), matching the HR-document pattern."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    trip = models.ForeignKey(VehicleTrip, on_delete=models.CASCADE, related_name="photos")
    kind = models.CharField(max_length=16, choices=PhotoKind.choices, default=PhotoKind.PRE_TRIP)
    image = models.FileField(upload_to="vehicle_register/")
    caption = models.CharField(max_length=200, blank=True, default="")
    uploaded_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "Vehicle Trip Photo"

    def __str__(self):
        return f"{self.get_kind_display()} — {self.trip_id}"
