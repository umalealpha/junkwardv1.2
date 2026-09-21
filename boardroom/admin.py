from django.contrib import admin

from .models import Booking, Room


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_prominent', 'floor', 'seats', 'is_active', 'sort_order')
    list_filter = ('is_prominent', 'is_active')
    search_fields = ('name', 'floor', 'kit')
    list_editable = ('is_prominent', 'is_active', 'sort_order')


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ('title', 'room', 'day', 'start_min', 'end_min', 'booked_by_name')
    list_filter = ('room', 'day')
    search_fields = ('title', 'booked_by_name')
    date_hierarchy = 'day'
