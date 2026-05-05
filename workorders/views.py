# workorders/views.py
from __future__ import annotations

from decimal import Decimal
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from inventory.services.stock_service import StockService
from workorders.forms import (
    WorkOrderCreateForm,
    WorkOrderLineCreateForm,
    ReserveForm,
    ConsumeForm,
    ReturnForm,
)
from workorders.models import WorkOrder
from workorders.permissions import can_manage_workorders, can_operate_inventory
from workorders.services.workorder_stock_service import (
    WorkOrderStockService,
    ConsumeLine,
    ReturnLine,
)
from workorders.services.workorder_workflow_service import WorkOrderWorkflowService


logger = logging.getLogger(__name__)


# -------------------------
# Helpers
# -------------------------

def _svc() -> WorkOrderStockService:
    return WorkOrderStockService(StockService())


def _wf() -> WorkOrderWorkflowService:
    """
    Importante: el workflow debe usar el servicio de stock de WO,
    porque cancel() libera reservas, etc.
    """
    stock = StockService()
    wo_stock = WorkOrderStockService(stock)
    return WorkOrderWorkflowService(wo_stock)


def _format_validation_error(ex: ValidationError) -> str:
    """
    Django ValidationError puede venir como:
    - mensaje string
    - lista ex.messages
    - dict ex.message_dict
    """
    if hasattr(ex, "message_dict") and ex.message_dict:
        parts = []
        for field, errors in ex.message_dict.items():
            label = str(field).replace("_", " ").capitalize()

            if isinstance(errors, (list, tuple)):
                for error in errors:
                    parts.append(f"{label}: {error}")
            else:
                parts.append(f"{label}: {errors}")

        return " | ".join(parts)

    if hasattr(ex, "messages") and ex.messages:
        return " | ".join(str(message) for message in ex.messages)

    return str(ex)


def _add_form_warning(request, form, message: str = "Revisa los campos marcados antes de continuar."):
    """
    Muestra un mensaje general sin imprimir form.errors crudo.
    Los detalles deben mostrarse en el template campo por campo.
    """
    if form.non_field_errors():
        messages.warning(request, _format_validation_error(ValidationError(form.non_field_errors())))
    else:
        messages.warning(request, message)


def _add_validation_error(request, prefix: str, ex: ValidationError):
    messages.error(request, f"{prefix}: {_format_validation_error(ex)}")


def _add_unexpected_error(request, ex: Exception, *, public_message: str):
    """
    Nunca muestra el detalle técnico de la excepción al usuario.
    El detalle completo queda registrado en logs del servidor.
    """
    logger.exception(public_message, exc_info=ex)
    messages.error(
        request,
        f"{public_message}. Si el problema continúa, revisa el log del servidor o contacta al administrador.",
    )


def _deny(request, msg: str, pk: int):
    messages.error(request, msg)
    return redirect("workorders:detail", pk=pk)


# -------------------------
# Listado
# -------------------------
@login_required
def workorder_list(request):
    qs = (
        WorkOrder.objects
        .select_related("requested_by", "assigned_to")
        .order_by("-created_at")
    )

    status = (request.GET.get("status") or "").strip()
    q = (request.GET.get("q") or "").strip()

    if status:
        qs = qs.filter(status=status)
    if q:
        qs = qs.filter(code__icontains=q)

    return render(
        request,
        "workorders/workorder_list.html",
        {
            "workorders": qs,
            "status": status,
            "q": q,
            "status_choices": WorkOrder.Status.choices,
        },
    )


# -------------------------
# Detalle
# -------------------------
@login_required
def workorder_detail(request, pk: int):
    wo = get_object_or_404(
        WorkOrder.objects.prefetch_related("lines__item", "reservations__item", "issues", "returns"),
        pk=pk,
    )

    can_manage_wo = can_manage_workorders(request.user)
    can_operate_wo = can_operate_inventory(request.user)

    can_issue = can_operate_wo and wo.status in (
        WorkOrder.Status.APPROVED,
        WorkOrder.Status.IN_PROGRESS,
        WorkOrder.Status.PAUSED,
    )

    can_return = can_operate_wo and wo.status in (
        WorkOrder.Status.IN_PROGRESS,
        WorkOrder.Status.PAUSED,
        WorkOrder.Status.COMPLETED,
    )

    can_reserve = can_operate_wo and wo.status not in (
        WorkOrder.Status.COMPLETED,
        WorkOrder.Status.CANCELLED,
    )

    reserve_form = ReserveForm(work_order=wo)
    consume_form = ConsumeForm(work_order=wo)
    return_form = ReturnForm(work_order=wo)

    return render(request, "workorders/workorder_detail.html", {
        "wo": wo,
        "reserve_form": reserve_form,
        "consume_form": consume_form,
        "return_form": return_form,

        "can_manage_wo": can_manage_wo,
        "can_operate_wo": can_operate_wo,
        "can_issue": can_issue,
        "can_return": can_return,
        "can_reserve": can_reserve,
    })


