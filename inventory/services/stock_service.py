from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ValidationError

from inventory.models import Item, InventoryMovement, StockSnapshot


@dataclass(frozen=True)
class StockResult:
    item_id: int
    new_on_hand: Decimal


class StockService:
    @staticmethod
    @transaction.atomic
    def register_movement(
        *,
        item: Item,
        movement_type: str,
        quantity: Decimal,
        registered_by,
        occurred_at=None,
        reference: str = "",
        notes: str = "",
    ) -> StockResult:
        if quantity <= Decimal("0"):
            raise ValidationError("La cantidad debe ser mayor a 0.")

        if occurred_at is None:
            occurred_at = timezone.now()

        # Lock por fila para evitar inconsistencias en concurrencia
        snapshot, _ = StockSnapshot.objects.select_for_update().get_or_create(
            item=item,
            defaults={"on_hand": Decimal("0.000"), "last_movement_at": None},
        )

        current = snapshot.on_hand

        if movement_type == InventoryMovement.MovementType.IN_:
            new_value = current + quantity
        elif movement_type == InventoryMovement.MovementType.OUT:
            new_value = current - quantity
            if new_value < Decimal("0.000"):
                raise ValidationError("Stock insuficiente: la salida dejaría el stock en negativo.")
        elif movement_type == InventoryMovement.MovementType.ADJ:
            # Para Iteración 1 puedes usar ADJ como ajuste positivo o negativo vía 'notes' y convención.
            # Si quieres ajuste con signo, lo modelamos con quantity_signed.
            raise ValidationError("ADJ aún no habilitado en esta iteración. Usa IN/OUT.")
        else:
            raise ValidationError("Tipo de movimiento inválido.")

        InventoryMovement.objects.create(
            item=item,
            movement_type=movement_type,
            quantity=quantity,
            occurred_at=occurred_at,
            registered_by=registered_by,
            reference=reference,
            notes=notes,
        )

        snapshot.on_hand = new_value
        snapshot.last_movement_at = occurred_at
        snapshot.save(update_fields=["on_hand", "last_movement_at", "updated_at"])

        return StockResult(item_id=item.id, new_on_hand=new_value)
