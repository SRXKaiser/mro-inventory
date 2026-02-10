# inventory/services/stock_service.py
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from inventory.models import InventoryMovement, StockSnapshot


@dataclass(frozen=True)
class StockResult:
    item_id: int
    location_id: int
    new_on_hand: Decimal
    movement_id: int


@dataclass(frozen=True)
class TransferResult:
    item_id: int
    from_location_id: int
    to_location_id: int
    qty: Decimal
    from_new_on_hand: Decimal
    to_new_on_hand: Decimal
    out_movement_id: int
    in_movement_id: int


@dataclass(frozen=True)
class VoidResult:
    original_id: int
    void_id: int
    item_id: int
    location_id: int
    new_on_hand: Decimal


@dataclass(frozen=True)
class ReserveResult:
    item_id: int
    location_id: int
    new_reserved: Decimal
    new_available: Decimal


class StockService:
    # ------------------------
    # Helpers
    # ------------------------
    @staticmethod
    def _validate_common(item, location, registered_by, quantity: Decimal):
        if item is None:
            raise ValidationError("item es obligatorio.")
        if location is None:
            raise ValidationError("location es obligatorio.")
        if registered_by is None:
            raise ValidationError("registered_by es obligatorio.")
        if quantity is None or quantity <= Decimal("0"):
            raise ValidationError("La cantidad debe ser mayor a 0.")

    @staticmethod
    def _get_snapshot_for_update(*, item, location) -> StockSnapshot:
        """
        Obtiene/crea snapshot con lock. Defaults consistentes con reserved.
        """
        snap, _ = StockSnapshot.objects.select_for_update().get_or_create(
            item=item,
            location=location,
            defaults={
                "on_hand": Decimal("0.000"),
                "reserved": Decimal("0.000"),
                "last_movement_at": None,
            },
        )
        return snap

    # ------------------------
    # A0) Movimientos simples IN/OUT
    # ------------------------
    @staticmethod
    @transaction.atomic
    def register_movement(
        *,
        item,
        location,
        movement_type,
        quantity: Decimal,
        registered_by,
        reference: str = "",
        notes: str = "",
        occurred_at=None,
    ) -> StockResult:
        StockService._validate_common(item, location, registered_by, quantity)

        reference = (reference or "").strip()
        notes = (notes or "").strip()

        if occurred_at is None:
            occurred_at = timezone.now()

        snapshot = StockService._get_snapshot_for_update(item=item, location=location)

        if movement_type == InventoryMovement.MovementType.IN_:
            new_value = snapshot.on_hand + quantity

        elif movement_type == InventoryMovement.MovementType.OUT:
            available = snapshot.on_hand - snapshot.reserved
            if quantity > available:
                raise ValidationError(
                    f"Stock insuficiente. Disponible: {available}. Reservado: {snapshot.reserved}."
                )
            new_value = snapshot.on_hand - quantity

        elif movement_type == InventoryMovement.MovementType.ADJ:
            raise ValidationError("Usa adjust_delta/adjust_set para ADJ (formal).")

        else:
            raise ValidationError("Tipo de movimiento inválido.")

        mv = InventoryMovement.objects.create(
            item=item,
            location=location,
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

        return StockResult(item_id=item.id, location_id=location.id, new_on_hand=new_value, movement_id=mv.id)

    # ------------------------
    # A1) Transferencia OUT + IN
    # ------------------------
    @staticmethod
    @transaction.atomic
    def transfer(
        *,
        item,
        from_location,
        to_location,
        quantity: Decimal,
        registered_by,
        reference: str = "",
        notes: str = "",
        occurred_at=None,
    ) -> TransferResult:
        if from_location is None or to_location is None:
            raise ValidationError("from_location y to_location son obligatorios.")
        if from_location.id == to_location.id:
            raise ValidationError("La ubicación origen y destino no pueden ser la misma.")
        StockService._validate_common(item, from_location, registered_by, quantity)

        reference = (reference or "").strip()
        notes = (notes or "").strip()
        if occurred_at is None:
            occurred_at = timezone.now()

        # Lock determinístico para evitar deadlocks
        loc_a, loc_b = sorted([from_location, to_location], key=lambda x: x.id)

        snap_a = StockService._get_snapshot_for_update(item=item, location=loc_a)
        snap_b = StockService._get_snapshot_for_update(item=item, location=loc_b)

        snap_from = snap_a if loc_a.id == from_location.id else snap_b
        snap_to = snap_b if snap_from is snap_a else snap_a

        available_from = snap_from.on_hand - snap_from.reserved
        if quantity > available_from:
            raise ValidationError(
                f"Stock insuficiente en origen. Disponible: {available_from}. Reservado: {snap_from.reserved}."
            )

        new_from = snap_from.on_hand - quantity
        new_to = snap_to.on_hand + quantity

        out_mv = InventoryMovement.objects.create(
            item=item,
            location=from_location,
            movement_type=InventoryMovement.MovementType.OUT,
            quantity=quantity,
            occurred_at=occurred_at,
            registered_by=registered_by,
            reference=reference,
            notes=(notes + "\n[TRANSFER OUT]").strip(),
        )
        in_mv = InventoryMovement.objects.create(
            item=item,
            location=to_location,
            movement_type=InventoryMovement.MovementType.IN_,
            quantity=quantity,
            occurred_at=occurred_at,
            registered_by=registered_by,
            reference=reference,
            notes=(notes + "\n[TRANSFER IN]").strip(),
        )

        snap_from.on_hand = new_from
        snap_from.last_movement_at = occurred_at
        snap_from.save(update_fields=["on_hand", "last_movement_at", "updated_at"])

        snap_to.on_hand = new_to
        snap_to.last_movement_at = occurred_at
        snap_to.save(update_fields=["on_hand", "last_movement_at", "updated_at"])

        return TransferResult(
            item_id=item.id,
            from_location_id=from_location.id,
            to_location_id=to_location.id,
            qty=quantity,
            from_new_on_hand=new_from,
            to_new_on_hand=new_to,
            out_movement_id=out_mv.id,
            in_movement_id=in_mv.id,
        )

    # ------------------------
    # B3) Ajustes (ADJ) formales
    # ------------------------
    @staticmethod
    @transaction.atomic
    def adjust_delta(
        *,
        item,
        location,
        delta: Decimal,  # puede ser + o -
        registered_by,
        reason: str,
        reference: str = "",
        notes: str = "",
        occurred_at=None,
    ) -> StockResult:
        if item is None:
            raise ValidationError("item es obligatorio.")
        if location is None:
            raise ValidationError("location es obligatorio.")
        if registered_by is None:
            raise ValidationError("registered_by es obligatorio.")
        if delta is None or delta == Decimal("0"):
            raise ValidationError("delta no puede ser 0.")
        if not (reason or "").strip():
            raise ValidationError("reason es obligatorio para un ajuste (ADJ).")

        reference = (reference or "").strip()
        notes = (notes or "").strip()
        if occurred_at is None:
            occurred_at = timezone.now()

        snapshot = StockService._get_snapshot_for_update(item=item, location=location)

        # Regla: no se permite dejar on_hand por debajo de reserved
        new_on_hand = snapshot.on_hand + delta
        if new_on_hand < snapshot.reserved:
            raise ValidationError(
                f"No se puede aplicar ajuste: dejaría on_hand({new_on_hand}) < reserved({snapshot.reserved})."
            )
        if new_on_hand < Decimal("0.000"):
            raise ValidationError("No se puede aplicar ajuste: dejaría el stock negativo.")

        InventoryMovement.objects.create(
            item=item,
            location=location,
            movement_type=InventoryMovement.MovementType.ADJ,
            quantity=abs(delta),
            occurred_at=occurred_at,
            registered_by=registered_by,
            reference=reference,
            notes=(f"[ADJ DELTA] {reason}\n{notes}").strip(),
        )

        snapshot.on_hand = new_on_hand
        snapshot.last_movement_at = occurred_at
        snapshot.save(update_fields=["on_hand", "last_movement_at", "updated_at"])

        return StockResult(item_id=item.id, location_id=location.id, new_on_hand=new_on_hand)

    @staticmethod
    @transaction.atomic
    def adjust_set(
        *,
        item,
        location,
        new_on_hand: Decimal,
        registered_by,
        reason: str,
        reference: str = "",
        notes: str = "",
        occurred_at=None,
    ) -> StockResult:
        if item is None:
            raise ValidationError("item es obligatorio.")
        if location is None:
            raise ValidationError("location es obligatorio.")
        if registered_by is None:
            raise ValidationError("registered_by es obligatorio.")
        if new_on_hand is None:
            raise ValidationError("new_on_hand es obligatorio.")
        if new_on_hand < Decimal("0.000"):
            raise ValidationError("new_on_hand no puede ser negativo.")
        if not (reason or "").strip():
            raise ValidationError("reason es obligatorio para un ajuste (ADJ).")

        reference = (reference or "").strip()
        notes = (notes or "").strip()
        if occurred_at is None:
            occurred_at = timezone.now()

        snapshot = StockService._get_snapshot_for_update(item=item, location=location)

        # Regla: no se permite set por debajo de reserved
        if new_on_hand < snapshot.reserved:
            raise ValidationError(
                f"No se puede aplicar ajuste: new_on_hand({new_on_hand}) < reserved({snapshot.reserved})."
            )

        # Log ADJ como evento formal
        InventoryMovement.objects.create(
            item=item,
            location=location,
            movement_type=InventoryMovement.MovementType.ADJ,
            quantity=abs(new_on_hand - snapshot.on_hand),
            occurred_at=occurred_at,
            registered_by=registered_by,
            reference=reference,
            notes=(f"[ADJ SET] {reason}\n{notes}").strip(),
        )

        snapshot.on_hand = new_on_hand
        snapshot.last_movement_at = occurred_at
        snapshot.save(update_fields=["on_hand", "last_movement_at", "updated_at"])

        return StockResult(item_id=item.id, location_id=location.id, new_on_hand=new_on_hand)

    # ------------------------
    # E2) Reservas (Commit / Release)
    # ------------------------
    @staticmethod
    @transaction.atomic
    def reserve(
        *,
        item,
        location,
        quantity: Decimal,
        reserved_by,
        reference: str = "",
        notes: str = "",
        occurred_at=None,
    ) -> ReserveResult:
        if item is None:
            raise ValidationError("item es obligatorio.")
        if location is None:
            raise ValidationError("location es obligatorio.")
        if reserved_by is None:
            raise ValidationError("reserved_by es obligatorio.")
        if quantity is None or quantity <= Decimal("0"):
            raise ValidationError("La cantidad debe ser mayor a 0.")

        reference = (reference or "").strip()
        notes = (notes or "").strip()
        if occurred_at is None:
            occurred_at = timezone.now()

        snapshot = StockService._get_snapshot_for_update(item=item, location=location)

        available = snapshot.on_hand - snapshot.reserved
        if quantity > available:
            raise ValidationError(f"No se puede reservar. Disponible: {available}.")

        snapshot.reserved = snapshot.reserved + quantity
        snapshot.last_movement_at = occurred_at
        snapshot.save(update_fields=["reserved", "last_movement_at", "updated_at"])

        return ReserveResult(
            item_id=item.id,
            location_id=location.id,
            new_reserved=snapshot.reserved,
            new_available=(snapshot.on_hand - snapshot.reserved),
        )

    @staticmethod
    @transaction.atomic
    def release(
        *,
        item,
        location,
        quantity: Decimal,
        released_by,
        reference: str = "",
        notes: str = "",
        occurred_at=None,
    ) -> ReserveResult:
        if item is None:
            raise ValidationError("item es obligatorio.")
        if location is None:
            raise ValidationError("location es obligatorio.")
        if released_by is None:
            raise ValidationError("released_by es obligatorio.")
        if quantity is None or quantity <= Decimal("0"):
            raise ValidationError("La cantidad debe ser mayor a 0.")

        reference = (reference or "").strip()
        notes = (notes or "").strip()
        if occurred_at is None:
            occurred_at = timezone.now()

        snapshot = StockService._get_snapshot_for_update(item=item, location=location)

        if quantity > snapshot.reserved:
            raise ValidationError(f"No se puede liberar más de lo reservado. Reservado: {snapshot.reserved}.")

        snapshot.reserved = snapshot.reserved - quantity
        snapshot.last_movement_at = occurred_at
        snapshot.save(update_fields=["reserved", "last_movement_at", "updated_at"])

        return ReserveResult(
            item_id=item.id,
            location_id=location.id,
            new_reserved=snapshot.reserved,
            new_available=(snapshot.on_hand - snapshot.reserved),
        )

    @staticmethod
    @transaction.atomic
    def void_movement(
        *,
        movement: InventoryMovement,
        voided_by,
        reason: str,
        reference: str = "",
        notes: str = "",
        occurred_at=None,
    ) -> VoidResult:
        if movement is None:
            raise ValidationError("movement es obligatorio.")
        if movement.is_void:
            raise ValidationError("Este movimiento ya está anulado.")
        if hasattr(movement, "voided_by_movement"):
            raise ValidationError("Este movimiento ya tiene reverso asociado.")

        if not reason or not reason.strip():
            raise ValidationError("El motivo de anulación es obligatorio.")
        if movement.location_id is None:
            raise ValidationError("No se puede anular un movimiento sin ubicación.")

        reference = (reference or "").strip()
        notes = (notes or "").strip()
        if occurred_at is None:
            occurred_at = timezone.now()

        snapshot = StockService._get_snapshot_for_update(item=movement.item, location=movement.location)

    # Determina el movimiento inverso (y valida contra reserved cuando corresponde)
        if movement.movement_type == InventoryMovement.MovementType.IN_:
            inverse_type = InventoryMovement.MovementType.OUT

            available = snapshot.on_hand - snapshot.reserved
            if movement.quantity > available:
                raise ValidationError(
                    f"No se puede anular este IN: parte del stock ya está reservado. "
                f"Disponible: {available}. Reservado: {snapshot.reserved}."
            )

            new_value = snapshot.on_hand - movement.quantity

        elif movement.movement_type == InventoryMovement.MovementType.OUT:
            inverse_type = InventoryMovement.MovementType.IN_
            new_value = snapshot.on_hand + movement.quantity

        elif movement.movement_type == InventoryMovement.MovementType.ADJ:
            raise ValidationError("Anulación de ADJ aún no habilitada en esta fase.")
        else:
            raise ValidationError("Tipo de movimiento inválido.")

    # Crea el movimiento inverso (reverso)
        void_m = InventoryMovement.objects.create(
            item=movement.item,
            location=movement.location,
            movement_type=inverse_type,
            quantity=movement.quantity,
            occurred_at=occurred_at,
            registered_by=voided_by,
            reference=reference or f"VOID:{movement.id}",
            notes=notes,

            is_void=True,
            voided_at=occurred_at,
            voided_by=voided_by,
            void_reason=reason.strip(),

            void_of=movement,
        )

    # Marca original como void (soft)
        movement.is_void = True
        movement.voided_at = occurred_at
        movement.voided_by = voided_by
        movement.void_reason = reason.strip()
        movement.save(update_fields=["is_void", "voided_at", "voided_by", "void_reason", "updated_at"])

    # Aplica snapshot
        snapshot.on_hand = new_value
        snapshot.last_movement_at = occurred_at
        snapshot.save(update_fields=["on_hand", "last_movement_at", "updated_at"])

        return VoidResult(
            original_id=movement.id,
            void_id=void_m.id,
            item_id=movement.item_id,
            location_id=movement.location_id,
            new_on_hand=new_value,
        )

