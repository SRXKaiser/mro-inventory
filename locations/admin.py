from django.contrib import admin
from .models import Warehouse, Location


@admin.register(Warehouse)
class WarehouseAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_active", "created_at", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("code", "name")
    ordering = ("code",)


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ("warehouse", "code", "name", "is_active", "created_at", "updated_at")
    list_filter = ("is_active", "warehouse")
    search_fields = ("code", "name", "warehouse__code", "warehouse__name")
    ordering = ("warehouse__code", "code")
    