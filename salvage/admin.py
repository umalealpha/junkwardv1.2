from django.contrib import admin

from .models import (
    Bid, PartCategory, SalvageAuction, SalvageImage, SalvageInspection,
    SalvageItem, VehicleBrand, VehicleModel,
)


@admin.register(PartCategory)
class PartCategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'is_active']
    search_fields = ['name']


@admin.register(VehicleBrand)
class VehicleBrandAdmin(admin.ModelAdmin):
    list_display = ['name', 'is_active']
    search_fields = ['name']


@admin.register(VehicleModel)
class VehicleModelAdmin(admin.ModelAdmin):
    list_display  = ['brand', 'name', 'is_active']
    list_filter   = ['brand']
    search_fields = ['name', 'brand__name']


class SalvageImageInline(admin.TabularInline):
    model = SalvageImage
    extra = 0


@admin.register(SalvageItem)
class SalvageItemAdmin(admin.ModelAdmin):
    list_display  = [
        'item_code', 'part_name', 'company',
        'condition', 'status', 'asking_price',
    ]
    list_filter   = ['status', 'condition', 'company']
    search_fields = ['item_code', 'part_name', 'claim_number', 'vin_number']
    inlines       = [SalvageImageInline]
    autocomplete_fields = ['category', 'vehicle_brand', 'vehicle_model']


@admin.register(SalvageInspection)
class SalvageInspectionAdmin(admin.ModelAdmin):
    list_display  = [
        'item', 'inspected_at', 'salvage_title_status',
        'runs_drives', 'mileage_km', 'inspected_by',
    ]
    list_filter   = ['salvage_title_status', 'runs_drives', 'airbags_deployed']
    search_fields = ['item__item_code', 'engine_status', 'transmission_status']
    autocomplete_fields = ['item']


@admin.register(SalvageAuction)
class SalvageAuctionAdmin(admin.ModelAdmin):
    list_display  = [
        'item', 'status', 'opens_at', 'closes_at',
        'min_bid', 'current_high_bid',
    ]
    list_filter   = ['status']
    search_fields = ['item__item_code']
    autocomplete_fields = ['item', 'current_high_bid']


@admin.register(Bid)
class BidAdmin(admin.ModelAdmin):
    list_display  = ['auction', 'buyer', 'amount', 'placed_at', 'withdrawn']
    list_filter   = ['withdrawn']
    search_fields = ['auction__item__item_code', 'buyer__buyer_name']
