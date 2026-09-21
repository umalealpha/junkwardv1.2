"""core/serializers.py"""
from django.contrib.auth.models import User
from django.db import transaction
from rest_framework import serializers

from .models import (AuditLog, Company, Currency, ExchangeRate,
                     NamedModuleAccess, TaxRate, UserProfile)


class AuditLogSerializer(serializers.ModelSerializer):
    user_username = serializers.CharField(source='user.username', read_only=True, default=None)
    action_display = serializers.CharField(source='get_action_display', read_only=True)

    class Meta:
        model = AuditLog
        fields = ['id', 'table_name', 'record_id', 'action', 'action_display',
                  'old_values', 'new_values', 'user_username', 'ip_address',
                  'description', 'created_at']
        read_only_fields = fields


class CurrencySerializer(serializers.ModelSerializer):
    class Meta:
        model = Currency
        fields = ['code', 'name', 'symbol', 'decimal_places', 'is_active']


class CompanySerializer(serializers.ModelSerializer):
    base_currency_name = serializers.CharField(source='base_currency.name', read_only=True)

    class Meta:
        model  = Company
        fields = ['id', 'code', 'name', 'legal_name', 'registration_number',
                  'tax_id', 'country', 'base_currency', 'base_currency_name',
                  'address', 'is_active', 'is_default',
                  # CFO directive 2026-05-19: entity_type drives the
                  # dashboard layout (insurance vs trading).
                  'entity_type',
                  'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']


class ExchangeRateSerializer(serializers.ModelSerializer):
    from_currency_name = serializers.CharField(source='from_currency.name', read_only=True)
    to_currency_name   = serializers.CharField(source='to_currency.name',   read_only=True)
    loaded_by_name     = serializers.CharField(source='loaded_by.get_full_name', read_only=True, default=None)
    approved_by_name   = serializers.CharField(source='approved_by.get_full_name', read_only=True, default=None)
    is_approved        = serializers.BooleanField(read_only=True)

    class Meta:
        model  = ExchangeRate
        fields = ['id', 'from_currency', 'from_currency_name',
                  'to_currency', 'to_currency_name',
                  'rate', 'effective_date', 'source', 'notes',
                  'loaded_by', 'loaded_by_name',
                  'approved_by', 'approved_by_name', 'approved_at',
                  'is_approved',
                  'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at',
                            'loaded_by', 'loaded_by_name',
                            'approved_by', 'approved_by_name', 'approved_at',
                            'is_approved']


class TaxRateSerializer(serializers.ModelSerializer):
    class Meta:
        model  = TaxRate
        fields = ['id', 'tax_code', 'name', 'rate', 'is_active',
                  'effective_from', 'effective_to']
        read_only_fields = ['id']


# ─── User profiles ────────────────────────────────────────────────────────────

