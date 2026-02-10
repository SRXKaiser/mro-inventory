# workorders/admin.py
from django.contrib import admin

from .models import (
    WorkOrder,
    WorkOrderLine,
    Reservation,
    WorkOrderIssue,
    WorkOrderIssueLine,
    WorkOrderReturn,
    WorkOrderReturnLine,
)


# -------------------------
# Inlines
# -------------------------
class WorkOrderLineInline(admin.TabularInline):
    model = WorkOrderLine
    extra = 0
    autocomplete_fields = ["item"]
    fields = (
        "item",
        "qty_required",
        "qty_reserved",
        "qty_consumed",
        "qty_returned",
        "created_at",
        "updated_at",
    )
    readonly_fields = ("qty_reserved", "qty_consumed", "qty_returned", "created_at", "updated_at")


class ReservationInline(admin.TabularInline):
    model = Reservation
    extra = 0
    autocomplete_fields = ["item", "location", "created_by"]
    fields = (
        "line",
        "item",
        "location",
        "quantity",
        "status",
        "created_by",
        "released_at",
        "reason",
        "created_at",
        "updated_at",
    )
    readonly_fields = ("created_at", "updated_at")


class WorkOrderIssueInline(admin.TabularInline):
    model = WorkOrderIssue
    extra = 0
    autocomplete_fields = ["technician"]
    fields = ("technician", "notes", "created_at", "updated_at")
    readonly_fields = ("technician", "notes", "created_at", "updated_at")
    can_delete = False
    show_change_link = True


class WorkOrderReturnInline(admin.TabularInline):
    model = WorkOrderReturn
    extra = 0
    autocomplete_fields = ["technician"]
    fields = ("technician", "notes", "created_at", "updated_at")
    readonly_fields = ("technician", "notes", "created_at", "updated_at")
    can_delete = False
    show_change_link = True


# -------------------------
# Admin: WorkOrder
# -------------------------
@admin.register(WorkOrder)
class WorkOrderAdmin(admin.ModelAdmin):
    list_display = ("code", "status", "priority", "requested_by", "assigned_to", "created_at")
    list_filter = ("status", "priority", "created_at")
    search_fields = ("code", "notes")
    autocomplete_fields = ("requested_by", "assigned_to")
    ordering = ("-created_at",)
    date_hierarchy = "created_at"

    fieldsets = (
        ("Identificación", {"fields": ("code", "status", "priority")}),
        ("Personas", {"fields": ("requested_by", "assigned_to")}),
        ("Fechas", {"fields": ("approved_at", "started_at", "closed_at")}),
        ("Notas", {"fields": ("notes",)}),
        ("Auditoría", {"fields": ("created_at", "updated_at")}),
    )

    readonly_fields = ("created_at", "updated_at")

    inlines = [
        WorkOrderLineInline,
        ReservationInline,
        WorkOrderIssueInline,
        WorkOrderReturnInline,
    ]


# -------------------------
# Admin: WorkOrderLine
# -------------------------
@admin.register(WorkOrderLine)
class WorkOrderLineAdmin(admin.ModelAdmin):
    list_display = ("work_order", "item", "qty_required", "qty_reserved", "qty_consumed", "qty_returned", "created_at")
    list_filter = ("created_at",)
    search_fields = ("work_order__code", "item__sku", "item__name")
    autocomplete_fields = ("work_order", "item")
    readonly_fields = ("qty_reserved", "qty_consumed", "qty_returned", "created_at", "updated_at")
    ordering = ("-created_at",)


# -------------------------
# Admin: Reservation
# -------------------------
@admin.register(Reservation)
class ReservationAdmin(admin.ModelAdmin):
    list_display = ("work_order", "item", "location", "quantity", "status", "created_by", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("work_order__code", "item__sku", "item__name", "reason")
    autocomplete_fields = ("work_order", "line", "item", "location", "created_by")
    ordering = ("-created_at",)
    readonly_fields = ("created_at", "updated_at")


# -------------------------
# Admin: Issue / IssueLine
# -------------------------
class WorkOrderIssueLineInline(admin.TabularInline):
    model = WorkOrderIssueLine
    extra = 0
    autocomplete_fields = ("item", "location", "reservation", "movement_out")
    fields = ("item", "location", "quantity", "reservation", "movement_out", "created_at", "updated_at")
    readonly_fields = ("created_at", "updated_at")
    can_delete = False


@admin.register(WorkOrderIssue)
class WorkOrderIssueAdmin(admin.ModelAdmin):
    list_display = ("work_order", "technician", "created_at")
    list_filter = ("created_at",)
    search_fields = ("work_order__code", "technician__username", "technician__email")
    autocomplete_fields = ("work_order", "technician")
    ordering = ("-created_at",)
    readonly_fields = ("created_at", "updated_at")

    inlines = [WorkOrderIssueLineInline]


@admin.register(WorkOrderIssueLine)
class WorkOrderIssueLineAdmin(admin.ModelAdmin):
    list_display = ("issue", "item", "location", "quantity", "movement_out", "created_at")
    list_filter = ("created_at",)
    search_fields = ("issue__work_order__code", "item__sku", "item__name")
    autocomplete_fields = ("issue", "item", "location", "reservation", "movement_out")
    ordering = ("-created_at",)
    readonly_fields = ("created_at", "updated_at")


# -------------------------
# Admin: Return / ReturnLine
# -------------------------
class WorkOrderReturnLineInline(admin.TabularInline):
    model = WorkOrderReturnLine
    extra = 0
    autocomplete_fields = ("item", "location", "movement_in")
    fields = ("item", "location", "quantity", "movement_in", "created_at", "updated_at")
    readonly_fields = ("created_at", "updated_at")
    can_delete = False


@admin.register(WorkOrderReturn)
class WorkOrderReturnAdmin(admin.ModelAdmin):
    list_display = ("work_order", "technician", "created_at")
    list_filter = ("created_at",)
    search_fields = ("work_order__code", "technician__username", "technician__email")
    autocomplete_fields = ("work_order", "technician")
    ordering = ("-created_at",)
    readonly_fields = ("created_at", "updated_at")

    inlines = [WorkOrderReturnLineInline]


@admin.register(WorkOrderReturnLine)
class WorkOrderReturnLineAdmin(admin.ModelAdmin):
    list_display = ("work_order_return", "item", "location", "quantity", "movement_in", "created_at")
    list_filter = ("created_at",)
    search_fields = ("work_order_return__work_order__code", "item__sku", "item__name")
    autocomplete_fields = ("work_order_return", "item", "location", "movement_in")
    ordering = ("-created_at",)
    readonly_fields = ("created_at", "updated_at")
