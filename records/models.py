"""
records/models.py — physical records register and chain of custody.

Requested by Admin (6 Aug 2026): a central register of physical records
— storeroom in/out, files issued to legal, who holds what right now — replacing a
self-hosted HTML page so Admin and Human Capital can both see it.

WHY THIS IS NOT PART OF THE FIXED-ASSET REGISTER
`assets.Asset` looked like the obvious home: `AssetAssignment` already models
from-person / to-person / from-location / to-location with a reason and a date,
which is exactly a file leaving the storeroom for a lawyer. It was rejected because
`Asset` carries cost, depreciation method and VAT treatment and feeds fixed-asset
reporting. Loading hundreds of paper files into it would corrupt that register for
the sake of reusing four fields. The movement PATTERN is copied deliberately; the
table is separate.

Records differ from assets in ways that matter here: they have a retention date and
get destroyed on a schedule, they can be frozen under legal hold, and they carry a
confidentiality class because HR and legal files are personal data.
"""

from django.conf import settings
from django.db import models

from core.models import AuditableMixin, BaseModel
from django.utils import timezone


class RecordCategory(AuditableMixin, BaseModel):
    """HR file, legal matter, policy file, finance voucher, and so on."""

    name           = models.CharField(max_length=120, unique=True)
    description    = models.TextField(blank=True, default='')
    # Default retention in months, applied to new records in this category. Null
    # means "no standing rule — set it per record".
    retention_months = models.PositiveIntegerField(null=True, blank=True)
    active         = models.BooleanField(default=True)

    class Meta(BaseModel.Meta):
        verbose_name_plural = 'record categories'
        ordering = ['name']

    def __str__(self):
        return self.name


class RecordItem(AuditableMixin, BaseModel):
    """One physical record — a file, a box, a bound volume."""

    class Confidentiality(models.TextChoices):
        PUBLIC       = 'public',       'Public'
        INTERNAL     = 'internal',     'Internal'
        CONFIDENTIAL = 'confidential', 'Confidential'
        # HR files, medical, anything with an Omang in it. Drives who may look.
        RESTRICTED   = 'restricted',   'Restricted — personal data'

    class Status(models.TextChoices):
        IN_STORE  = 'in_store',  'In the storeroom'
        ISSUED    = 'issued',    'Issued out'
        ARCHIVED  = 'archived',  'Archived off-site'
        DESTROYED = 'destroyed', 'Destroyed'
        LOST      = 'lost',      'Missing'

    reference       = models.CharField(
        max_length=60, unique=True,
        help_text='The reference written on the file itself.')
    title           = models.CharField(max_length=250)
    category        = models.ForeignKey(
        RecordCategory, on_delete=models.PROTECT, related_name='items')
    company         = models.ForeignKey(
        'core.Company', on_delete=models.PROTECT,
        related_name='record_items', null=True, blank=True)
    confidentiality = models.CharField(
        max_length=20, choices=Confidentiality.choices,
        default=Confidentiality.INTERNAL)
    status          = models.CharField(
        max_length=20, choices=Status.choices, default=Status.IN_STORE)

    # Where it is NOW. Kept on the item so the register answers "who has it?"
    # without walking the movement history on every row.
    current_holder   = models.ForeignKey(
        'payroll.Employee', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='records_held')
    # Free text so a record can sit with someone who is not staff — an external
    # lawyer, an auditor, the bank.
    # Which department the record belongs to (Tlotlo Maswabi; CFO 2026-08-07).
    # Free text on purpose: Admin's own register already uses department names
    # that do not match any table in Omni, and forcing a foreign key would mean
    # rejecting rows they can currently file.
    department      = models.CharField(
        max_length=120, blank=True, default='',
        help_text='The department the record belongs to.')

    current_custodian = models.CharField(max_length=200, blank=True, default='')
    current_location  = models.CharField(max_length=200, blank=True, default='')

    # When the CURRENT issue is expected back. Fable review: the overdue list used
    # to join every historical movement, so a file re-issued after a late return
    # stayed overdue for ever and the badge count with it. Overdue is a property of
    # where the record is NOW, so it belongs on the record.
    due_back_on     = models.DateField(null=True, blank=True)
    # The date the physical file last left the repository. Stored rather than
    # derived from the movement history so the register can show a day count
    # without one extra query per row (Tlotlo Maswabi; CFO 2026-08-07).
    out_since       = models.DateField(null=True, blank=True)
    opened_on       = models.DateField(null=True, blank=True)
    closed_on       = models.DateField(null=True, blank=True)
    # When it may be destroyed. Nothing deletes automatically — this is a due
    # date for a person, not a purge job.
    retention_until = models.DateField(null=True, blank=True)
    # A record under legal hold must not be destroyed whatever its retention date
    # says. Checked before any disposal.
    legal_hold      = models.BooleanField(default=False)
    legal_hold_note = models.CharField(max_length=300, blank=True, default='')

    notes           = models.TextField(blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['reference']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['current_holder']),
            models.Index(fields=['retention_until']),
            models.Index(fields=['due_back_on']),
        ]  # names are Django-derived; see migration 0002
        permissions = [
            # Granted to a NAMED SUBJECT, never by job title (CFO, 11 Aug 2026).
            # The Records officer must see the personnel files she physically
            # holds, but her title is `accountant` — putting `accountant` into
            # RESTRICTED_TITLES would hand every accountant in the group sight of
            # every staff member's personal file, which is not what was approved.
            # A permission grant keeps this narrow, visible in the admin, and
            # revocable without a code change. A group grant is equally valid —
            # what matters is that it is deliberate, not automatic by title.
            ('view_restricted_records',
             'Can see records marked restricted (personal data)'),
            # Opening the register at all. Added 2026-08-25 on Tlotlo Maswabi's
            # request that Goitsemang Ngwako be able to cover for her.
            #
            # The gap this closes: `view_restricted_records` above could be granted
            # to a NAMED person, but there was no equivalent for simply opening the
            # register — that came only from a job title in REGISTER_TITLES. So the
            # only ways to admit a second Records Officer were to change her job
            # title, or to widen a whole title into the register. Both are wrong:
            # Goitsemang's title is `operations`, and titles drive approval rights
            # elsewhere in Omni (payments, POs), so promoting her to `accountant`
            # to read a register would hand her accounting authority she was never
            # given. Widening `operations` would open the register to every
            # operational staff member.
            #
            # So the register now honours the same kind of explicit, named,
            # admin-revocable grant that restricted records already did.
            ('view_records_register',
             'Can open the records register (without holding a register job title)'),
        ]

    def __str__(self):
        return f'{self.reference} — {self.title}'

    @property
    def is_out(self) -> bool:
        return self.status == self.Status.ISSUED

    @property
    def days_out(self) -> int | None:
        """How many days the physical file has been out of the repository.

        None when it is not out. 0 on the day it left. Reads the stored
        out_since, so a list of 500 records costs no extra queries.
        """
        from datetime import date
        if not self.is_out or not self.out_since:
            return None
        return max(0, (timezone.localdate() - self.out_since).days)

    @property
    def may_be_destroyed(self) -> bool:
        """Retention has expired AND it is not frozen under legal hold."""
        from datetime import date
        if self.legal_hold or not self.retention_until:
            return False
        return self.retention_until <= timezone.localdate()


