"""underwriting/serializers.py"""
from rest_framework import serializers

from .models import Quote, UnderwritingDocument


class UnderwritingDocumentSerializer(serializers.ModelSerializer):
    doctype_label = serializers.CharField(source='get_doctype_display', read_only=True)
    fmt_label     = serializers.CharField(source='get_fmt_display', read_only=True)
    company_code  = serializers.CharField(source='company.code', read_only=True, default=None)
    issued_by_name = serializers.CharField(
        source='issued_by.get_full_name', read_only=True, default=None)

    class Meta:
        model  = UnderwritingDocument
        fields = ['id', 'doctype', 'doctype_label', 'fmt', 'fmt_label',
                  'policy_number', 'insured_name', 'company', 'company_code',
                  'status', 'pdf_size',
                  'issued_by', 'issued_by_name', 'issued_at',
                  'emailed_to', 'emailed_at', 'created_at']
        read_only_fields = fields  # register is read-only; issue via the action


class QuoteSerializer(serializers.ModelSerializer):
    # What the underwriter typed into the ask box. Write-only: it is kept for QC
    # (it shows what Aria was given when a quote later looks wrong) but there is
    # no reason to hand it back on every register row.
    source_text = serializers.CharField(required=False, allow_blank=True, write_only=True)

    # What is NOT covered. A bare JSONField accepts anything, and a plain STRING
    # would make the template iterate its characters — per-character bullets under
    # "What is not covered" on a contractual document (Fable review 2026-08-10).
    # Pin the shape: a list of lines.
    # 1..12; anything else is bad data, not "a full year" (panel 2026-08-11).
    period_months = serializers.IntegerField(min_value=1, max_value=12, required=False)

    exclusions = serializers.ListField(
        child=serializers.CharField(allow_blank=False, max_length=300),
        required=False)

    """The register row + the full quotation. VAT and total are read-only
    everywhere: they are derived on save, never accepted from a client."""

    status_label   = serializers.CharField(source='get_status_display', read_only=True)
    company_code   = serializers.CharField(source='company.code', read_only=True, default=None)
    # get_full_name() returns '' for anyone without a first/last name on their
    # record, so the register's UNDERWRITER column came back blank on a quotation
    # that plainly had one. Fall back to the username: a login name says who it
    # was, an empty cell says nobody did it.
    underwriter_name = serializers.SerializerMethodField()
    issued_by_name = serializers.SerializerMethodField()

    def _person(self, user):
        if not user:
            return None
        return user.get_full_name() or user.get_username()

    def get_underwriter_name(self, obj):
        return self._person(obj.underwriter)

    def get_issued_by_name(self, obj):
        return self._person(obj.issued_by)

    class Meta:
        model  = Quote
        fields = ['id', 'quote_number', 'version',
                  'client_name', 'client_attn', 'class_of_business', 'period', 'broker',
                  'agent', 'agent_email',
                  'sections',
                  'rate_pct', 'rate_incl_vat', 'exclusions', 'notes', 'period_months',
                  'annual_premium', 'premium', 'vat', 'total', 'premium_is_suggested',
                  'valid_until', 'status', 'status_label',
                  'company', 'company_code',
                  'underwriter', 'underwriter_name',
                  'issued_by', 'issued_by_name', 'issued_at',
                  'converted_policy_number', 'outcome_at',
                  'emailed_to', 'emailed_at',
                  'drafted_by_ai', 'source_text', 'pdf_size', 'created_at']
        # Everything that carries a control is server-owned. DeepSeek review
        # 2026-08-08 found that leaving these writable let an ordinary PATCH walk
        # straight through the guards: setting status='issued' skipped issue()
        # entirely (no number stamp, no PDF, no audit), and clearing
        # premium_is_suggested released a premium nobody had confirmed — which is
        # the one thing the CFO asked to be impossible.
        #   status                → only issue() / outcome() move it
        #   premium_is_suggested  → only confirm-premium clears it
        #   company               → set from the caller, never chosen
        #   drafted_by_ai         → set by the ask box, not claimed by the client
        read_only_fields = ['id', 'quote_number', 'vat', 'total', 'annual_premium',
                            'status', 'premium_is_suggested', 'company',
                            'drafted_by_ai', 'underwriter',
                            'issued_by', 'issued_at', 'pdf_size', 'created_at',
                            'status_label', 'company_code', 'underwriter_name',
                            'issued_by_name', 'emailed_to', 'emailed_at']
