# inventory/models.py
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

from locations.models import Location


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class ItemType(TimeStampedModel):
    name = models.CharField(max_length=80, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self) -> str:
        return self.name


class UnitOfMeasure(TimeStampedModel):
    code = models.CharField(max_length=10, unique=True)
    name = models.CharField(max_length=40)
    is_active = models.BooleanField(default=True)

    def __str__(self) -> str:
        return self.code


class Criticality(TimeStampedModel):
    code = models.CharField(max_length=10, unique=True)
    name = models.CharField(max_length=40)
    rank = models.IntegerField()
    is_active = models.BooleanField(default=True)

    def __str__(self) -> str:
        return self.name


class Item(TimeStampedModel):
    sku = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)

    item_type = models.ForeignKey(ItemType, on_delete=models.PROTECT)
    criticality = models.ForeignKey(Criticality, on_delete=models.PROTECT)
    uom = models.ForeignKey(UnitOfMeasure, on_delete=models.PROTECT)

    min_stock = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal("0.000"))
    is_active = models.BooleanField(default=True)

    def __str__(self) -> str:
        return f"{self.sku} - {self.name}"


class StockSnapshot(TimeStampedModel):
    """
    Stock por (Item, Location).
    El stock existe físicamente en una ubicación.
    """
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="stock_snapshots")

    location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        related_name="stock_snapshots",
        null=True,
        blank=True,
    )

    # Stock físico
    on_hand = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal("0.000"),
        validators=[MinValueValidator(Decimal("0.000"))],
    )

    # Stock reservado/comprometido
    reserved = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal("0.000"),
        validators=[MinValueValidator(Decimal("0.000"))],
    )

    last_movement_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["item", "location"], name="uq_stock_item_location"),
        ]
        indexes = [
            models.Index(fields=["item", "location"]),
            models.Index(fields=["location", "item"]),
        ]

    @property
    def available(self) -> Decimal:
        # disponible = físico - reservado
        return self.on_hand - self.reserved

    def __str__(self) -> str:
        loc = self.location if self.location_id else "N/A"
        return f"{self.item.sku} @ {loc} -> on_hand={self.on_hand} reserved={self.reserved}"


class InventoryMovement(TimeStampedModel):
    class MovementType(models.TextChoices):
        IN_ = "IN", "Entrada"
        OUT = "OUT", "Salida"
        ADJ = "ADJ", "Ajuste"

    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="movements")

    location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        related_name="movements",
        null=True,
        blank=True,
    )

    movement_type = models.CharField(max_length=3, choices=MovementType.choices)
    quantity = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    occurred_at = models.DateTimeField()

    registered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    reference = models.CharField(max_length=80, blank=True)
    notes = models.TextField(blank=True)

    # ===== VOID / ANULACIÓN =====
    is_void = models.BooleanField(default=False)
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="voided_movements",
    )
    void_reason = models.CharField(max_length=180, blank=True)

    # Liga al movimiento que anula/revierte (y viceversa)
    # - Si ESTE movimiento es el reverso, aquí apuntas al ORIGINAL
    # - En el ORIGINAL, lo puedes consultar con: original.voided_by_movement
    void_of = models.OneToOneField(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="voided_by_movement",
        help_text="Si este movimiento es el reverso de otro, aquí va el original.",
    )

    class Meta:
        indexes = [
            models.Index(fields=["item", "location", "occurred_at"]),
            models.Index(fields=["location", "occurred_at"]),
            models.Index(fields=["is_void", "occurred_at"]),
        ]

    def __str__(self) -> str:
        loc = self.location if self.location_id else "N/A"
        base = f"{self.movement_type} {self.quantity} {self.item.sku} @ {loc}"
        return f"[VOID] {base}" if self.is_void else base
