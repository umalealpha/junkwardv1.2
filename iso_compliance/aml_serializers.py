"""
iso_compliance/aml_serializers.py — the AML registers, over the wire.

Written 2026-09-16. The models landed on 2026-09-09 but nothing was ever built
on top of them: no route, no page. Django admin was the only way in, and the
AML officer is not a staff user, so in practice NOBODY but a superuser could
record a screening, a training record or a board pack. Every objective counting
those registers therefore read zero regardless of the work actually done.

These serializers exist so the officer can record the work from the app.
"""
from __future__ import annotations

from rest_framework import serializers

from iso_compliance.aml_models import (
    AMLTrainingRecord,
    ComplianceReport,
    SanctionsScreening,
)
from procurement.kyc_models import VendorKYC


class SanctionsScreeningSerializer(serializers.ModelSerializer):
    subject_type_label = serializers.CharField(source='get_subject_type_display', read_only=True)
    result_label = serializers.CharField(source='get_result_display', read_only=True)
    pep_label = serializers.CharField(source='get_pep_status_display', read_only=True)
    screened_by_name = serializers.SerializerMethodField()
    needs_fia_report = serializers.BooleanField(read_only=True)

    class Meta:
        model = SanctionsScreening
        fields = (
            'id', 'subject_type', 'subject_type_label', 'subject_name', 'subject_ref',
            'list_source', 'list_version', 'result', 'result_label',
            'pep_status', 'pep_label', 'screened_at', 'screened_by', 'screened_by_name',
            'reported_to_fia_at', 'needs_fia_report', 'notes', 'created_at',
        )
        read_only_fields = ('id', 'created_at', 'screened_by', 'needs_fia_report')

    def get_screened_by_name(self, obj):
        u = obj.screened_by
        if not u:
            return ''
        return (u.get_full_name() or u.username or '').strip()

    def validate_list_version(self, value):
        """A search nobody can repeat is not evidence.

        The list moves; without the version searched there is no way to show a
        regulator what was actually checked, so this is required rather than
        the blank-by-default the model allows for legacy rows.
        """
        if not (value or '').strip():
            raise serializers.ValidationError(
                'Record which version or date of the list you searched — '
                'without it the screening cannot be repeated or evidenced.')
        return value.strip()


class AMLTrainingRecordSerializer(serializers.ModelSerializer):
    employee_name = serializers.SerializerMethodField()
    valid_until = serializers.DateField(read_only=True)
    is_current = serializers.SerializerMethodField()

    class Meta:
        model = AMLTrainingRecord
        fields = (
            'id', 'employee', 'employee_name', 'course', 'completed_on',
            'score', 'evidence', 'valid_until', 'is_current',
            'recorded_by', 'created_at',
        )
        read_only_fields = ('id', 'created_at', 'recorded_by', 'valid_until')

    def get_employee_name(self, obj):
        return str(obj.employee) if obj.employee_id else ''

    def get_is_current(self, obj):
        from django.utils import timezone
        return obj.valid_until >= timezone.localdate()


class ComplianceReportSerializer(serializers.ModelSerializer):
    kind_label = serializers.CharField(source='get_kind_display', read_only=True)
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    due_on = serializers.DateField(read_only=True)
    is_discharged = serializers.BooleanField(read_only=True)

    class Meta:
        model = ComplianceReport
        fields = (
            'id', 'kind', 'kind_label', 'period_year', 'period_quarter',
            'title', 'summary', 'document', 'status', 'status_label',
            'sent_at', 'recipients', 'prepared_by',
            'due_on', 'is_discharged', 'created_at',
        )
        read_only_fields = ('id', 'created_at', 'prepared_by',
                            'due_on', 'is_discharged')

    def validate_period_quarter(self, value):
        if value not in (1, 2, 3, 4):
            raise serializers.ValidationError('Quarter must be 1, 2, 3 or 4.')
        return value

    def validate(self, attrs):
        """Do not let a report be marked filed with nothing attached.

        `is_discharged` already refuses to count such a row, so allowing the
        status to be set would only produce a screen that says 'sent' next to a
        counter that says outstanding. Refuse it at the door instead.
        """
        # A filed report can never move between the two registers. The kind
        # split exists so the AML pack cannot discharge the DPO's duty; letting
        # an existing row change kind would reopen that hole from the other
        # side, and quietly - the row would still look filed, just against the
        # wrong officer. The screen guards this too, but a UI guard is only a
        # guard for people using the UI.
        if self.instance and 'kind' in attrs and attrs['kind'] != self.instance.kind:
            raise serializers.ValidationError({
                'kind': 'A report cannot be moved between the AML and '
                        'data-protection registers.'})
        status = attrs.get('status', getattr(self.instance, 'status', None))
        document = attrs.get('document', getattr(self.instance, 'document', None))
        if status in (ComplianceReport.Status.SENT, ComplianceReport.Status.NOTED) and not document:
            raise serializers.ValidationError({
                'document': 'Attach the report before marking it sent. '
                            'A report with no document is not a report.'})
        return attrs


class VendorKYCScreeningSerializer(serializers.ModelSerializer):
    """Supplier screening only — the document side of VendorKYC is untouched."""

    contact_name = serializers.SerializerMethodField()
    sanctions_label = serializers.CharField(source='get_sanctions_status_display', read_only=True)
    pep_label = serializers.CharField(source='get_pep_status_display', read_only=True)

    class Meta:
        model = VendorKYC
        fields = (
            'id', 'contact', 'contact_name',
            'sanctions_status', 'sanctions_label', 'sanctions_checked_at',
            'pep_status', 'pep_label', 'kyc_expires_on', 'notes', 'updated_at',
        )
        # `contact` is READ-ONLY on purpose. Left writable, a single PATCH
        # re-points one supplier's whole KYC file — its TIN, certificate of
        # incorporation, bank letter, beneficial owners and screening verdict —
        # onto a different supplier, silently. The record would still look
        # complete; it would just describe the wrong company. Which supplier a
        # KYC record belongs to is fixed when `screen` creates it.
        read_only_fields = ('id', 'updated_at', 'contact')

    def get_contact_name(self, obj):
        return str(obj.contact) if obj.contact_id else ''
