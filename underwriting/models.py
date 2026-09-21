"""
underwriting/models.py

UnderwritingDocument — an issued (or draft) underwriting document: a Cover
Note, a financed Cover Note, or a WCA (Worker's Compensation Act) certificate.

The visual template + the four WCA looks live in
`templates/underwriting/tool.html` (ported verbatim from the CFO's reference
tool, 8 Jul 2026). This model records WHAT was issued, to WHOM, by WHOM, and
keeps the rendered PDF for audit — these are binding proof-of-cover documents.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.utils import timezone

from core.models import AuditableMixin, BaseModel


def _next_quote_number() -> str:
    """Q-YYYY-NNNNN, sequential within the year.

    Taken under a row lock on the last quote of the year so two underwriters
    pressing Save at the same moment cannot be handed the same number — a
    duplicate quote number is indistinguishable from a reused file, which is
    the exact problem this module exists to end.
    """
    year = timezone.now().year
    prefix = f'Q-{year}-'
    with transaction.atomic():
        last = (Quote.objects.select_for_update()
                .filter(quote_number__startswith=prefix)
                .order_by('-quote_number').first())
        seq = 1
        if last:
            try:
                seq = int(last.quote_number.rsplit('-', 1)[1]) + 1
            except (IndexError, ValueError):
                seq = Quote.objects.filter(quote_number__startswith=prefix).count() + 1
        return f'{prefix}{seq:05d}'


class UnderwritingDocument(AuditableMixin, BaseModel):

    class DocType(models.TextChoices):
        COVER_NOTE          = 'cn',   'Cover Note'
        COVER_NOTE_FINANCED = 'cnfi', 'Cover Note — financed'
        WCA                 = 'wca',  'WCA Certificate'

    class WcaFormat(models.TextChoices):
        ORIGINAL = 'orig',   'Original'
        COOL     = 'cool',   'Cool'
        ROYAL    = 'royal',  'Royal'
        FORMAL   = 'formal', 'Formal'

    class Status(models.TextChoices):
        DRAFT  = 'draft',  'Draft'
        ISSUED = 'issued', 'Issued'

    doctype   = models.CharField(max_length=8, choices=DocType.choices)
    # Only meaningful for WCA; blank for cover notes.
    fmt       = models.CharField(max_length=10, choices=WcaFormat.choices,
                                 blank=True, default='')
    # The exact field payload used to render (keys per the tool's schema).
    fields    = models.JSONField(default=dict, blank=True)

    # Human-readable anchors surfaced in the register (denormalised from
    # `fields` at issue time so the list view needs no JSON digging).
    policy_number = models.CharField(max_length=60, blank=True, default='')
    insured_name  = models.CharField(max_length=200, blank=True, default='')

    # Optional link to the originating company (entity scope / topbar).
    company   = models.ForeignKey(
        'core.Company', on_delete=models.PROTECT, null=True, blank=True,
        related_name='underwriting_documents',
    )

    status    = models.CharField(max_length=10, choices=Status.choices,
                                 default=Status.DRAFT, db_index=True)

    # Stored rendered PDF (audit copy). Kept in the DB as bytes — these are
    # single-page A4 documents, a few hundred KB each.
    pdf_bytes = models.BinaryField(null=True, blank=True, editable=False)
    pdf_size  = models.PositiveIntegerField(default=0)

    issued_by = models.ForeignKey(
        User, on_delete=models.PROTECT, null=True, blank=True,
        related_name='underwriting_documents_issued',
    )
    issued_at   = models.DateTimeField(null=True, blank=True)
    emailed_to  = models.CharField(max_length=254, blank=True, default='')
    emailed_at  = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['doctype', 'status'], name='uw_doc_dt_status_idx'),
            models.Index(fields=['company', '-created_at'], name='uw_doc_company_created_idx'),
        ]
        verbose_name = 'Underwriting document'
        verbose_name_plural = 'Underwriting documents'

    def __str__(self):
        return f'{self.get_doctype_display()} — {self.insured_name or self.policy_number or self.id}'


class Quote(AuditableMixin, BaseModel):
    """A quotation, produced on ONE standard template (CFO/EXCO 2026-08-08).

    Why this exists: ten real quotes were reviewed and no two matched. Two
    clients shared a file, one was a competitor's schedule reused as our base,
    and VAT appeared three different ways. The loose Excel files stop here.

    The money is not free text. `premium` is the only figure anyone enters;
    `vat` and `total` are derived from the single rule in quote_parse.price()
    and recomputed on every save, so no quote can leave with VAT worked out a
    different way. See `recalc()`.
    """

    class Status(models.TextChoices):
        DRAFT   = 'draft',   'Draft'
        ISSUED  = 'issued',  'Issued'
        LAPSED  = 'lapsed',  'Lapsed'
        WON     = 'won',     'Converted to policy'
        LOST    = 'lost',    'Lost'

    quote_number = models.CharField(max_length=20, unique=True, editable=False, db_index=True)
    version      = models.PositiveSmallIntegerField(default=1)

    client_name  = models.CharField(max_length=200)
    client_attn  = models.CharField(max_length=200, blank=True, default='')
    class_of_business = models.CharField(max_length=80, blank=True, default='')
    period       = models.CharField(max_length=40, blank=True, default='12 months')
    broker       = models.CharField(max_length=120, blank=True, default='')
    # The producing AGENT — the person or agency that brought the business. Shown
    # on the quote NEXT TO the Alpha Direct underwriter who priced it, so both are
    # accountable (CFO 2026-08-12, Motlatsi item 6). Distinct from `broker` (the
    # firm) and from `underwriter` (our staffer / the quote's creator).
    agent        = models.CharField(max_length=160, blank=True, default='')
    agent_email  = models.EmailField(blank=True, default='')

    # [{group, name, note, sum_insured, basis, excess}] — the cover table. One
    # shape serves single-risk, group and motor-fleet; a fleet just has more rows.
    sections     = models.JSONField(default=list, blank=True)

    # CFO 2026-08-10: a premium can be RATED instead of typed. When rate_pct > 0
    # the premium is DERIVED from the total sum insured (rate_pct% of it), never
    # typed. rate_incl_vat=True (the usual case) means the rate already carries
    # VAT, so SI x rate is the GROSS (total) and the net premium is backed out of
    # it; False means SI x rate is the net premium and VAT is added on top. Same
    # one money rule as price() — computed in quote_parse, never by AI, never by
    # hand. rate_pct is stored as a percentage, e.g. 3.0000 = 3%.
    rate_pct      = models.DecimalField(max_digits=8, decimal_places=4, default=0)
    rate_incl_vat = models.BooleanField(default=True)

    # A part-year policy: 12 = a normal annual quote, 6 = six months' cover. The
    # rated premium is annual; recalc() charges months/12 of it (CFO 2026-08-11).
    # BOUNDED 1..12: an out-of-range value used to fall through to "no pro-rata"
    # and quietly charge a full year, which hides the bad data instead of showing
    # it (panel review 2026-08-11).
    period_months = models.PositiveSmallIntegerField(
        default=12,
        validators=[MinValueValidator(1), MaxValueValidator(12)],
        help_text='Months of cover, 1-12. 12 is a normal annual quotation.')

    # The ANNUAL net, before any part-year pro-rata. Persisted because recalc()
    # runs on EVERY save: without its own home, a typed-premium part-year quote
    # was pro-rated again each time (halved, then quartered — an issued 6-month
    # quote charged 25% of the year). `premium` is always DERIVED from this, so
    # recalc is idempotent (Fable review 2026-08-11).
    annual_premium = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    premium = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    vat     = models.DecimalField(max_digits=14, decimal_places=2, default=0, editable=False)
    total   = models.DecimalField(max_digits=14, decimal_places=2, default=0, editable=False)

    # CFO 2026-08-08: Aria may SUGGEST a premium when none was typed, marked as a
    # guess. It must never reach a client unconfirmed, so `issue()` refuses while
    # this is True — the underwriter has to look at the number and accept it.
    premium_is_suggested = models.BooleanField(default=False)

    # What is NOT covered — the underwriter's class-specific list, printed under
    # a locked pointer to the policy wording (CFO 2026-08-10). Data, never
    # invented by the document: an exclusion is contractual.
    exclusions   = models.JSONField(default=list, blank=True)

    # Free-text notes typed by the underwriter — e.g. the Motor Excess Conditions
    # block (Gomolemo Sebudula, 14 Aug 2026). Printed line-for-line on the quote;
    # the document invents nothing, so an empty value prints nothing. Kept plain
    # text (never HTML) — it is rendered with white-space:pre-line and escaped.
    notes        = models.TextField(blank=True, default='')

    valid_until  = models.DateField(null=True, blank=True)
    status       = models.CharField(max_length=10, choices=Status.choices,
                                    default=Status.DRAFT, db_index=True)

    # What the underwriter typed into the box, kept for QC: it shows what Aria
    # was given when a quote later looks wrong.
    source_text  = models.TextField(blank=True, default='')
    drafted_by_ai = models.BooleanField(default=False)

    company = models.ForeignKey(
        'core.Company', on_delete=models.PROTECT, null=True, blank=True,
        related_name='quotes',
    )
    underwriter = models.ForeignKey(
        User, on_delete=models.PROTECT, null=True, blank=True,
        related_name='quotes_underwritten',
    )

    pdf_bytes = models.BinaryField(null=True, blank=True, editable=False)
    pdf_size  = models.PositiveIntegerField(default=0)

    issued_by = models.ForeignKey(
        User, on_delete=models.PROTECT, null=True, blank=True,
        related_name='quotes_issued',
    )
    issued_at = models.DateTimeField(null=True, blank=True)

    # Conversion tracking — the register's reason for existing.
    converted_policy_number = models.CharField(max_length=60, blank=True, default='')
    outcome_at = models.DateTimeField(null=True, blank=True)

    # Sent to the client from Omni (phone flow, CFO 2026-09-04) — who and when,
    # the same stamp the certificates carry. Blank = never emailed from here.
    emailed_to = models.EmailField(blank=True, default='')
    emailed_at = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', '-created_at'], name='uw_quote_status_idx'),
            models.Index(fields=['company', '-created_at'], name='uw_quote_company_idx'),
        ]
        verbose_name = 'Quotation'
        verbose_name_plural = 'Quotations'

    def __str__(self):
        return f'{self.quote_number} — {self.client_name}'

    # ── the locked money rule ────────────────────────────────────────────────
    def recalc(self):
        """VAT and total are ALWAYS derived. Never set them from input.

        When a rate is set (rate_pct > 0) the PREMIUM is derived too — rate_pct%
        of the total sum insured — so the underwriter never types the money. The
        arithmetic stays in quote_parse, the one place the CFO signed off.

        When rows are priced individually (a row `premium` or `rate`), the stored
        premium is the SUM OF THE PER-ROW NET FIGURES the client sees printed —
        same function the PDF uses — so the breakdown on the page always adds up
        to the total on the record, to the thebe.
        """
        from .quote_parse import (price, price_from_rate, total_sum_insured,
                                   premium_from_section_rates, prorate,
                                   _money_from_figure)
        # Precedence: a rate on each cover row (per-product) wins; else a single
        # quote-level rate on the total sum insured; else the typed premium.
        # 1. The ANNUAL net, from whichever source prices this quote.
        m = premium_from_section_rates(self.sections, self.rate_incl_vat)
        if m is None and self.rate_pct and self.rate_pct > 0:
            m = price_from_rate(total_sum_insured(self.sections),
                                self.rate_pct, self.rate_incl_vat)
        if m is not None:
            annual = m['premium']
        elif self.annual_premium and self.annual_premium > 0:
            # Already banked. Re-derive from the ANNUAL, not from `premium`,
            # which may already carry a pro-rata cut — EXCEPT when someone has
            # just edited `premium` by hand, which must become the new annual.
            # Told apart by asking what `premium` SHOULD be if nothing changed:
            # if it differs, a person typed over it. Without this, editing the
            # premium on a typed quote snapped straight back to the old figure.
            banked = prorate(self.annual_premium, self.period_months)
            expected = banked['charged'] if banked else self.annual_premium
            typed = price(self.premium)['premium']
            annual = self.annual_premium if typed == expected else typed
        else:
            annual = price(self.premium)['premium']      # first save of a typed premium
        self.annual_premium = annual

        # 2. Part-year cover charges months/12 of that annual figure, and VAT is
        # taken on the charged amount by the same one rule. Deriving `premium`
        # from `annual_premium` every time makes this safe to run repeatedly.
        pr = prorate(annual, self.period_months)
        self.premium = pr['charged'] if pr else annual
        # For incl_vat section-rated quotes the total the client pays is the
        # SUM of the SI x rate figures — forward-VATing the backed-out net
        # drifts by a thebe on figures that do not divide by 1.14 cleanly
        # (Gomolemo, ADIC 2026-08-19: 260,000 x 3.2%% = 8,320.00 on screen,
        # 8,320.01 on the PDF). Keep the gross when we have it; back out net
        # and VAT once from it, so the printed total matches the tile exactly.
        if m is not None and self.rate_incl_vat and m.get('total') and not pr:
            money = _money_from_figure(m['total'], incl_vat=True)
        elif m is not None and self.rate_incl_vat and m.get('total') and pr:
            # Prorated incl-VAT: scale the GROSS by months/12, back out net once.
            from decimal import Decimal, ROUND_HALF_UP
            months = pr['months']
            charged_total = (m['total'] * Decimal(months) / Decimal('12')).quantize(
                Decimal('0.01'), rounding=ROUND_HALF_UP)
            money = _money_from_figure(charged_total, incl_vat=True)
            self.premium = money['premium']
        else:
            money = price(self.premium)
        self.vat = money['vat']
        self.total = money['total']

    def save(self, *args, **kwargs):
        if not self.valid_until:
            self.valid_until = (timezone.now() + timedelta(days=30)).date()
        self.recalc()

        if self.quote_number:
            return super().save(*args, **kwargs)

        # Number and INSERT in ONE transaction, and retry if two underwriters
        # collided anyway. Taking the number in its own committed block (as this
        # first did) leaves a window where both get the same one and the second
        # save fails — an underwriter losing a quote to a crash is not acceptable,
        # and `unique` is the only real guarantee.
        from django.db import IntegrityError
        for attempt in range(5):
            try:
                with transaction.atomic():
                    self.quote_number = _next_quote_number()
                    return super().save(*args, **kwargs)
            except IntegrityError:
                self.quote_number = ''
                if attempt == 4:
                    raise

    def issue(self, user):
        """Draft → issued: stamp it, freeze a PDF, put it in the register.

        Refuses on the two things that must never reach a broker: no client, and
        a premium Aria guessed that nobody has confirmed.
        """
        if self.status != self.Status.DRAFT:
            raise ValidationError(f'{self.quote_number} is already {self.get_status_display()}.')
        if not (self.client_name or '').strip():
            raise ValidationError('A quotation cannot be issued without a client name.')
        if self.premium_is_suggested:
            raise ValidationError(
                'The premium was suggested by Aria and has not been confirmed. '
                'Check the figure and confirm it before issuing.')
        if not self.premium or self.premium <= 0:
            raise ValidationError('A quotation cannot be issued with no premium.')
        # Per-product rating: if some rows are rated, a row with a sum insured but
        # no rate was left OUT of the premium — that product would be free. Refuse
        # rather than send a broker a quote that under-charges (review 2026-08-10).
        from .quote_parse import unrated_covered_rows
        missing = unrated_covered_rows(self.sections)
        if missing:
            raise ValidationError(
                'These cover rows have a sum insured but no rate, so they are not '
                'in the premium: ' + ', '.join(missing[:4]) +
                '. Give them a rate, or clear their sum insured.')
        # Under-pricing guard: no rate may fall below the floor set for the class.
        floor, below = quote_rates_below_floor(self)
        if below:
            raise ValidationError(
                f'The minimum premium rate for this class is {floor}%. Below it: '
                + ', '.join(below[:4]) + '. Raise the rate, or change the floor.')
        minimum, under = quote_below_min_premium(self)
        if under is not None:
            term = ('' if int(self.period_months or 12) == 12
                    else f' for {self.period_months} months')
            raise ValidationError(
                f'The premium charged{term} is {under:,.2f}, below the {minimum:,.2f} '
                'minimum for this class. Raise the premium, shorten nothing further, '
                'or change the minimum.')
        # `or 12` would swallow a ZERO — 0 is falsy, so a zero-month quote read
        # as a normal year and issued (caught by the guard's own test).
        months = 12 if self.period_months is None else int(self.period_months)
        if not (1 <= months <= 12):
            raise ValidationError(
                f'The period of cover is {months} months. It must be '
                'between 1 and 12 — a longer term is a separate quotation.')
        if not self.company_id:
            raise ValidationError(
                'This quotation is not attached to a company. Pick the entity at the '
                'top of the screen and save again before issuing.')

        self.status = self.Status.ISSUED
        self.issued_by = user
        self.issued_at = timezone.now()
        self.save()
        return self


class QuoteRateFloor(models.Model):
    """The lowest premium rate % a quotation may use — the under-pricing guard.

    One 'default' row (blank class_of_business) sets the floor for every quote;
    add a row with a class_of_business to override the floor for that class
    (exact, case-insensitive match on the quote's class). Set by underwriting
    management; enforced live on the quote screen and, hard, at issue(). A floor
    of 0 — or no active row — means no check, so the feature is safe until a
    floor is actually set.
    """
    class_of_business = models.CharField(
        max_length=80, blank=True, default='',
        help_text="Blank = the default floor that applies to every class.")
    min_rate_pct = models.DecimalField(
        max_digits=8, decimal_places=4, default=0,
        help_text="Lowest allowed premium rate %, e.g. 0.25 means 0.25%.")
    # The rate floor guards the RATE; this guards the MONEY. Straight pro-rata
    # means a one-month quote is a twelfth of the year, which can be uneconomic
    # however correct the rate is (CFO 2026-08-11). Checked against the amount
    # actually CHARGED, so it catches short-period quotes. 0 = no minimum.
    min_premium = models.DecimalField(
        max_digits=14, decimal_places=2, default=0,
        help_text="Lowest premium we will issue for this class, BWP. 0 = no minimum.")
    active = models.BooleanField(default=True)
    note = models.CharField(max_length=200, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Quote rate floor'
        verbose_name_plural = 'Quote rate floors'

    def __str__(self):
        return f'{self.class_of_business or "(default)"} >= {self.min_rate_pct}%'

    @classmethod
    def resolve_row(cls, class_of_business):
        """The floor ROW for a class — exact active class match first, else the
        active default row, else None. resolve() returns only the RATE, so a
        caller wanting the minimum PREMIUM must use this; reading .min_premium
        off resolve()'s Decimal silently gave 0 and the guard never fired."""
        c = (class_of_business or '').strip().lower()
        rows = list(cls.objects.filter(active=True))
        if c:
            for r in rows:
                v = (r.class_of_business or '').strip()
                if v and v.lower() == c:
                    return r
        for r in rows:
            if not (r.class_of_business or '').strip():
                return r
        return None

    @classmethod
    def resolve(cls, class_of_business) -> Decimal:
        """Floor % for a class: an exact active class match wins, else the active
        default row, else 0 (no floor)."""
        c = (class_of_business or '').strip().lower()
        rows = list(cls.objects.filter(active=True))
        if c:
            for r in rows:
                if (r.class_of_business or '').strip() and (r.class_of_business or '').strip().lower() == c:
                    return r.min_rate_pct or Decimal('0')
        for r in rows:
            if not (r.class_of_business or '').strip():
                return r.min_rate_pct or Decimal('0')
        return Decimal('0')


def quote_below_min_premium(quote):
    """Is the premium actually CHARGED under the minimum for this class?

    Returns (minimum, charged) when it is, else (minimum, None). Checked on the
    charged figure, not the annual, so a short-period quote priced at a correct
    rate is still caught when the money is too small to be worth writing.
    """
    row = QuoteRateFloor.resolve_row(getattr(quote, 'class_of_business', ''))
    minimum = (row.min_premium if row is not None else None) or Decimal('0')
    if minimum <= 0:
        return minimum, None
    charged = quote.premium or Decimal('0')
    return minimum, (charged if charged < minimum else None)


def quote_rates_below_floor(quote):
    """Rates in a quote that fall below the floor for its class.

    Returns (floor, [labels]); an empty list means OK (or no floor set). Checks
    the whole-quote rate and every per-row rate, so a single under-priced product
    in a multi-product quote is caught too.
    """
    from .quote_parse import _to_decimal
    floor = QuoteRateFloor.resolve(getattr(quote, 'class_of_business', ''))
    if not floor or floor <= 0:
        return floor, []
    bad = []
    q = _to_decimal(getattr(quote, 'rate_pct', None))
    if q and q > 0 and q < floor:
        bad.append(f'whole-quote {q}%')
    for s in (quote.sections or []):
        if isinstance(s, dict):
            r = _to_decimal(s.get('rate'))
            if r and r > 0 and r < floor:
                bad.append(f"{(s.get('name') or 'a cover row')} {r}%")
    return floor, bad


class QuoteTemplate(models.Model):
    """A reusable starting point for a quotation — the standard cover rows for a
    class of business, so the underwriter never faces a blank page. Picking a
    template fills the class, the cover table and a default rate; every field
    stays editable afterwards. Set by underwriting management in admin.
    """
    name = models.CharField(max_length=120)
    class_of_business = models.CharField(max_length=80, blank=True, default='')
    # Same row shape as Quote.sections: [{group,name,note,sum_insured,basis,excess,rate}].
    sections = models.JSONField(default=list, blank=True)
    rate_pct = models.DecimalField(max_digits=8, decimal_places=4, default=0)
    rate_incl_vat = models.BooleanField(default=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Quote template'
        verbose_name_plural = 'Quote templates'

    def __str__(self):
        return self.name


class GraphiteMapping(models.Model):
    """Translation table for the Omni → Graphite conversion (policy on "won").

    Graphite creates a policy from its OWN identifiers, not our words: our class of
    business has to become a Graphite product_id + plan_id, and our broker name has
    to become a Graphite agent code (Pramod Bisen, 11-Aug-2026). Holding the
    mapping in a table means the codes are set by underwriting management when
    TheRiskCo issues them — not hardcoded in a deploy.

    Rows are looked up case-insensitively on `omni_value`. An unmapped value is a
    hard stop at conversion time: better to refuse than to create a policy on the
    wrong product.
    """

    class Kind(models.TextChoices):
        CLASS_OF_BUSINESS = 'class',  'Class of business → product / plan'
        BROKER            = 'broker', 'Broker → agent code'

    kind       = models.CharField(max_length=10, choices=Kind.choices, db_index=True)
    omni_value = models.CharField(max_length=120)
    # Graphite side. product/plan for a class; agent_code for a broker.
    product_id = models.PositiveIntegerField(null=True, blank=True)
    plan_id    = models.PositiveIntegerField(null=True, blank=True)
    agent_code = models.CharField(max_length=60, blank=True, default='')
    active     = models.BooleanField(default=True)
    note       = models.CharField(max_length=200, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Graphite mapping'
        verbose_name_plural = 'Graphite mappings'
        constraints = [
            models.UniqueConstraint(fields=['kind', 'omni_value'],
                                    name='uniq_graphite_mapping_kind_value'),
        ]

    def __str__(self):
        target = self.agent_code or f'product {self.product_id} / plan {self.plan_id}'
        return f'{self.get_kind_display()}: {self.omni_value} → {target}'

    @classmethod
    def resolve(cls, kind, omni_value):
        """The mapping row for a value, or None. Case-insensitive, active only."""
        v = (omni_value or '').strip()
        if not v:
            return None
        return cls.objects.filter(kind=kind, active=True, omni_value__iexact=v).first()
