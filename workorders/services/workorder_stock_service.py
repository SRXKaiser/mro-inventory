from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from workorders.models import WorkOrderReturn, WorkOrderReturnLine
from inventory.models import Item, InventoryMovement, StockSnapshot
from inventory.services.stock_service import StockService
from locations.models import Location

from workorders.models import (
    WorkOrder,
    WorkOrderLine,
    Reservation,
    WorkOrderIssue,
    WorkOrderIssueLine,
    WorkOrderReturn,
    WorkOrderReturnLine,
)


@dataclass(frozen=True)
class ConsumeLine:
    item_id: int
    location_id: int
    qty: Decimal
    reservation_id: int | None = None  # opcional


@dataclass(frozen=True)
class ReturnLine:
    item_id: int
    location_id: int
    qty: Decimal


class WorkOrderStockService:
    """
    Integración Work Orders ↔ Inventario.
    - Las RESERVAS afectan StockSnapshot.reserved (no crean InventoryMovement).
    - El CONSUMO/DEVOLUCIÓN sí crean InventoryMovement (OUT/IN).
    """

    def __init__(self, stock_service: StockService):
        self._stock = stock_service

    # -------------------------
    # Reservas
    # -------------------------
    @transaction.atomic
    def reserve(
        self,
        *,
        line_id: int,
        qty: Decimal,
        user,
        location_id: int | None = None,
        reason: str = "",
    ) -> Reservation:
        if qty <= 0:
            raise ValidationError("La cantidad a reservar debe ser mayor que 0.")

        line = (
            WorkOrderLine.objects
            .select_related("work_order", "item")
            .select_for_update()
            .get(id=line_id)
        )

        wo = line.work_order
        if wo.status not in (WorkOrder.Status.DRAFT, WorkOrder.Status.APPROVED, WorkOrder.Status.IN_PROGRESS):
            raise ValidationError(f"No se puede reservar en una OT con estado: {wo.status}")

        # Bloqueamos snapshots relevantes para evitar race conditions
        if location_id is not None:
            snapshot = (
                StockSnapshot.objects
                .select_for_update()
                .get(item_id=line.item_id, location_id=location_id)
            )
            if snapshot.available < qty:
                raise ValidationError(
                    f"Stock insuficiente en la ubicación. Disponible={snapshot.available}, requerido={qty}."
                )
            snapshot.reserved = snapshot.reserved + qty
            snapshot.save(update_fields=["reserved", "updated_at"])
        else:
            # Reserva "sin location": debes escoger una location después para surtir/consumir.
            # Aun así, necesitas definir una política:
            # - O reservas por almacén (no lo tienes modelado aún)
            # - O exiges location
            # Aquí opto por exigir location para que sea consistente con tu StockSnapshot (item+location).
            raise ValidationError("Para reservar debes indicar una ubicación (location_id).")

        res = Reservation.objects.create(
            work_order=wo,
            line=line,
            item=line.item,
            location_id=location_id,
            quantity=qty,
            status=Reservation.Status.ACTIVE,
            created_by=user,
            reason=reason or "",
        )

        # Cache/update en la línea
        line.qty_reserved = line.qty_reserved + qty
        line.save(update_fields=["qty_reserved", "updated_at"])

        return res

    @transaction.atomic
    def release_reservation(
        self,
        *,
        reservation_id: int,
        qty: Decimal | None,
        user,
        reason: str = "",
    ) -> Reservation:
        res = (
            Reservation.objects
            .select_related("line", "work_order")
            .select_for_update()
            .get(id=reservation_id)
        )

        if res.status != Reservation.Status.ACTIVE:
            raise ValidationError("Solo se pueden liberar reservas en estado ACTIVE.")

        release_qty = res.quantity if qty is None else qty
        if release_qty <= 0:
            raise ValidationError("La cantidad a liberar debe ser mayor que 0.")
        if release_qty > res.quantity:
            raise ValidationError("No puedes liberar más de lo reservado.")

        # Actualiza snapshot.reserved
        snapshot = (
            StockSnapshot.objects
            .select_for_update()
            .get(item_id=res.item_id, location_id=res.location_id)
        )
        if snapshot.reserved < release_qty:
            raise ValidationError("Inconsistencia: reserved en snapshot es menor que lo que intentas liberar.")

        snapshot.reserved = snapshot.reserved - release_qty
        snapshot.save(update_fields=["reserved", "updated_at"])

        # Actualiza reservation
        remaining = res.quantity - release_qty
        if remaining == 0:
            res.status = Reservation.Status.RELEASED
            res.released_at = timezone.now()
            res.reason = reason or res.reason
            res.save(update_fields=["status", "released_at", "reason", "updated_at"])
        else:
            res.quantity = remaining
            res.reason = reason or res.reason
            res.save(update_fields=["quantity", "reason", "updated_at"])

        # Actualiza línea
        line = (
            WorkOrderLine.objects
            .select_for_update()
            .get(id=res.line_id)
        )
        if line.qty_reserved < release_qty:
            raise ValidationError("Inconsistencia: qty_reserved en línea es menor que lo que intentas liberar.")
        line.qty_reserved = line.qty_reserved - release_qty
        line.save(update_fields=["qty_reserved", "updated_at"])

        return res

    @transaction.atomic
    def consume(
        self,
        *,
        work_order_id: int,
        technician,
        registered_by,
        occurred_at=None,
        notes: str = "",
        lines: list[ConsumeLine],
    ) -> WorkOrderIssue:
        if not lines:
            raise ValidationError("No hay líneas para consumir.")

        wo = WorkOrder.objects.select_for_update().get(id=work_order_id)
        if wo.status not in (WorkOrder.Status.APPROVED, WorkOrder.Status.IN_PROGRESS):
            raise ValidationError(f"No se puede consumir en una OT con estado: {wo.status}")

        occurred_at = occurred_at or timezone.now()
        clean_notes = (notes or "").strip()

        issue = WorkOrderIssue.objects.create(
            work_order=wo,
            technician=technician,
            notes=clean_notes,
        )

        for ln in lines:
            if ln.qty <= 0:
                raise ValidationError("Cantidad inválida en consumo (debe ser > 0).")

            item_obj = Item.objects.get(id=ln.item_id)
            loc_obj = Location.objects.get(id=ln.location_id)

            reservation = None

            # 1) Si viene reserva: validar + descontar reserved
            if ln.reservation_id is not None:
                reservation = Reservation.objects.select_for_update().get(id=ln.reservation_id)

                if reservation.status != Reservation.Status.ACTIVE:
                    raise ValidationError("La reserva debe estar ACTIVE para consumir.")
                if reservation.item_id != item_obj.id:
                    raise ValidationError("La reserva no corresponde al item.")
                if reservation.location_id != loc_obj.id:
                    raise ValidationError("La reserva no corresponde a la ubicación.")
                if reservation.quantity < ln.qty:
                    raise ValidationError("La cantidad a consumir excede lo reservado.")

                snap = StockSnapshot.objects.select_for_update().get(
                    item=item_obj,
                    location=loc_obj,
                )

                if snap.reserved < ln.qty:
                    raise ValidationError("Inconsistencia: snapshot.reserved < consumo reservado.")

                snap.reserved = snap.reserved - ln.qty
                snap.save(update_fields=["reserved", "updated_at"])

                reservation.quantity = reservation.quantity - ln.qty
                if reservation.quantity == 0:
                    reservation.status = Reservation.Status.CONSUMED
                reservation.save(update_fields=["quantity", "status", "updated_at"])

                wol_reserved = WorkOrderLine.objects.select_for_update().get(id=reservation.line_id)
                if wol_reserved.qty_reserved < ln.qty:
                    raise ValidationError("Inconsistencia: WO line qty_reserved < consumo.")
                wol_reserved.qty_reserved = wol_reserved.qty_reserved - ln.qty
                wol_reserved.save(update_fields=["qty_reserved", "updated_at"])

            # 2) Movimiento OUT real
            result = self._stock.register_movement(
                item=item_obj,
                location=loc_obj,
                movement_type=InventoryMovement.MovementType.OUT,
                quantity=ln.qty,
                occurred_at=occurred_at,
                registered_by=registered_by,
                reference=f"WO:{wo.code}",
                notes=clean_notes,
            )

            mv = InventoryMovement.objects.get(id=result.movement_id)

            WorkOrderIssueLine.objects.create(
                issue=issue,
                item=item_obj,
                location=loc_obj,
                quantity=ln.qty,
                reservation=reservation,
                movement_out=mv,
            )

            wol_consumed = WorkOrderLine.objects.select_for_update().get(
                work_order_id=wo.id,
                item_id=item_obj.id,
            )
            wol_consumed.qty_consumed = wol_consumed.qty_consumed + ln.qty
            wol_consumed.save(update_fields=["qty_consumed", "updated_at"])

        if wo.status == WorkOrder.Status.APPROVED:
            wo.status = WorkOrder.Status.IN_PROGRESS
            wo.started_at = wo.started_at or timezone.now()
            wo.save(update_fields=["status", "started_at", "updated_at"])

        return issue
    
    @transaction.atomic
    def return_to_stock(
        self,
        *,
        work_order_id: int,
        technician,
        registered_by,
        occurred_at=None,
        notes: str = "",
        lines: list[ReturnLine],
    ) -> WorkOrderReturn:
        if not lines:
            raise ValidationError("No hay líneas para devolver.")

        wo = WorkOrder.objects.select_for_update().get(id=work_order_id)
        if wo.status not in (WorkOrder.Status.IN_PROGRESS, WorkOrder.Status.COMPLETED):
            raise ValidationError(f"No se puede devolver en una OT con estado: {wo.status}")

        occurred_at = occurred_at or timezone.now()
        clean_notes = (notes or "").strip()

        ret = WorkOrderReturn.objects.create(
            work_order=wo,
            technician=technician,
            notes=clean_notes,
        )

        for ln in lines:
            if ln.qty <= 0:
                raise ValidationError("Cantidad inválida en devolución (debe ser > 0).")

            item_obj = Item.objects.get(id=ln.item_id)
            loc_obj = Location.objects.get(id=ln.location_id)

            result = self._stock.register_movement(
                item=item_obj,
                location=loc_obj,
                movement_type=InventoryMovement.MovementType.IN_,
                quantity=ln.qty,
                occurred_at=occurred_at,
                registered_by=registered_by,
                reference=f"WO:{wo.code}",
                notes=clean_notes,
            )

            mv = InventoryMovement.objects.get(id=result.movement_id)

            WorkOrderReturnLine.objects.create(
                work_order_return=ret,
                item=item_obj,
                location=loc_obj,
                quantity=ln.qty,
                movement_in=mv,
            )

            wol = WorkOrderLine.objects.select_for_update().get(
                work_order_id=wo.id,
                item_id=item_obj.id,
            )
            wol.qty_returned = wol.qty_returned + ln.qty
            wol.save(update_fields=["qty_returned", "updated_at"])

        return ret



    # -------------------------
    # Utilidad: recalcular caches 
    # -------------------------
    @transaction.atomic
    def recompute_line_caches(self, work_order_id: int) -> None:
        wo = WorkOrder.objects.select_for_update().get(id=work_order_id)

        for line in wo.lines.select_for_update().all():
            reserved_active = (
                line.reservations
                .filter(status=Reservation.Status.ACTIVE)
                .aggregate(s=Sum("quantity"))["s"] or Decimal("0.000")
            )
            line.qty_reserved = reserved_active

            consumed = (
                WorkOrderIssueLine.objects
                .filter(issue__work_order_id=wo.id, item_id=line.item_id)
                .aggregate(s=Sum("quantity"))["s"] or Decimal("0.000")
            )
            line.qty_consumed = consumed

            returned = (
                WorkOrderReturnLine.objects
                .filter(work_order_return__work_order_id=wo.id, item_id=line.item_id)
                .aggregate(s=Sum("quantity"))["s"] or Decimal("0.000")
            )
            line.qty_returned = returned

            line.save(update_fields=["qty_reserved", "qty_consumed", "qty_returned", "updated_at"])



    @transaction.atomic
    def pause(self, *, work_order_id: int, user, reason: str = "") -> WorkOrder:
        wo = WorkOrder.objects.select_for_update().get(id=work_order_id)

        if wo.status != WorkOrder.Status.IN_PROGRESS:
            raise ValidationError(f"Solo puedes pausar una OT en IN_PROGRESS. Estado actual: {wo.status}")

        wo.status = WorkOrder.Status.PAUSED

        # Opcional (si tienes campos): paused_at / pause_reason
        if hasattr(wo, "paused_at"):
            wo.paused_at = getattr(wo, "paused_at") or timezone.now()
        if hasattr(wo, "pause_reason"):
            wo.pause_reason = (reason or "").strip()

        update_fields = ["status", "updated_at"]
        if hasattr(wo, "paused_at"):
            update_fields.append("paused_at")
        if hasattr(wo, "pause_reason"):
            update_fields.append("pause_reason")

        wo.save(update_fields=update_fields)
        return wo

    @transaction.atomic
    def resume(self, *, work_order_id: int, user) -> WorkOrder:
        wo = WorkOrder.objects.select_for_update().get(id=work_order_id)

        if wo.status != WorkOrder.Status.PAUSED:
            raise ValidationError(f"Solo puedes reanudar una OT en PAUSED. Estado actual: {wo.status}")

        wo.status = WorkOrder.Status.IN_PROGRESS
        wo.save(update_fields=["status", "updated_at"])
        return wo

