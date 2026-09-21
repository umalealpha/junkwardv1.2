"""
ifrs17/models.py — the IFRS 17 Exception Register.

CFO directive 2026-08-26: Finance must record, against every MATERIAL exception
in the Empirica valuation, a written explanation of what it is and what is being
done about it. Small variances are disclosed and left alone.

Shape deliberately copied from two things that already work:
  * `supplier_recon` — the "no position without a written justification, and a
    minimum length so it cannot be a tick-box" gate.
  * `nbfira` — maker (Finance answers) then reviewer (CFO/FC/FM accepts or
    returns), with who and when on every step.

This app holds NO ledger figures and posts nothing. The valuation stays exactly
as Empirica signed it; this register only records what the humans say about the
exceptions in it.
"""
from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from core.models import AuditableMixin, BaseModel

# Long enough that "noted" / "agreed" / "N/A" cannot pass. Same intent as
# supplier_recon.MIN_JUSTIFICATION_CHARS (20), raised for this register because
# these answers go in front of the auditors.
MIN_EXPLANATION_CHARS = 80


class IFRS17Exception(AuditableMixin, BaseModel):
    """One disclosed exception in the IFRS 17 valuation, and Finance's answer.

    Rows are SEEDED from `constants.KNOWN_VARIANCES` — nobody types the
    exception itself, because the exception is the actuary's disclosure, not an
    opinion. What humans add is `explanation`, `action`, `owner` and the review.
    """

    class Band(models.TextChoices):
        MATERIAL   = 'material',   'Material — explanation required'
        WATCH      = 'watch',      'Watch — monitored, no explanation required'
        IMMATERIAL = 'immaterial', 'Immaterial — disclosed, no response required'

    class Status(models.TextChoices):
        OPEN     = 'open',     'Open — awaiting Finance'
        ANSWERED = 'answered', 'Answered — awaiting review'
        ACCEPTED = 'accepted', 'Accepted'
        RETURNED = 'returned', 'Returned to Finance'
        NO_RESPONSE_REQUIRED = 'no_response_required', 'Below materiality — no response required'

    # --- identity (seeded, read-only to users) --------------------------- #
    ref            = models.CharField(max_length=16)
    financial_year = models.CharField(max_length=8, default='FY2026')
    title          = models.CharField(max_length=200)
    detail         = models.TextField()
    source         = models.CharField(max_length=80, blank=True, default='',
                                     help_text="Where in the actuary's report this sits.")
    amount         = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    severity       = models.CharField(max_length=8, blank=True, default='')

    # --- materiality (derived by ifrs17.materiality, stored for the audit
    #     trail so a later threshold change cannot silently rewrite history) - #
    band                 = models.CharField(max_length=12, choices=Band.choices)
    materiality_basis    = models.CharField(max_length=12, blank=True, default='',
                                            help_text='quantitative or qualitative')
    materiality_reason   = models.TextField(blank=True, default='')
    threshold_at_seed    = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    # --- the human part -------------------------------------------------- #
    status      = models.CharField(max_length=24, choices=Status.choices,
                                   default=Status.OPEN)
    explanation = models.TextField(
                      blank=True, default='',
                      help_text='What this exception is, and why the position taken is right. '
                                'Mandatory for material exceptions.')
    action      = models.TextField(
                      blank=True, default='',
                      help_text='What is being done about it, and by when.')
    owner       = models.ForeignKey(
                      settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                      null=True, blank=True, related_name='owned_ifrs17_exceptions',
                      help_text='The person in Finance accountable for this answer.')

    answered_by = models.ForeignKey(
                      settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                      null=True, blank=True, related_name='answered_ifrs17_exceptions')
    answered_at = models.DateTimeField(null=True, blank=True)

    reviewed_by = models.ForeignKey(
                      settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                      null=True, blank=True, related_name='reviewed_ifrs17_exceptions')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.TextField(blank=True, default='',
                                   help_text='Why it was accepted, or what is missing.')

    class Meta(BaseModel.Meta):
        constraints = [
            models.UniqueConstraint(fields=['ref', 'financial_year'],
                                    name='uniq_ifrs17_exception_ref_year'),
        ]
        indexes = [
            models.Index(fields=['financial_year', 'band', 'status']),
        ]

    def __str__(self) -> str:
        return f'{self.ref} {self.financial_year} — {self.title[:60]}'

    # --- rules ------------------------------------------------------------ #
    @property
    def explanation_required(self) -> bool:
        """Only the material band creates an obligation. That is the whole point
        of the CFO's "work on the materiality" instruction."""
        return self.band == self.Band.MATERIAL

    @property
    def is_outstanding(self) -> bool:
        """Still needs Finance to do something."""
        return (self.explanation_required
                and self.status in (self.Status.OPEN, self.Status.RETURNED))

    def clean(self):
        super().clean()
        # A material exception that claims to be answered must carry a real
        # explanation. Gate is keyed off STATUS, not off a display property —
        # supplier_recon shipped that exact bug (a board-display concept was
        # used as the integrity rule, and the gate silently did nothing).
        if self.status not in (self.Status.ANSWERED, self.Status.ACCEPTED):
            return
        if not self.explanation_required:
            return
        text = (self.explanation or '').strip()
        if not text:
            raise ValidationError({
                'explanation': 'A written explanation is required for a material '
                               'IFRS 17 exception.',
            })
        if len(text) < MIN_EXPLANATION_CHARS:
            raise ValidationError({
                'explanation': f'The explanation must be at least '
                               f'{MIN_EXPLANATION_CHARS} characters — explain the '
                               f'position for the auditors, do not just tick a box.',
            })