# -------------------------
# Reservar
# -------------------------
@login_required
def workorder_reserve(request, pk: int):
    if not can_operate_inventory(request.user):
        messages.error(request, "No tienes permisos para operar inventario en Work Orders.")
        return redirect("workorders:detail", pk=pk)

    wo = get_object_or_404(WorkOrder, pk=pk)

    if request.method != "POST":
        return redirect("workorders:detail", pk=pk)

    form = ReserveForm(request.POST, work_order=wo)
    if not form.is_valid():
        _add_form_warning(request, form, "Formulario de reserva inválido.")
        return redirect("workorders:detail", pk=pk)

    svc = _svc()

    line = form.cleaned_data["line"]
    location = form.cleaned_data["location"]
    qty = form.cleaned_data["qty"]
    reason = (form.cleaned_data.get("reason") or "").strip()

    try:
        svc.reserve(
            line_id=line.id,
            qty=qty,
            user=request.user,
            location_id=location.id,
            reason=reason,
        )
        messages.success(request, "Reserva creada correctamente.")
    except ValidationError as ex:
        _add_validation_error(request, "No se pudo reservar", ex)
    except Exception as ex:
        _add_unexpected_error(request, ex, public_message="No se pudo reservar")

    return redirect("workorders:detail", pk=pk)


# -------------------------
# Liberar reserva
# (qty opcional: si viene vacío libera todo)
# -------------------------
@login_required
def workorder_release_reservation(request, pk: int):
    _ = get_object_or_404(WorkOrder, pk=pk)

    if request.method != "POST":
        return redirect("workorders:detail", pk=pk)

    reservation_id = request.POST.get("reservation_id")
    qty_raw = (request.POST.get("qty") or "").strip()
    reason = (request.POST.get("reason") or "Liberación manual").strip()

    if not reservation_id:
        messages.error(request, "Falta reservation_id.")
        return redirect("workorders:detail", pk=pk)

    qty = None
    if qty_raw:
        try:
            qty = Decimal(qty_raw)
        except Exception as ex:
            logger.warning("Cantidad inválida para liberar reserva.", exc_info=ex)
            messages.error(request, "Cantidad inválida para liberar.")
            return redirect("workorders:detail", pk=pk)

    svc = _svc()

    try:
        svc.release_reservation(
            reservation_id=int(reservation_id),
            qty=qty,
            user=request.user,
            reason=reason,
        )
        messages.success(request, "Reserva liberada correctamente.")
    except ValidationError as ex:
        _add_validation_error(request, "No se pudo liberar", ex)
    except Exception as ex:
        _add_unexpected_error(request, ex, public_message="No se pudo liberar la reserva")

    return redirect("workorders:detail", pk=pk)


# -------------------------
# Issue (consumo OUT)
# -------------------------
@login_required
def workorder_issue(request, pk: int):
    if not can_operate_inventory(request.user):
        messages.error(request, "No tienes permisos para operar inventario en Work Orders.")
        return redirect("workorders:detail", pk=pk)

    wo = get_object_or_404(WorkOrder, pk=pk)

    if request.method != "POST":
        return redirect("workorders:detail", pk=pk)

    form = ConsumeForm(request.POST, work_order=wo)
    if not form.is_valid():
        _add_form_warning(request, form, "Formulario de consumo inválido.")
        return redirect("workorders:detail", pk=pk)

    svc = _svc()

    item = form.cleaned_data["item"]
    location = form.cleaned_data["location"]
    qty = form.cleaned_data["qty"]
    reservation = form.cleaned_data.get("reservation")
    notes = (form.cleaned_data.get("notes") or "").strip()

    try:
        svc.consume(
            work_order_id=wo.id,
            technician=request.user,
            registered_by=request.user,
            occurred_at=timezone.now(),
            notes=notes,
            lines=[
                ConsumeLine(
                    item_id=item.id,
                    location_id=location.id,
                    qty=qty,
                    reservation_id=reservation.id if reservation else None,
                )
            ],
        )
        messages.success(request, "Consumo registrado correctamente.")
    except ValidationError as ex:
        _add_validation_error(request, "No se pudo consumir", ex)
    except Exception as ex:
        _add_unexpected_error(request, ex, public_message="No se pudo registrar el consumo")

    return redirect("workorders:detail", pk=pk)


