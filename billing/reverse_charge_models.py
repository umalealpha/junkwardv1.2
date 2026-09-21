"""
billing/reverse_charge_models.py

Reverse-charge VAT capture on imported remote services — Botswana VAT
Amendment Act No.16 of 2025, effective 1 June 2026.

Foreign digital-service suppliers (AWS, Anthropic, Google, OpenAI, GitHub,
Mailgun, Time Doctor, etc.) are non-resident and do not charge Botswana
VAT. Under the reverse charge, the LOCAL recipient self-assesses instead:
  - declares the VAT as OUTPUT tax, as if it were the supplier, and
  - to the extent the input is recoverable, claims the same amount back
    as INPUT tax on the same return.

When input_vat_recoverable is left at its full-recovery default the two
legs are equal and the net cost is zero — reverse charge is cash-flow
neutral. It only becomes a real cost when the underlying spend sits behind
VAT-exempt income (e.g. exempt insurance premium) and the input claim is
restricted below 100%.

Model:
  - ReverseChargeEntry   One self-assessed line per foreign invoice/charge.

This file is a sibling of billing/models.py and is imported at the bottom
of that module (same pattern as billing/payment_terms_models.py) so that:
  (a) Django sees the model on the billing app_label, and
  (b) its migration lands in billing/migrations/.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models

from core.models import AuditableMixin, BaseModel

TWO_PLACES = Decimal('0.01')
ZERO       = Decimal('0.00')


# ---------------------------------------------------------------------------
# ReverseChargeEntry
# ---------------------------------------------------------------------------

class ReverseChargeEntry(AuditableMixin, BaseModel):
    """
    One self-assessed reverse-charge VAT entry for an imported remote
    service.

    output_vat and net_vat_cost are ALWAYS server-computed in save() —
    never trust a client-supplied value for either (the DRF serializer
    also marks them read-only, as defence in depth).

    input_vat_recoverable defaults to full recovery (= output_vat) and
    STAYS pinned to it on every save — unless input_recovery_overridden is
    True, in which case the explicit value sticks instead. Finance lowers
    it (via the serializer, which is the only place that flips the flag —
    see ReverseChargeEntrySerializer.create/update) on entries that sit
    behind VAT-exempt insurance income, where full recovery would
    overstate the recoverable input VAT and is a BURS audit risk.

    C-1 fix (Fable-5 review): the previous design kept "has this ever been
    explicitly set" as a data value (input_vat_recoverable is None ==
    never touched). That materialised full recovery ONCE at create time —
    a later bwp_amount edit recomputed output_vat but left the stale
    input_vat_recoverable behind, so net_vat_cost silently drifted off
    zero. input_recovery_overridden persists that INTENT explicitly
    instead of inferring it from the data, so full recovery is
    re-pinned on every save (edits, future RC_VAT_RATE changes) for as
    long as nobody has explicitly overridden it.

    Reconciliation guarantee: whenever input_recovery_overridden is False,
    input_vat_recoverable is assigned from the exact same Decimal object
    as output_vat (not recomputed independently), so `net_vat_cost =
    output_vat - input_vat_recoverable` is exactly Decimal('0.00') at full
    recovery — not "close to zero", exactly zero, by construction — and
    that holds on every save, not just the first one.
    """

    class Category(models.TextChoices):
        CLOUD        = 'cloud',        'Cloud hosting & compute (e.g. AWS)'
        AI_SAAS      = 'ai_saas',      'AI / SaaS subscriptions (e.g. Anthropic, Google, OpenAI)'
        PRODUCTIVITY = 'productivity', 'Productivity & automation (e.g. Microsoft 365, n8n, Power Automate)'
        DESIGN       = 'design',       'Design & content tools (e.g. Canva, Gamma)'
        DEVTOOLS     = 'devtools',     'Dev tooling (e.g. GitHub, Cursor)'
        HRIS         = 'hris',         'HRIS / monitoring (e.g. Time Doctor)'
        EMAIL_API    = 'email_api',    'Email / messaging API (e.g. Mailgun)'
        OTHER        = 'other',        'Other imported remote service'

    vendor              = models.CharField(
                              max_length=200,
                              help_text='Free-text vendor name, e.g. "Amazon Web Services".',
                          )
    category            = models.CharField(max_length=20, choices=Category.choices)
    invoice_date         = models.DateField()
    foreign_currency    = models.CharField(
                              max_length=3,
                              help_text='ISO 4217 currency code the foreign invoice was billed '
                                        'in, e.g. USD.',
                          )
    foreign_amount       = models.DecimalField(
                              max_digits=18, decimal_places=2,
                              help_text='Amount on the foreign invoice, in foreign_currency.',
                          )
    # Deliberately NOT auto-converted from foreign_amount — the user enters
    # the BWP amount actually paid (bank-statement truth), which is not
    # always foreign_amount * a same-day rate (card FX markups, timing).
    bwp_amount           = models.DecimalField(
                              max_digits=18, decimal_places=2,
                              help_text='The BWP amount actually paid. Entered directly — '
                                        'NOT auto-converted from foreign_amount.',
                          )
    reverse_charge_applies = models.BooleanField(
                              default=True,
                              help_text='True for non-resident/foreign remote services only. '
                                        'Local suppliers (e.g. RealPay) are never reverse-charge.',
                          )

    # ---- Server-computed — see save(). Never accept these from the client.
    output_vat           = models.DecimalField(
                              max_digits=18, decimal_places=2, default=ZERO,
                              help_text='= bwp_amount * RC_VAT_RATE. Computed in save(); read-only.',
                          )
    # Nullable so an instance can exist before the first save() has ever
    # computed a value — but "was this explicitly overridden" is tracked by
    # input_recovery_overridden below, NOT by None-ness (see C-1 in the
    # class docstring). save() re-pins this to output_vat on every save
    # while input_recovery_overridden is False.
    input_vat_recoverable = models.DecimalField(
                              max_digits=18, decimal_places=2, null=True, blank=True,
                              help_text='Defaults to full recovery (= output_vat) and stays '
                                        'pinned to it on every save. Set input_recovery_overridden '
                                        '(via an explicit edit) to lower it for entries behind '
                                        'VAT-exempt income.',
                          )
    # C-1 fix — persists INTENT rather than inferring it from whether
    # input_vat_recoverable is None. Set to True ONLY by
    # ReverseChargeEntrySerializer.create/update when the client actually
    # supplies input_vat_recoverable — never directly by the client (the
    # serializer marks it read-only). See save() below.
    input_recovery_overridden = models.BooleanField(
                              default=False,
                              help_text='True once a caller has explicitly supplied '
                                        'input_vat_recoverable. While False, save() keeps '
                                        'input_vat_recoverable pinned to full recovery '
                                        '(= output_vat) on every save.',
                          )
    net_vat_cost         = models.DecimalField(
                              max_digits=18, decimal_places=2, default=ZERO,
                              help_text='= output_vat - input_vat_recoverable. Computed in save(); read-only.',
                          )

    note                 = models.TextField(
                              blank=True, default='',
                              help_text='Mandatory when category is "Other imported remote service".',
                          )

    # Owning legal entity — mirrors Contact.company / Invoice.company.
    company              = models.ForeignKey(
                              'core.Company', on_delete=models.PROTECT,
                              null=True, blank=True,
                              related_name='reverse_charge_entries',
                              help_text='Owning legal entity. Filters apply per-company in the '
                                        'topbar switcher.',
                          )

    created_by           = models.ForeignKey(
                              User, null=True, blank=True,
                              on_delete=models.SET_NULL,
                              related_name='reverse_charge_entries_created',
                          )
    modified_by          = models.ForeignKey(
                              User, null=True, blank=True,
                              on_delete=models.SET_NULL,
                              related_name='reverse_charge_entries_modified',
                          )

    class Meta(BaseModel.Meta):
        verbose_name        = 'Reverse-Charge VAT Entry'
        verbose_name_plural = 'Reverse-Charge VAT Entries'
        # PERF: the VAT-return builder filters this table by company +
        # invoice_date range on every run — index the pair it actually
        # queries on (reverse_charge_applies is boolean/low-cardinality,
        # not worth a separate index).
        indexes = [
            models.Index(fields=['company', 'invoice_date'],
                         name='rc_entry_company_date_idx'),
        ]

    def __str__(self):
        return f"{self.vendor} — {self.invoice_date} ({self.bwp_amount} BWP)"

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def clean(self):
        super().clean()
        errors = {}

        if self.category == self.Category.OTHER and not (self.note or '').strip():
            errors['note'] = (
                'A note is required when category is "Other imported remote service".'
            )

        # H-1 (Fable-5 review): range validation — over-claims and negative
        # amounts were previously accepted outright. Mirrored in
        # ReverseChargeEntrySerializer.validate() for the API write path
        # (nothing calls full_clean() there); kept here too as defence in
        # depth for admin/ORM use.
        if self.bwp_amount is not None and self.bwp_amount <= ZERO:
            errors['bwp_amount'] = 'BWP amount paid must be greater than zero.'

        if self.foreign_amount is not None and self.foreign_amount <= ZERO:
            errors['foreign_amount'] = 'Foreign amount must be greater than zero.'

        if self.input_vat_recoverable is not None and self.bwp_amount is not None:
            bound = (self.bwp_amount * settings.RC_VAT_RATE).quantize(
                TWO_PLACES, rounding=ROUND_HALF_UP)
            if self.input_vat_recoverable < ZERO:
                errors['input_vat_recoverable'] = 'Input VAT recoverable cannot be negative.'
            elif self.input_vat_recoverable > bound:
                errors['input_vat_recoverable'] = (
                    f'Input VAT recoverable cannot exceed output VAT ({bound}) — '
                    f'that would over-claim input VAT.'
                )

        # M-3 (Fable-5 review): reverse charge only applies from
        # RC_VAT_EFFECTIVE_DATE (VAT Amendment Act No.16 of 2025, effective
        # 1 June 2026) — not retroactively.
        if self.invoice_date and self.invoice_date < settings.RC_VAT_EFFECTIVE_DATE:
            errors['invoice_date'] = (
                f'Reverse-charge VAT applies to invoices dated on or after '
                f'{settings.RC_VAT_EFFECTIVE_DATE} (VAT Amendment Act No.16 of 2025).'
            )

        if errors:
            raise ValidationError(errors)

    # ------------------------------------------------------------------
    # save() — server-side computation, never trust client math
    # ------------------------------------------------------------------

    def save(self, *args, audit_user=None, audit_ip=None, audit_description=None, **kwargs):
        # output_vat is ALWAYS derived from bwp_amount * RC_VAT_RATE — the
        # rate lives in settings.RC_VAT_RATE (never hardcode 0.14 here).
        rate = settings.RC_VAT_RATE
        bwp = self.bwp_amount or ZERO
        self.output_vat = (bwp * rate).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

        # C-1 fix (Fable-5 review): re-pin to full recovery on EVERY save
        # while input_recovery_overridden is False — not just when
        # input_vat_recoverable happens to be None. The old None-check only
        # ever fired once (at create), so a later bwp_amount edit recomputed
        # output_vat above but left this stale, and net_vat_cost silently
        # drifted off zero. Assigning the SAME Decimal instance that
        # output_vat now holds (not a fresh computation) is what makes the
        # net=0 reconciliation exact rather than rounding-dependent. The
        # `or is None` guard is belt-and-braces for a row that somehow has
        # overridden=True but no actual value yet (shouldn't happen via the
        # serializer, which only sets the flag alongside a real value).
        if not self.input_recovery_overridden or self.input_vat_recoverable is None:
            self.input_vat_recoverable = self.output_vat

        self.net_vat_cost = self.output_vat - self.input_vat_recoverable

        # Normalise free-text fields so list/search/grouping doesn't fragment
        # on stray whitespace or mixed-case currency codes.
        if self.vendor:
            self.vendor = self.vendor.strip()
        if self.foreign_currency:
            self.foreign_currency = self.foreign_currency.strip().upper()[:3]

        if audit_user is not None and getattr(audit_user, 'pk', None):
            if not self.created_by_id:
                self.created_by = audit_user
            self.modified_by = audit_user

        super().save(
            *args,
            audit_user=audit_user,
            audit_ip=audit_ip,
            audit_description=audit_description,
            **kwargs,
        )
