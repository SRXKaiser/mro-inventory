from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

from inventory.models import Item, InventoryMovement
from locations.models import Location
from inventory.models import TimeStampedModel


class WorkOrder(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        APPROVED = "APPROVED", "Approved"
        IN_PROGRESS = "IN_PROGRESS", "In progress"
        PAUSED = "PAUSED", "Paused"
        COMPLETED = "COMPLETED", "Completed"
        CANCELLED = "CANCELLED", "Cancelled"

    class Priority(models.TextChoices):
        LOW = "LOW", "Low"
        MEDIUM = "MED", "Medium"
        HIGH = "HIGH", "High"
        URGENT = "URG", "Urgent"

    code = models.CharField(max_length=40, unique=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    priority = models.CharField(
        max_length=5,
        choices=Priority.choices,
        default=Priority.MEDIUM,
    )

    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="requested_workorders",
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="assigned_workorders",
        null=True,
        blank=True,
    )

    notes = models.TextField(blank=True)

    approved_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["priority"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"{self.code} ({self.status})"


class WorkOrderLine(TimeStampedModel):
    work_order = models.ForeignKey(
        WorkOrder,
        on_delete=models.CASCADE,
        related_name="lines",
    )
    item = models.ForeignKey(Item, on_delete=models.PROTECT)

    qty_required = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0.001"))],
    )

    qty_reserved = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal("0.000"),
        validators=[MinValueValidator(Decimal("0.000"))],
    )

    qty_consumed = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal("0.000"),
        validators=[MinValueValidator(Decimal("0.000"))],
    )

    qty_returned = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal("0.000"),
        validators=[MinValueValidator(Decimal("0.000"))],
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["work_order", "item"],
                name="uq_workorder_item",
            ),
        ]
        indexes = [
            models.Index(fields=["item"]),
        ]

    @property
    def qty_pending(self) -> Decimal:
        return self.qty_required - self.qty_consumed

    def __str__(self):
        return f"{self.work_order.code} - {self.item.sku}"


class Reservation(TimeStampedModel):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        RELEASED = "RELEASED", "Released"
        CONSUMED = "CONSUMED", "Consumed"
        VOIDED = "VOIDED", "Voided"

    work_order = models.ForeignKey(
        WorkOrder,
        on_delete=models.CASCADE,
        related_name="reservations",
    )
    line = models.ForeignKey(
        WorkOrderLine,
        on_delete=models.CASCADE,
        related_name="reservations",
    )
    item = models.ForeignKey(Item, on_delete=models.PROTECT)

    location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        help_text="Ubicación específica si se reserva por location",
    )

    quantity = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0.001"))],
    )

    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.ACTIVE,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
    )
    released_at = models.DateTimeField(null=True, blank=True)
    reason = models.CharField(max_length=180, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["item", "status"]),
            models.Index(fields=["work_order", "status"]),
        ]

    def __str__(self):
        return f"RES {self.quantity} {self.item.sku} ({self.status})"

class WorkOrderIssue(TimeStampedModel):
    work_order = models.ForeignKey(
        WorkOrder,
        on_delete=models.CASCADE,
        related_name="issues",
    )
    technician = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="workorder_issues",
    )
    notes = models.TextField(blank=True)

    def __str__(self):
        return f"Issue {self.work_order.code} → {self.technician}"

class WorkOrderIssueLine(TimeStampedModel):
    issue = models.ForeignKey(
        WorkOrderIssue,
        on_delete=models.CASCADE,
        related_name="lines",
    )
    item = models.ForeignKey(Item, on_delete=models.PROTECT)

    location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
    )

    quantity = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0.001"))],
    )

    reservation = models.ForeignKey(
        Reservation,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )

    movement_out = models.OneToOneField(
        InventoryMovement,
        on_delete=models.PROTECT,
        related_name="workorder_issue_line",
    )

    def __str__(self):
        return f"OUT {self.quantity} {self.item.sku}"

class WorkOrderReturn(TimeStampedModel):
    work_order = models.ForeignKey(
        WorkOrder,
        on_delete=models.CASCADE,
        related_name="returns",
    )
    technician = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
    )
    notes = models.TextField(blank=True)

    def __str__(self):
        return f"Return {self.work_order.code}"
    
class WorkOrderReturnLine(TimeStampedModel):
    work_order_return = models.ForeignKey(
        WorkOrderReturn,
        on_delete=models.CASCADE,
        related_name="lines",
    )
    item = models.ForeignKey(Item, on_delete=models.PROTECT)
    location = models.ForeignKey(Location, on_delete=models.PROTECT)

    quantity = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0.001"))],
    )

    movement_in = models.OneToOneField(
        InventoryMovement,
        on_delete=models.PROTECT,
        related_name="workorder_return_line",
    )

    def __str__(self):
        return f"IN {self.quantity} {self.item.sku}"
