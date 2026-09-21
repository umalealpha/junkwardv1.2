from django.contrib import admin

from .models import (
    FleetVehicle, NexusDriver, NexusRewardLedger, NexusTrip,
    VehicleTrip, VehicleTripPhoto,
)


@admin.register(NexusDriver)
class NexusDriverAdmin(admin.ModelAdmin):
    list_display = ("full_name", "total_points", "created_at")
    search_fields = ("full_name", "external_ref")


@admin.register(NexusTrip)
class NexusTripAdmin(admin.ModelAdmin):
    list_display = ("driver", "label", "started_at", "distance_km", "score", "points")
    list_filter = ("driver",)


@admin.register(NexusRewardLedger)
class NexusRewardLedgerAdmin(admin.ModelAdmin):
    list_display = ("driver", "points", "description", "created_at")
    list_filter = ("driver",)


@admin.register(FleetVehicle)
class FleetVehicleAdmin(admin.ModelAdmin):
    list_display = ("registration", "make", "model", "status", "odometer_km", "home_yard")
    list_filter = ("status", "make")
    search_fields = ("registration", "make", "model", "vin")


class VehicleTripPhotoInline(admin.TabularInline):
    model = VehicleTripPhoto
    extra = 0
    readonly_fields = ("created_at",)


@admin.register(VehicleTrip)
class VehicleTripAdmin(admin.ModelAdmin):
    list_display = ("vehicle", "driver_name", "purpose", "status", "flagged",
                    "checkout_at", "checkin_at")
    list_filter = ("status", "flagged", "damage_on_return", "purpose")
    search_fields = ("driver_name", "destination", "vehicle__registration")
    readonly_fields = ("created_at", "updated_at", "distance_km", "is_overdue")
    inlines = [VehicleTripPhotoInline]