class RecordMovement(AuditableMixin, BaseModel):
    """One leg of the chain of custody. Append-only — never edited in place.

    Mirrors assets.AssetAssignment on purpose: from/to person, from/to custodian,
    from/to location, reason, date, and who recorded it. That model has been in
    production since June and is the shape Admin already understands.
    """

    class Kind(models.TextChoices):
        ISSUE    = 'issue',    'Issued out'
        RETURN   = 'return',   'Returned to store'
        TRANSFER = 'transfer', 'Passed to someone else'
        ARCHIVE  = 'archive',  'Sent to archive'
        DESTROY  = 'destroy',  'Destroyed'

    record          = models.ForeignKey(
        RecordItem, on_delete=models.CASCADE, related_name='movements')
    kind            = models.CharField(max_length=20, choices=Kind.choices)

    from_employee   = models.ForeignKey(
        'payroll.Employee', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='records_given')
    to_employee     = models.ForeignKey(
        'payroll.Employee', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='records_received')
    from_custodian  = models.CharField(max_length=200, blank=True, default='')
    to_custodian    = models.CharField(max_length=200, blank=True, default='')
    from_location   = models.CharField(max_length=200, blank=True, default='')
    to_location     = models.CharField(max_length=200, blank=True, default='')

    reason          = models.CharField(max_length=300, blank=True, default='')
    moved_at        = models.DateField()
    # When it is expected back. The overdue list is built from this.
    due_back_on     = models.DateField(null=True, blank=True)
    recorded_by     = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name='record_movements')

    class Meta(BaseModel.Meta):
        ordering = ['-moved_at', '-created_at']
        indexes = [
            models.Index(fields=['record', '-moved_at']),
            models.Index(fields=['due_back_on']),
        ]

    def __str__(self):
        return f'{self.record_id} {self.kind} on {self.moved_at}'


# Staff file-request + approve/deny trail — lives in its own file, imported here
# so Django registers it (Tshepo Maswabi 2026-08-11).
from .request_models import RecordFileRequest  # noqa: E402,F401