class UserProfileSerializer(serializers.ModelSerializer):
    """Read serializer — used in list / detail."""
    username   = serializers.CharField(source='user.username',   read_only=True)
    first_name = serializers.CharField(source='user.first_name', read_only=True)
    last_name  = serializers.CharField(source='user.last_name',  read_only=True)
    email      = serializers.CharField(source='user.email',      read_only=True)
    is_user_active = serializers.BooleanField(source='user.is_active', read_only=True)
    title_display  = serializers.CharField(source='get_title_display', read_only=True)
    role_display   = serializers.CharField(source='get_role_display',  read_only=True)
    can_approve_journal_entries = serializers.BooleanField(read_only=True)
    can_create_journal_entries  = serializers.BooleanField(read_only=True)
    can_approve_payroll         = serializers.BooleanField(read_only=True)
    can_administer_users        = serializers.BooleanField(read_only=True)
    can_post_directly           = serializers.BooleanField(read_only=True)
    can_manage_periods          = serializers.BooleanField(read_only=True)
    is_payroll_processor        = serializers.SerializerMethodField()
    can_view_internal_audit     = serializers.SerializerMethodField()
    can_edit_internal_audit     = serializers.SerializerMethodField()

    def get_is_payroll_processor(self, obj) -> bool:
        from core.models import is_payroll_processor
        return is_payroll_processor(getattr(obj, 'user', None))

    def get_can_view_internal_audit(self, obj) -> bool:
        from internal_audit.access import can_view
        return can_view(getattr(obj, 'user', None))

    def get_can_edit_internal_audit(self, obj) -> bool:
        from internal_audit.access import is_editor
        return is_editor(getattr(obj, 'user', None))

    class Meta:
        model = UserProfile
        fields = [
            'id', 'username', 'first_name', 'last_name', 'email',
            'role', 'role_display', 'title', 'title_display',
            'department', 'job_title', 'is_administrator', 'is_access_delegate', 'is_active', 'is_user_active',
            'can_approve_journal_entries', 'can_create_journal_entries',
            'can_approve_payroll', 'is_payroll_processor',
            'can_administer_users', 'can_post_directly',
            'can_manage_periods',
            'can_view_internal_audit', 'can_edit_internal_audit',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class UserProfileMeSerializer(UserProfileSerializer):
    """`/me/` endpoint — same shape as the read serializer, PLUS the mobile
    capability manifest (Omni Mobile, Workstream A).

    The manifest is server-truth: the phone renders its Home / Work / Inbox /
    People / Me surfaces from these booleans and never infers authority from the
    title string. See ``core.mobile_capabilities.build_mobile_capabilities``.
    """
    mobile_capabilities = serializers.SerializerMethodField()

    def get_mobile_capabilities(self, obj) -> dict:
        from core.mobile_capabilities import build_mobile_capabilities
        return build_mobile_capabilities(getattr(obj, 'user', None))

    class Meta(UserProfileSerializer.Meta):
        fields = UserProfileSerializer.Meta.fields + ['mobile_capabilities']


class UserProfileWriteSerializer(serializers.ModelSerializer):
    """
    Create / update serializer used by admins.

    On CREATE: also creates the underlying Django User with the supplied
    username, password (optional), email, first_name, last_name.
    On UPDATE: lets admins change title / role / department / admin / active.
    """
    username   = serializers.CharField(write_only=True, required=False)
    password   = serializers.CharField(write_only=True, required=False, allow_blank=True)
    first_name = serializers.CharField(write_only=True, required=False, allow_blank=True)
    last_name  = serializers.CharField(write_only=True, required=False, allow_blank=True)
    email      = serializers.EmailField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = UserProfile
        fields = [
            'id', 'username', 'password', 'first_name', 'last_name', 'email',
            'role', 'title', 'department', 'is_administrator',
            'is_access_delegate', 'is_active',
        ]
        read_only_fields = ['id']

    def create(self, validated_data):
        username   = validated_data.pop('username')
        password   = validated_data.pop('password',   '') or ''
        first_name = validated_data.pop('first_name', '') or ''
        last_name  = validated_data.pop('last_name',  '') or ''
        email      = validated_data.pop('email',      '') or ''

        with transaction.atomic():
            user = User.objects.create(
                username=username,
                first_name=first_name,
                last_name=last_name,
                email=email,
            )
            if password:
                user.set_password(password)
            else:
                # No password set means the user can't log in until reset
                user.set_unusable_password()
            user.save()

            profile = UserProfile.objects.create(user=user, **validated_data)
        return profile

    def update(self, instance, validated_data):
        # Only username / password / name / email if provided — admin reset path
        user_fields_changed = False
        password = validated_data.pop('password', None)
        for field in ('username', 'first_name', 'last_name', 'email'):
            if field in validated_data:
                setattr(instance.user, field, validated_data.pop(field) or '')
                user_fields_changed = True
        if password is not None and password != '':
            instance.user.set_password(password)
            user_fields_changed = True
        if user_fields_changed:
            instance.user.save()

        for attr, val in validated_data.items():
            setattr(instance, attr, val)
        instance.save()
        return instance

    def to_representation(self, instance):
        # Echo back the read shape after write
        return UserProfileSerializer(instance, context=self.context).data


class NamedModuleAccessSerializer(serializers.ModelSerializer):
    """Module access as data (CFO directive 2026-09-15)."""
    granted_by_name = serializers.SerializerMethodField()

    class Meta:
        model = NamedModuleAccess
        fields = ['id', 'module', 'email', 'is_active', 'note',
                  'granted_by', 'granted_by_name', 'created_at']
        read_only_fields = ['id', 'granted_by', 'granted_by_name', 'created_at']

    def get_granted_by_name(self, obj):
        return obj.granted_by.get_full_name() if obj.granted_by else ''

    def validate_email(self, value):
        """Company addresses only — module access is for staff."""
        value = (value or '').strip().lower()
        if not value.endswith('@alphadirect.co.bw'):
            raise serializers.ValidationError(
                'Only alphadirect.co.bw addresses can be given module access.')
        return value
