from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models



class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

class ItemType(TimeStampedModel):
    name = models.CharField(max_length=80, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name

class UnitOfMeasure(TimeStampedModel):
    code = models.CharField(max_length=10, unique=True)
    name = models.CharField(max_length=40)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.code

class Criticality(TimeStampedModel):
    code = models.CharField(max_length=10, unique=True)
    name = models.CharField(max_length=40)
    rank = models.IntegerField()
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name

class Item(TimeStampedModel):
    sku = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)

    item_type = models.ForeignKey(ItemType, on_delete=models.PROTECT)
    criticality = models.ForeignKey(Criticality, on_delete=models.PROTECT)
    uom = models.ForeignKey(UnitOfMeasure, on_delete=models.PROTECT)

    min_stock = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.sku} - {self.name}"

class StockSnapshot(TimeStampedModel):
    item = models.OneToOneField("Item", on_delete=models.CASCADE, related_name="stock_snapshot")
    on_hand = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal("0.000"),
        validators=[MinValueValidator(Decimal("0.000"))],
    )
    last_movement_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.item.sku} -> {self.on_hand}"


class InventoryMovement(TimeStampedModel):
    class MovementType(models.TextChoices):
        IN_ = "IN", "Entrada"
        OUT = "OUT", "Salida"
        ADJ = "ADJ", "Ajuste"

    item = models.ForeignKey("Item", on_delete=models.PROTECT, related_name="movements")
    movement_type = models.CharField(max_length=3, choices=MovementType.choices)
    quantity = models.DecimalField(max_digits=12, decimal_places=3, validators=[MinValueValidator(Decimal("0.001"))])
    occurred_at = models.DateTimeField()

    registered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    reference = models.CharField(max_length=80, blank=True)
    notes = models.TextField(blank=True)

    def __str__(self):
        return f"{self.movement_type} {self.quantity} {self.item.sku}"


