# workorders/services/workorder_workflow_service.py
from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from inventory.services.stock_service import StockService
from workorders.services.workorder_stock_service import WorkOrderStockService
from workorders.models import WorkOrder, Reservation, WorkOrderIssueLine


class WorkOrderWorkflowService:
    """
    Gobernanza de estados:
    - APPROVE:  DRAFT -> APPROVED
    - COMPLETE: IN_PROGRESS -> COMPLETED
    - CLOSE:    COMPLETED -> CLOSED
    - CANCEL:   DRAFT/APPROVED -> CANCELLED (libera reservas; PROHIBIDO si hubo consumos)
    - PAUSE:    IN_PROGRESS -> PAUSED
    - RESUME:   PAUSED -> IN_PROGRESS
    """

    def __init__(self, wo_stock: WorkOrderStockService | None = None):
        self._wo_stock = wo_stock or WorkOrderStockService(StockService())

    @transaction.atomic
    def approve(self, *, work_order_id: int, user) -> WorkOrder:
        wo = WorkOrder.objects.select_for_update().get(id=work_order_id)

        if wo.status != WorkOrder.Status.DRAFT:
            raise ValidationError(f"Solo se puede aprobar una OT en DRAFT. Estado actual: {wo.status}")

        if not wo.lines.exists():
            raise ValidationError("No puedes aprobar una OT sin líneas.")

        wo.status = WorkOrder.Status.APPROVED
        wo.save(update_fields=["status", "updated_at"])
        return wo

    @transaction.atomic
    def pause(self, *, work_order_id: int, user, reason: str = "") -> WorkOrder:
        wo = WorkOrder.objects.select_for_update().get(id=work_order_id)

        if wo.status != WorkOrder.Status.IN_PROGRESS:
            raise ValidationError(f"Solo se puede pausar una OT en IN_PROGRESS. Estado actual: {wo.status}")

        wo.status = WorkOrder.Status.PAUSED
        wo.save(update_fields=["status", "updated_at"])
        return wo

    @transaction.atomic
    def resume(self, *, work_order_id: int, user) -> WorkOrder:
        wo = WorkOrder.objects.select_for_update().get(id=work_order_id)

        if wo.status != WorkOrder.Status.PAUSED:
            raise ValidationError(f"Solo se puede reanudar una OT en PAUSED. Estado actual: {wo.status}")

        wo.status = WorkOrder.Status.IN_PROGRESS
        wo.save(update_fields=["status", "updated_at"])
        return wo

    @transaction.atomic
    def complete(self, *, work_order_id: int, user) -> WorkOrder:
        wo = WorkOrder.objects.select_for_update().get(id=work_order_id)

        if wo.status != WorkOrder.Status.IN_PROGRESS:
            raise ValidationError(f"Solo se puede completar una OT en IN_PROGRESS. Estado actual: {wo.status}")

        total_consumed = (
            WorkOrderIssueLine.objects
            .filter(issue__work_order_id=wo.id)
            .count()
        )
        if total_consumed == 0:
            raise ValidationError("No puedes completar una OT sin consumos registrados.")

        wo.status = WorkOrder.Status.COMPLETED
        wo.save(update_fields=["status", "updated_at"])
        return wo

    @transaction.atomic
    def close(self, *, work_order_id: int, user) -> WorkOrder:
        wo = WorkOrder.objects.select_for_update().get(id=work_order_id)

        if wo.status != WorkOrder.Status.COMPLETED:
            raise ValidationError(f"Solo se puede cerrar una OT en COMPLETED. Estado actual: {wo.status}")

        wo.status = WorkOrder.Status.CLOSED
        wo.save(update_fields=["status", "updated_at"])
        return wo

    @transaction.atomic
    def cancel(self, *, work_order_id: int, user, reason: str = "") -> WorkOrder:
        wo = WorkOrder.objects.select_for_update().get(id=work_order_id)

        if wo.status == WorkOrder.Status.CANCELLED:
            return wo

        if wo.status not in (WorkOrder.Status.DRAFT, WorkOrder.Status.APPROVED):
            raise ValidationError(f"Solo se puede cancelar una OT en DRAFT/APPROVED. Estado actual: {wo.status}")

        # VALIDACIÓN INDUSTRIAL: si hubo consumos, NO se cancela (se completa o se cierra con devolución)
        total_consumed = (
            WorkOrderIssueLine.objects
            .filter(issue__work_order_id=wo.id)
            .count()
        )
        if total_consumed > 0:
            raise ValidationError("No puedes cancelar una OT que ya tiene consumos. Completa o gestiona devoluciones.")

        # Liberar reservas activas
        active_res = (
            Reservation.objects
            .select_for_update()
            .filter(work_order_id=wo.id, status=Reservation.Status.ACTIVE)
        )

        for r in active_res:
            self._wo_stock.release_reservation(
                reservation_id=r.id,
                qty=None,
                user=user,
                reason=(reason or "Cancelación de Work Order").strip(),
            )

        wo.status = WorkOrder.Status.CANCELLED

        # solo si existen campos
        if hasattr(wo, "cancelled_at"):
            wo.cancelled_at = timezone.now()
        if hasattr(wo, "cancel_reason"):
            wo.cancel_reason = (reason or "").strip()

        update_fields = ["status", "updated_at"]
        if hasattr(wo, "cancelled_at"):
            update_fields.append("cancelled_at")
        if hasattr(wo, "cancel_reason"):
            update_fields.append("cancel_reason")

        wo.save(update_fields=update_fields)
        return wo
