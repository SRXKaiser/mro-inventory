from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from inventory.services.stock_service import StockService
from workorders.models import WorkOrder, Reservation, WorkOrderIssueLine
from workorders.services.workorder_stock_service import WorkOrderStockService


class WorkOrderWorkflowService:
    """
    Gobernanza de estados (según tus choices reales):
    - APPROVE: DRAFT -> APPROVED
    - PAUSE:   IN_PROGRESS -> PAUSED
    - RESUME:  PAUSED -> IN_PROGRESS
    - COMPLETE: IN_PROGRESS/PAUSED -> COMPLETED
    - CANCEL:  DRAFT/APPROVED/PAUSED -> CANCELLED (libera reservas; no si hubo consumos)
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

        # opcional: guardar razón si tienes campos
        if hasattr(wo, "pause_reason"):
            wo.pause_reason = (reason or "").strip()

        wo.status = WorkOrder.Status.PAUSED
        wo.save(update_fields=["status", "updated_at"] + (["pause_reason"] if hasattr(wo, "pause_reason") else []))
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

        if wo.status not in (WorkOrder.Status.IN_PROGRESS, WorkOrder.Status.PAUSED):
            raise ValidationError(f"Solo se puede completar una OT en IN_PROGRESS/PAUSED. Estado actual: {wo.status}")

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
    def cancel(self, *, work_order_id: int, user, reason: str = "") -> WorkOrder:
        wo = WorkOrder.objects.select_for_update().get(id=work_order_id)

        if wo.status == WorkOrder.Status.CANCELLED:
            return wo

        if wo.status not in (WorkOrder.Status.DRAFT, WorkOrder.Status.APPROVED, WorkOrder.Status.PAUSED):
            raise ValidationError(f"No puedes cancelar una OT en estado: {wo.status}")

        # Regla: no cancelar si ya hubo consumos
        has_consumption = WorkOrderIssueLine.objects.filter(issue__work_order_id=wo.id).exists()
        if has_consumption:
            raise ValidationError("No puedes cancelar una OT con consumos. Usa devolución/ajuste y completa.")

        # 1) liberar reservas activas
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

        # 2) cambiar estado
        wo.status = WorkOrder.Status.CANCELLED

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
