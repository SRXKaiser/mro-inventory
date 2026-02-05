from django.contrib import admin
from .models import Item, ItemType, UnitOfMeasure, Criticality, InventoryMovement, StockSnapshot

@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ("sku", "name", "item_type", "criticality", "uom", "min_stock", "is_active")
    search_fields = ("sku", "name")
    list_filter = ("item_type", "criticality", "is_active")


admin.site.register(ItemType)
admin.site.register(UnitOfMeasure)
admin.site.register(Criticality)
admin.site.register(StockSnapshot)


@admin.register(InventoryMovement)
class InventoryMovementAdmin(admin.ModelAdmin):
    # Solo lectura: auditoría
    readonly_fields = [f.name for f in InventoryMovement._meta.fields]
    list_display = ("occurred_at", "movement_type", "item", "quantity", "registered_by", "reference")
    list_filter = ("movement_type",)
    search_fields = ("item__sku", "item__name", "reference", "registered_by__username")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