# -------------------------
# Return (devolución IN)
# -------------------------
@login_required
def workorder_return(request, pk: int):
    if not can_operate_inventory(request.user):
        messages.error(request, "No tienes permisos para operar inventario en Work Orders.")
        return redirect("workorders:detail", pk=pk)

    wo = get_object_or_404(WorkOrder, pk=pk)

    if request.method != "POST":
        return redirect("workorders:detail", pk=pk)

    form = ReturnForm(request.POST, work_order=wo)
    if not form.is_valid():
        _add_form_warning(request, form, "Formulario de devolución inválido.")
        return redirect("workorders:detail", pk=pk)

    svc = _svc()

    item = form.cleaned_data["item"]
    location = form.cleaned_data["location"]
    qty = form.cleaned_data["qty"]
    notes = (form.cleaned_data.get("notes") or "").strip()

    try:
        svc.return_to_stock(
            work_order_id=wo.id,
            technician=request.user,
            registered_by=request.user,
            occurred_at=timezone.now(),
            notes=notes,
            lines=[
                ReturnLine(
                    item_id=item.id,
                    location_id=location.id,
                    qty=qty,
                )
            ],
        )
        messages.success(request, "Devolución registrada correctamente.")
    except ValidationError as ex:
        _add_validation_error(request, "No se pudo devolver", ex)
    except Exception as ex:
        _add_unexpected_error(request, ex, public_message="No se pudo registrar la devolución")

    return redirect("workorders:detail", pk=pk)


@login_required
def workorder_reserve_page(request, pk: int):
    wo = get_object_or_404(WorkOrder.objects.prefetch_related("lines__item"), pk=pk)
    reserve_form = ReserveForm(work_order=wo)
    return render(request, "workorders/workorder_reserve.html", {"wo": wo, "reserve_form": reserve_form})


@login_required
def workorder_issue_page(request, pk: int):
    wo = get_object_or_404(WorkOrder.objects.prefetch_related("lines__item"), pk=pk)
    consume_form = ConsumeForm(work_order=wo)
    return render(request, "workorders/workorder_issue.html", {"wo": wo, "consume_form": consume_form})


@login_required
@transaction.atomic
def workorder_create(request):
    if request.method == "POST":
        form = WorkOrderCreateForm(request.POST)
        if form.is_valid():
            try:
                wo = form.save(commit=False)
                wo.requested_by = request.user

                # si no mandan code, autogenera
                if not (wo.code or "").strip():
                    wo.code = f"WO-{timezone.now().strftime('%Y%m%d%H%M%S')}"

                wo.status = WorkOrder.Status.DRAFT
                wo.save()
                messages.success(request, "Work Order creada.")
                return redirect("workorders:detail", pk=wo.id)
            except ValidationError as ex:
                _add_validation_error(request, "No se pudo crear la Work Order", ex)
            except Exception as ex:
                _add_unexpected_error(request, ex, public_message="No se pudo crear la Work Order")
        else:
            _add_form_warning(request, form, "Revisa el formulario.")

    else:
        form = WorkOrderCreateForm()

    return render(request, "workorders/workorder_create.html", {"form": form})


@login_required
@transaction.atomic
def workorder_line_create(request, pk: int):
    wo = get_object_or_404(WorkOrder.objects.select_for_update(), pk=pk)

    if wo.status not in (WorkOrder.Status.DRAFT, WorkOrder.Status.APPROVED, WorkOrder.Status.IN_PROGRESS):
        messages.error(request, f"No puedes agregar líneas en una OT con estado: {wo.status}")
        return redirect("workorders:detail", pk=pk)

    if request.method == "POST":
        form = WorkOrderLineCreateForm(request.POST)
        if form.is_valid():
            try:
                line = form.save(commit=False)
                line.work_order = wo
                line.save()
                messages.success(request, "Línea agregada.")
                return redirect("workorders:detail", pk=pk)
            except ValidationError as ex:
                _add_validation_error(request, "No se pudo agregar la línea", ex)
            except Exception as ex:
                _add_unexpected_error(request, ex, public_message="No se pudo agregar la línea")
        else:
            _add_form_warning(request, form, "Revisa el formulario.")

    else:
        form = WorkOrderLineCreateForm()

    return render(request, "workorders/workorder_line_create.html", {"wo": wo, "form": form})


