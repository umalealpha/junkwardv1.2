from django.contrib import admin

from .models import (ProviderDashboardConfig, ServiceProvider,
                     ServiceProviderApplication, VendorOnboarding)


@admin.register(ProviderDashboardConfig)
class ProviderDashboardConfigAdmin(admin.ModelAdmin):
    list_display = ("enabled", "updated_by", "updated_at")


@admin.register(ServiceProvider)
class ServiceProviderAdmin(admin.ModelAdmin):
    list_display = ("practice_number", "name", "discipline", "town",
                    "contract_status", "qc_confirmed", "is_active")
    list_filter = ("discipline", "contract_status", "qc_confirmed", "is_active")
    search_fields = ("practice_number", "name", "town", "email")
    readonly_fields = ("created_at", "updated_at", "last_imported_at", "source_file")


@admin.register(ServiceProviderApplication)
class ServiceProviderApplicationAdmin(admin.ModelAdmin):
    list_display = ("name", "discipline", "town", "status", "created_at")
    list_filter = ("status", "discipline")
    search_fields = ("name", "town", "email", "practice_number")
    readonly_fields = ("created_at", "updated_at", "submitter_ip", "submitter_user_agent")


@admin.register(VendorOnboarding)
class VendorOnboardingAdmin(admin.ModelAdmin):
    list_display = ("reference_number", "company_name", "service_category",
                    "status", "agreement_email_sent", "created_at")
    list_filter = ("status", "agreement_email_sent", "council_type", "service_category")
    search_fields = ("reference_number", "company_name", "registration_number",
                     "practitioner_name", "trading_name")
    readonly_fields = ("reference_number", "created_at", "payload", "signature_data_url")
    # Mask the bank account in the changelist; full value stays on the record.
    def get_readonly_fields(self, request, obj=None):
        return self.readonly_fields