# -------------------------
# APPROVE: DRAFT -> APPROVED
# -------------------------
@require_POST
@login_required
def workorder_approve(request, pk: int):
    if not can_manage_workorders(request.user):
        return _deny(request, "No tienes permisos para aprobar Work Orders.", pk)

    try:
        _wf().approve(work_order_id=pk, user=request.user)
        messages.success(request, "Work Order aprobada.")
    except ValidationError as ex:
        _add_validation_error(request, "No se pudo aprobar", ex)
    except Exception as ex:
        _add_unexpected_error(request, ex, public_message="No se pudo aprobar la Work Order")

    return redirect("workorders:detail", pk=pk)


# -------------------------
# COMPLETE: IN_PROGRESS -> COMPLETED
# -------------------------
@require_POST
@login_required
def workorder_complete(request, pk: int):
    if not can_manage_workorders(request.user):
        return _deny(request, "No tienes permisos para completar Work Orders.", pk)

    try:
        _wf().complete(work_order_id=pk, user=request.user)
        messages.success(request, "Work Order completada.")
    except ValidationError as ex:
        _add_validation_error(request, "No se pudo completar", ex)
    except Exception as ex:
        _add_unexpected_error(request, ex, public_message="No se pudo completar la Work Order")

    return redirect("workorders:detail", pk=pk)


# -------------------------
# CLOSE: COMPLETED -> CLOSED
# -------------------------
@require_POST
@login_required
def workorder_close(request, pk: int):
    if not can_manage_workorders(request.user):
        return _deny(request, "No tienes permisos para cerrar Work Orders.", pk)

    try:
        _wf().close(work_order_id=pk, user=request.user)
        messages.success(request, "Work Order cerrada.")
    except ValidationError as ex:
        _add_validation_error(request, "No se pudo cerrar", ex)
    except Exception as ex:
        _add_unexpected_error(request, ex, public_message="No se pudo cerrar la Work Order")

    return redirect("workorders:detail", pk=pk)


# -------------------------
# CANCEL: DRAFT/APPROVED -> CANCELLED (libera reservas)
# -------------------------
@require_POST
@login_required
def workorder_cancel(request, pk: int):
    if not can_manage_workorders(request.user):
        return _deny(request, "No tienes permisos para cancelar Work Orders.", pk)

    reason = (request.POST.get("reason") or "").strip()

    try:
        _wf().cancel(work_order_id=pk, user=request.user, reason=reason)
        messages.success(request, "Work Order cancelada. Reservas liberadas.")
    except ValidationError as ex:
        _add_validation_error(request, "No se pudo cancelar", ex)
    except Exception as ex:
        _add_unexpected_error(request, ex, public_message="No se pudo cancelar la Work Order")

    return redirect("workorders:detail", pk=pk)


# -------------------------
# PAUSE: IN_PROGRESS -> PAUSED (o APPROVED -> PAUSED, según tu regla)
# -------------------------
@require_POST
@login_required
def workorder_pause(request, pk: int):
    if not can_manage_workorders(request.user):
        return _deny(request, "No tienes permisos para pausar Work Orders.", pk)

    reason = (request.POST.get("reason") or "").strip()

    try:
        _wf().pause(work_order_id=pk, user=request.user, reason=reason)
        messages.success(request, "Work Order pausada.")
    except ValidationError as ex:
        _add_validation_error(request, "No se pudo pausar", ex)
    except Exception as ex:
        _add_unexpected_error(request, ex, public_message="No se pudo pausar la Work Order")

    return redirect("workorders:detail", pk=pk)


# -------------------------
# RESUME: PAUSED -> IN_PROGRESS
# -------------------------
@require_POST
@login_required
def workorder_resume(request, pk: int):
    if not can_manage_workorders(request.user):
        return _deny(request, "No tienes permisos para reanudar Work Orders.", pk)

    try:
        _wf().resume(work_order_id=pk, user=request.user)
        messages.success(request, "Work Order reanudada.")
    except ValidationError as ex:
        _add_validation_error(request, "No se pudo reanudar", ex)
    except Exception as ex:
        _add_unexpected_error(request, ex, public_message="No se pudo reanudar la Work Order")

    return redirect("workorders:detail", pk=pk)
