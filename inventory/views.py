from decimal import Decimal
import csv
import logging
from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import (
    F,
    Q,
    Sum,
    Value,
    Case,
    When,
    IntegerField,
    DecimalField,
    ExpressionWrapper,
)
from django.db.models.functions import TruncDate, Coalesce
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from locations.models import Warehouse, Location
from .forms import ItemForm, MovementForm
from .forms_extra import (
    TransferForm,
    AdjustmentForm,
    VoidMovementForm,
    CycleCountForm,
    ReservationForm,
    ReserveForm,
    ReleaseForm,
)
from .models import InventoryMovement, Item, StockSnapshot
from .services.stock_service import StockService

logger = logging.getLogger(__name__)

try:
    from workorders.models import WorkOrder
    HAS_WO = True
except Exception:
    HAS_WO = False


# ============================================================
# Helpers generales
# ============================================================

def _get_default_location():
    wh, _ = Warehouse.objects.get_or_create(
        code="DEFAULT",
        defaults={"name": "Almacén Default"},
    )
    loc, _ = Location.objects.get_or_create(
        warehouse=wh,
        code="DEFAULT",
        defaults={"name": "Ubicación Default"},
    )
    return loc


def _perm_flags(user):
    if not user.is_authenticated:
        return {"can_transfer": False, "can_adjust": False, "can_export": False}

    if user.is_superuser:
        return {"can_transfer": True, "can_adjust": True, "can_export": True}

    return {
        "can_transfer": user.groups.filter(name="inventory_operator").exists(),
        "can_adjust": user.groups.filter(name="inventory_supervisor").exists(),
        "can_export": user.groups.filter(name="inventory_admin").exists(),
    }


def _require_group(user, group_name: str):
    if not user.is_authenticated:
        raise PermissionDenied("No autenticado.")
    if user.is_superuser:
        return
    if not user.groups.filter(name=group_name).exists():
        raise PermissionDenied(f"Permiso requerido: {group_name}")


def _format_validation_error(ex: ValidationError) -> str:
    """
    Convierte ValidationError de Django a texto limpio para usuario.

    Django puede entregar:
    - ex.message_dict
    - ex.messages
    - string simple
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
        return " | ".join(str(m) for m in ex.messages)

    return str(ex)


def _add_form_warning(request, form):
    """
    Mensaje general para formularios inválidos.
    Los errores específicos siguen viviendo en form.errors dentro del HTML.
    """
    if form.non_field_errors():
        messages.warning(request, _format_validation_error(ValidationError(form.non_field_errors())))
    else:
        messages.warning(request, "Revisa los campos marcados antes de continuar.")


def _add_validation_error(request, prefix: str, ex: ValidationError):
    messages.error(request, f"{prefix}: {_format_validation_error(ex)}")


def _add_unexpected_error(request, ex: Exception, *, public_message: str):
    """
    Nunca muestra el detalle técnico al usuario.
    El detalle queda en logs del servidor.
    """
    logger.exception(public_message, exc_info=ex)
    messages.error(
        request,
        f"{public_message}. Si el problema continúa, revisa el log del servidor o contacta al administrador.",
    )


def _ctx_with_perms(request, base: dict | None = None) -> dict:
    ctx = base or {}
    ctx.update(_perm_flags(request.user))
    return ctx


# ============================================================
# Dashboard
# ============================================================

@login_required
def dashboard(request):
    today = timezone.localdate()

    warehouse_id = (request.GET.get("warehouse_id") or "").strip()

    warehouses = Warehouse.objects.order_by("code")

    snap_qs = StockSnapshot.objects.all()
    mv_qs = InventoryMovement.objects.all()

    if warehouse_id:
        snap_qs = snap_qs.filter(location__warehouse_id=warehouse_id)
        mv_qs = mv_qs.filter(location__warehouse_id=warehouse_id)

    DEC_QTY = DecimalField(max_digits=18, decimal_places=3)

    low_stock = (
        snap_qs
        .select_related("item", "location", "location__warehouse")
        .annotate(
            reserved0=Coalesce(
                "reserved",
                Value(Decimal("0.000")),
                output_field=DEC_QTY,
            ),
        )
        .annotate(
            available_calc=ExpressionWrapper(
                F("on_hand") - F("reserved0"),
                output_field=DEC_QTY,
            )
        )
        .filter(available_calc__lte=F("item__min_stock"))
        .annotate(
            severity=Case(
                When(available_calc__lte=0, then=Value(3)),
                When(reserved0__gt=0, then=Value(2)),
                default=Value(1),
                output_field=IntegerField(),
            )
        )
        .order_by(
            "-severity",
            "item__sku",
            "location__warehouse__code",
            "location__code",
        )
    )

    kpi_critical_count = low_stock.filter(severity=3).count()
    kpi_high_count = low_stock.filter(severity=2).count()
    kpi_medium_count = low_stock.filter(severity=1).count()

    inv_totals = snap_qs.aggregate(
        total_on_hand=Coalesce(Sum("on_hand"), Decimal("0.000")),
        total_reserved=Coalesce(Sum("reserved"), Decimal("0.000")),
    )
    total_on_hand = inv_totals["total_on_hand"]
    total_reserved = inv_totals["total_reserved"]
    total_available = total_on_hand - total_reserved

    low_count = low_stock.count()

    movements_today = mv_qs.filter(
        occurred_at__date=today,
        is_void=False,
    ).count()

    recent_movements = (
        mv_qs
        .select_related("item", "location", "location__warehouse", "registered_by")
        .order_by("-occurred_at", "-id")[:10]
    )

    since_7d = timezone.now() - timedelta(days=7)
    kpi_out_7d = mv_qs.filter(
        movement_type="OUT",
        is_void=False,
        occurred_at__gte=since_7d,
    ).aggregate(
        total=Coalesce(Sum("quantity"), Decimal("0.000"))
    )["total"]

    kpi_stockout_risk = snap_qs.filter(
        on_hand__lte=F("item__min_stock"),
        reserved__gt=0,
    ).count()

    days = 14
    start_date = today - timedelta(days=days - 1)

    mv_by_day = (
        mv_qs
        .filter(is_void=False, occurred_at__date__gte=start_date, occurred_at__date__lte=today)
        .annotate(day=TruncDate("occurred_at"))
        .values("day")
        .annotate(
            in_cnt=Coalesce(
                Sum(Case(
                    When(movement_type="IN", then=Value(1)),
                    default=Value(0),
                    output_field=IntegerField(),
                )),
                0,
            ),
            out_cnt=Coalesce(
                Sum(Case(
                    When(movement_type="OUT", then=Value(1)),
                    default=Value(0),
                    output_field=IntegerField(),
                )),
                0,
            ),
            adj_cnt=Coalesce(
                Sum(Case(
                    When(movement_type="ADJ", then=Value(1)),
                    default=Value(0),
                    output_field=IntegerField(),
                )),
                0,
            ),
        )
        .order_by("day")
    )

    mv_map = {
        r["day"].isoformat(): {
            "IN": int(r["in_cnt"]),
            "OUT": int(r["out_cnt"]),
            "ADJ": int(r["adj_cnt"]),
        }
        for r in mv_by_day
    }

    mv_labels = [(start_date + timedelta(days=i)).isoformat() for i in range(days)]
    mv_in = [mv_map.get(d, {}).get("IN", 0) for d in mv_labels]
    mv_out = [mv_map.get(d, {}).get("OUT", 0) for d in mv_labels]
    mv_adj = [mv_map.get(d, {}).get("ADJ", 0) for d in mv_labels]

    since_30d = timezone.now() - timedelta(days=30)
    top_out = (
        mv_qs
        .filter(movement_type="OUT", is_void=False, occurred_at__gte=since_30d)
        .values("item__sku")
        .annotate(total=Coalesce(Sum("quantity"), Decimal("0.000")))
        .order_by("-total")[:10]
    )
    top_out_labels = [r["item__sku"] for r in top_out]
    top_out_values = [float(r["total"]) for r in top_out]

    wh_raw = (
        snap_qs
        .filter(location__isnull=False)
        .values("location__warehouse__code")
        .annotate(total=Coalesce(Sum("on_hand"), Decimal("0.000")))
        .order_by("location__warehouse__code")
    )
    wh_labels = [r["location__warehouse__code"] for r in wh_raw]
    wh_values = [float(r["total"]) for r in wh_raw]

    wo_open = 0
    wo_paused = 0
    recent_wos = []
    if HAS_WO:
        wo_open = WorkOrder.objects.exclude(
            status__in=[WorkOrder.Status.COMPLETED, WorkOrder.Status.CANCELLED]
        ).count()

        wo_paused = WorkOrder.objects.filter(status=WorkOrder.Status.PAUSED).count()

        recent_wos = (
            WorkOrder.objects
            .select_related("requested_by", "assigned_to")
            .order_by("-created_at")[:8]
        )

    ctx = {
        "warehouses": warehouses,
        "filter_warehouse_id": warehouse_id,
        "low_stock": low_stock,
        "kpi_low_count": low_count,
        "kpi_movements_today": movements_today,
        "kpi_total_on_hand": total_on_hand,
        "kpi_total_reserved": total_reserved,
        "kpi_total_available": total_available,
        "kpi_out_7d": kpi_out_7d,
        "kpi_stockout_risk": kpi_stockout_risk,
        "kpi_critical_count": kpi_critical_count,
        "kpi_high_count": kpi_high_count,
        "kpi_medium_count": kpi_medium_count,
        "kpi_wo_open": wo_open,
        "kpi_wo_paused": wo_paused,
        "has_wo": HAS_WO,
        "recent_movements": recent_movements,
        "recent_wos": recent_wos,
        "mv_labels": mv_labels,
        "mv_in": mv_in,
        "mv_out": mv_out,
        "mv_adj": mv_adj,
        "top_out_labels": top_out_labels,
        "top_out_values": top_out_values,
        "wh_labels": wh_labels,
        "wh_values": wh_values,
    }

    ctx.update(_perm_flags(request.user))
    return render(request, "dashboard.html", ctx)


# ============================================================
# Items
# ============================================================

@login_required
def item_list(request):
    q = (request.GET.get("q") or "").strip()
    qs = Item.objects.select_related("item_type", "criticality", "uom").order_by("sku")

    if q:
        qs = qs.filter(Q(sku__icontains=q) | Q(name__icontains=q))

    return render(request, "inventory/item_list.html", {"items": qs, "q": q})


@login_required
def item_create(request):
    if request.method == "POST":
        form = ItemForm(request.POST)
        if form.is_valid():
            try:
                item = form.save()
                messages.success(request, f"Artículo creado: {item.sku}")
                return redirect("item_detail", item.id)
            except ValidationError as ex:
                _add_validation_error(request, "No se pudo crear el artículo", ex)
            except Exception as ex:
                _add_unexpected_error(request, ex, public_message="No se pudo crear el artículo")
        else:
            _add_form_warning(request, form)
    else:
        form = ItemForm()

    return render(request, "inventory/item_form.html", {"form": form, "title": "Nuevo artículo"})


@login_required
def item_edit(request, item_id: int):
    item = get_object_or_404(Item, pk=item_id)

    if request.method == "POST":
        form = ItemForm(request.POST, instance=item)
        if form.is_valid():
            try:
                item = form.save()
                messages.success(request, f"Artículo actualizado: {item.sku}")
                return redirect("item_detail", item.id)
            except ValidationError as ex:
                _add_validation_error(request, "No se pudo actualizar el artículo", ex)
            except Exception as ex:
                _add_unexpected_error(request, ex, public_message="No se pudo actualizar el artículo")
        else:
            _add_form_warning(request, form)
    else:
        form = ItemForm(instance=item)

    return render(request, "inventory/item_form.html", {"form": form, "title": f"Editar {item.sku}"})


@login_required
def item_detail(request, item_id: int):
    item = get_object_or_404(
        Item.objects.select_related("item_type", "criticality", "uom"),
        pk=item_id,
    )

    stock_by_location = (
        StockSnapshot.objects
        .select_related("location", "location__warehouse")
        .filter(item=item)
        .order_by("location__warehouse__code", "location__code")
    )

    total_stock = sum((s.on_hand for s in stock_by_location), start=Decimal("0.000"))

    movement_type = (request.GET.get("type") or "").strip().upper()
    location_id = (request.GET.get("location") or "").strip()
    date_from = (request.GET.get("from") or "").strip()
    date_to = (request.GET.get("to") or "").strip()
    show_void = (request.GET.get("void") or "").strip()

    movements_qs = (
        InventoryMovement.objects
        .select_related("registered_by", "location", "location__warehouse")
        .filter(item=item)
        .order_by("-occurred_at", "-id")
    )

    if movement_type in {"IN", "OUT", "ADJ"}:
        movements_qs = movements_qs.filter(movement_type=movement_type)

    if location_id.isdigit():
        movements_qs = movements_qs.filter(location_id=int(location_id))

    if date_from:
        movements_qs = movements_qs.filter(occurred_at__date__gte=date_from)
    if date_to:
        movements_qs = movements_qs.filter(occurred_at__date__lte=date_to)

    if show_void != "1":
        movements_qs = movements_qs.filter(is_void=False)

    paginator = Paginator(movements_qs, 50)
    page_number = request.GET.get("page") or 1
    movements_page = paginator.get_page(page_number)

    location_options = (
        StockSnapshot.objects
        .select_related("location", "location__warehouse")
        .filter(item=item, location__isnull=False)
        .values("location_id", "location__warehouse__code", "location__code")
        .distinct()
        .order_by("location__warehouse__code", "location__code")
    )

    ctx = {
        "item": item,
        "stock_by_location": stock_by_location,
        "total_stock": total_stock,
        "movements": movements_page,
        "paginator": paginator,
        "filter_type": movement_type,
        "filter_location": location_id,
        "filter_from": date_from,
        "filter_to": date_to,
        "filter_void": show_void,
        "location_options": location_options,
    }
    ctx.update(_perm_flags(request.user))
    return render(request, "inventory/item_detail.html", ctx)


# ============================================================
# Movimientos y consultas AJAX
# ============================================================

@login_required
def movement_create(request):
    if request.method == "POST":
        form = MovementForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            try:
                result = StockService.register_movement(
                    item=cd["item"],
                    location=cd["location"],
                    movement_type=cd["movement_type"],
                    quantity=cd["quantity"],
                    registered_by=request.user,
                    reference=cd.get("reference") or "",
                    notes=cd.get("notes") or "",
                )
            except ValidationError as ex:
                _add_validation_error(request, "No se pudo registrar el movimiento", ex)
                return render(request, "inventory/movement_form.html", {"form": form})
            except Exception as ex:
                _add_unexpected_error(request, ex, public_message="No se pudo registrar el movimiento")
                return render(request, "inventory/movement_form.html", {"form": form})

            messages.success(
                request,
                f"Movimiento registrado en {cd['location']}. Stock actual: {result.new_on_hand}",
            )
            return redirect("item_detail", cd["item"].id)
        else:
            _add_form_warning(request, form)

    initial = {}
    if request.GET.get("item_id"):
        initial["item"] = request.GET.get("item_id")
    if request.GET.get("location_id"):
        initial["location"] = request.GET.get("location_id")

    form = form if request.method == "POST" else MovementForm(initial=initial)
    return render(request, "inventory/movement_form.html", {"form": form})


@login_required
def locations_by_warehouse(request):
    warehouse_id = request.GET.get("warehouse_id")
    if not warehouse_id:
        return JsonResponse([], safe=False)

    qs = (
        Location.objects
        .filter(warehouse_id=warehouse_id)
        .order_by("code")
        .values("id", "name", "code")
    )
    return JsonResponse(list(qs), safe=False)


@login_required
def stock_by_item_location(request):
    item_id = request.GET.get("item_id")
    location_id = request.GET.get("location_id")

    if not item_id or not location_id:
        return JsonResponse({"on_hand": None, "reserved": None, "available": None, "min_stock": None})

    try:
        snap = StockSnapshot.objects.select_related("item").get(
            item_id=item_id,
            location_id=location_id,
        )
        on_hand = snap.on_hand
        reserved = getattr(snap, "reserved", Decimal("0.000"))
        available = on_hand - reserved

        return JsonResponse({
            "on_hand": str(on_hand),
            "reserved": str(reserved),
            "available": str(available),
            "min_stock": str(snap.item.min_stock),
        })
    except StockSnapshot.DoesNotExist:
        return JsonResponse({
            "on_hand": "0.000",
            "reserved": "0.000",
            "available": "0.000",
            "min_stock": None,
        })


# ============================================================
# Operaciones especiales
# ============================================================

@login_required
def transfer_create(request):
    _require_group(request.user, "inventory_operator")

    if request.method == "POST":
        form = TransferForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            try:
                result = StockService.transfer(
                    item=cd["item"],
                    from_location=cd["from_location"],
                    to_location=cd["to_location"],
                    quantity=cd["quantity"],
                    registered_by=request.user,
                    reference=cd.get("reference") or "",
                    notes=cd.get("notes") or "",
                    occurred_at=timezone.now(),
                )
            except ValidationError as ex:
                _add_validation_error(request, "No se pudo transferir", ex)
                return render(request, "inventory/transfer_form.html", {"form": form})
            except Exception as ex:
                _add_unexpected_error(request, ex, public_message="No se pudo transferir")
                return render(request, "inventory/transfer_form.html", {"form": form})

            messages.success(
                request,
                f"Transferencia OK. Origen nuevo: {result.from_new_on_hand}, Destino nuevo: {result.to_new_on_hand}",
            )
            return redirect("item_detail", cd["item"].id)
        else:
            _add_form_warning(request, form)

    else:
        initial = {}

        item_id = (request.GET.get("item_id") or "").strip()
        from_location_id = (request.GET.get("from_location_id") or "").strip()

        if item_id.isdigit():
            initial["item"] = int(item_id)

        if from_location_id.isdigit():
            initial["from_location"] = int(from_location_id)

        form = TransferForm(initial=initial)

    return render(request, "inventory/transfer_form.html", {"form": form})


@login_required
def adjustment_create(request):
    _require_group(request.user, "inventory_supervisor")

    if request.method == "POST":
        form = AdjustmentForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            try:
                if cd["mode"] == AdjustmentForm.MODE_DELTA:
                    result = StockService.adjust_delta(
                        item=cd["item"],
                        location=cd["location"],
                        delta=cd["delta"],
                        registered_by=request.user,
                        reason=cd["reason"],
                        reference=cd.get("reference") or "",
                        notes=cd.get("notes") or "",
                    )
                else:
                    result = StockService.adjust_set(
                        item=cd["item"],
                        location=cd["location"],
                        new_on_hand=cd["new_on_hand"],
                        registered_by=request.user,
                        reason=cd["reason"],
                        reference=cd.get("reference") or "",
                        notes=cd.get("notes") or "",
                    )
            except ValidationError as ex:
                _add_validation_error(request, "No se pudo aplicar el ajuste", ex)
                return render(request, "inventory/adjustment_form.html", {"form": form})
            except Exception as ex:
                _add_unexpected_error(request, ex, public_message="No se pudo aplicar el ajuste")
                return render(request, "inventory/adjustment_form.html", {"form": form})

            messages.success(request, f"Ajuste OK. Stock actual: {result.new_on_hand}")
            return redirect("item_detail", cd["item"].id)
        else:
            _add_form_warning(request, form)

    form = form if request.method == "POST" else AdjustmentForm()
    return render(request, "inventory/adjustment_form.html", {"form": form})


@login_required
def cycle_count(request):
    _require_group(request.user, "inventory_supervisor")

    if request.method == "POST":
        form = CycleCountForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            try:
                result = StockService.adjust_set(
                    item=cd["item"],
                    location=cd["location"],
                    new_on_hand=cd["counted_qty"],
                    registered_by=request.user,
                    reason="CONTEO FÍSICO",
                    reference=cd.get("reference") or "CYCLE COUNT",
                    notes=cd.get("notes") or "",
                )
            except ValidationError as ex:
                _add_validation_error(request, "No se pudo aplicar el conteo", ex)
                return render(request, "inventory/cycle_count.html", {"form": form})
            except Exception as ex:
                _add_unexpected_error(request, ex, public_message="No se pudo aplicar el conteo")
                return render(request, "inventory/cycle_count.html", {"form": form})

            messages.success(request, f"Conteo aplicado. Nuevo stock: {result.new_on_hand}")
            return redirect("item_detail", cd["item"].id)
        else:
            _add_form_warning(request, form)

    form = form if request.method == "POST" else CycleCountForm()
    return render(request, "inventory/cycle_count.html", {"form": form})


# ============================================================
# Exportaciones CSV
# ============================================================

@login_required
def export_movements_csv(request):
    _require_group(request.user, "inventory_admin")

    item_id = request.GET.get("item_id")
    location_id = request.GET.get("location_id")
    date_from = request.GET.get("from")
    date_to = request.GET.get("to")

    qs = (
        InventoryMovement.objects
        .select_related("item", "location", "registered_by", "location__warehouse")
        .order_by("-occurred_at", "-id")
    )

    if item_id:
        qs = qs.filter(item_id=item_id)
    if location_id:
        qs = qs.filter(location_id=location_id)
    if date_from:
        qs = qs.filter(occurred_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(occurred_at__date__lte=date_to)

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="inventory_movements.csv"'

    writer = csv.writer(response)
    writer.writerow([
        "id", "occurred_at", "movement_type", "quantity",
        "item_sku", "item_name",
        "warehouse", "location_code",
        "registered_by",
        "reference", "notes",
    ])

    for m in qs.iterator(chunk_size=2000):
        wh_code = m.location.warehouse.code if m.location_id else ""
        loc_code = m.location.code if m.location_id else ""
        writer.writerow([
            m.id,
            m.occurred_at.isoformat(),
            m.movement_type,
            str(m.quantity),
            m.item.sku,
            m.item.name,
            wh_code,
            loc_code,
            getattr(m.registered_by, "username", str(m.registered_by_id)),
            m.reference,
            (m.notes or "").replace("\n", " ").strip(),
        ])

    return response


@login_required
def export_snapshots_csv(request):
    _require_group(request.user, "inventory_admin")

    warehouse_id = request.GET.get("warehouse_id")
    location_id = request.GET.get("location_id")
    q = (request.GET.get("q") or "").strip()

    qs = (
        StockSnapshot.objects
        .select_related("item", "location", "location__warehouse")
        .order_by("item__sku", "location__warehouse__code", "location__code")
    )

    if warehouse_id:
        qs = qs.filter(location__warehouse_id=warehouse_id)
    if location_id:
        qs = qs.filter(location_id=location_id)
    if q:
        qs = qs.filter(Q(item__sku__icontains=q) | Q(item__name__icontains=q))

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="inventory_snapshots.csv"'

    writer = csv.writer(response)
    writer.writerow([
        "snapshot_id",
        "item_id", "sku", "item_name",
        "warehouse_code",
        "location_id", "location_code", "location_name",
        "on_hand",
        "last_movement_at",
        "updated_at",
    ])

    for s in qs.iterator(chunk_size=2000):
        wh_code = s.location.warehouse.code if s.location_id else ""
        loc_code = s.location.code if s.location_id else ""
        loc_name = (s.location.name or "") if s.location_id else ""

        writer.writerow([
            s.id,
            s.item_id, s.item.sku, s.item.name,
            wh_code,
            s.location_id or "",
            loc_code,
            loc_name,
            str(s.on_hand),
            s.last_movement_at.isoformat() if s.last_movement_at else "",
            s.updated_at.isoformat() if s.updated_at else "",
        ])

    return response


# ============================================================
# Void / anulación
# ============================================================

@require_http_methods(["GET", "POST"])
@login_required
def movement_void(request, movement_id: int):
    """
    Anula un movimiento de inventario creando un movimiento inverso.
    Solo inventory_admin puede ejecutarlo.
    """
    _require_group(request.user, "inventory_admin")

    movement = get_object_or_404(
        InventoryMovement.objects.select_related(
            "item",
            "location",
            "location__warehouse",
            "registered_by",
        ),
        pk=movement_id,
    )

    if movement.is_void:
        messages.warning(request, "Este movimiento ya está marcado como anulado.")
        return redirect("item_detail", movement.item_id)

    already_has_reverse = InventoryMovement.objects.filter(void_of_id=movement.id).exists()

    if already_has_reverse:
        messages.warning(request, "Este movimiento ya tiene un reverso asociado.")
        return redirect("item_detail", movement.item_id)

    if request.method == "POST":
        form = VoidMovementForm(request.POST)

        if form.is_valid():
            cd = form.cleaned_data

            try:
                result = StockService.void_movement(
                    movement=movement,
                    voided_by=request.user,
                    reason=cd["reason"],
                    reference=cd.get("reference") or "",
                    notes=cd.get("notes") or "",
                    occurred_at=timezone.now(),
                )
            except ValidationError as ex:
                _add_validation_error(request, "No se pudo anular", ex)
                return render(
                    request,
                    "inventory/movement_void.html",
                    {"form": form, "movement": movement},
                )
            except Exception as ex:
                _add_unexpected_error(request, ex, public_message="No se pudo anular el movimiento")
                return render(
                    request,
                    "inventory/movement_void.html",
                    {"form": form, "movement": movement},
                )

            messages.success(
                request,
                f"Movimiento anulado correctamente. Nuevo stock: {result.new_on_hand}",
            )
            return redirect("item_detail", movement.item_id)

        _add_form_warning(request, form)

    form = form if request.method == "POST" else VoidMovementForm()

    return render(
        request,
        "inventory/movement_void.html",
        {
            "form": form,
            "movement": movement,
        },
    )


# ============================================================
# Reservas
# ============================================================

@login_required
def reservation_manage(request):
    _require_group(request.user, "inventory_supervisor")

    if request.method == "POST":
        form = ReservationForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            try:
                if cd["mode"] == ReservationForm.MODE_RESERVE:
                    result = StockService.reserve(
                        item=cd["item"],
                        location=cd["location"],
                        quantity=cd["quantity"],
                        reserved_by=request.user,
                        reference=cd.get("reference") or "",
                        notes=cd.get("notes") or "",
                        occurred_at=timezone.now(),
                    )
                    messages.success(
                        request,
                        f"Reserva OK. Reservado: {result.new_reserved} | Disponible: {result.new_available}",
                    )
                else:
                    result = StockService.release(
                        item=cd["item"],
                        location=cd["location"],
                        quantity=cd["quantity"],
                        released_by=request.user,
                        reference=cd.get("reference") or "",
                        notes=cd.get("notes") or "",
                        occurred_at=timezone.now(),
                    )
                    messages.success(
                        request,
                        f"Liberación OK. Reservado: {result.new_reserved} | Disponible: {result.new_available}",
                    )

                return redirect("item_detail", cd["item"].id)

            except ValidationError as ex:
                _add_validation_error(request, "No se pudo aplicar la operación", ex)
                return render(request, "inventory/reservation_form.html", {"form": form})
            except Exception as ex:
                _add_unexpected_error(request, ex, public_message="No se pudo aplicar la operación")
                return render(request, "inventory/reservation_form.html", {"form": form})
        else:
            _add_form_warning(request, form)

    else:
        initial = {}
        item_id = (request.GET.get("item_id") or "").strip()
        location_id = (request.GET.get("location_id") or "").strip()

        if item_id.isdigit():
            initial["item"] = int(item_id)
        if location_id.isdigit():
            initial["location"] = int(location_id)

        form = ReservationForm(initial=initial)

    return render(request, "inventory/reservation_form.html", {"form": form})


@login_required
def reserve_create(request):
    _require_group(request.user, "inventory_operator")

    if request.method == "POST":
        form = ReserveForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            try:
                result = StockService.reserve(
                    item=cd["item"],
                    location=cd["location"],
                    quantity=cd["quantity"],
                    reserved_by=request.user,
                    reference=cd.get("reference") or "RESERVE",
                    notes=cd.get("notes") or "",
                )
            except ValidationError as ex:
                _add_validation_error(request, "No se pudo reservar", ex)
                ctx = _ctx_with_perms(request, {"form": form})
                return render(request, "inventory/reserve_form.html", ctx)
            except Exception as ex:
                _add_unexpected_error(request, ex, public_message="No se pudo reservar")
                ctx = _ctx_with_perms(request, {"form": form})
                return render(request, "inventory/reserve_form.html", ctx)

            messages.success(
                request,
                f"Reserva aplicada. Reservado: {result.new_reserved} | Disponible: {result.new_available}",
            )
            return redirect("item_detail", cd["item"].id)
        else:
            _add_form_warning(request, form)

    else:
        form = ReserveForm()

    ctx = _ctx_with_perms(request, {"form": form})
    return render(request, "inventory/reserve_form.html", ctx)


@login_required
def release_create(request):
    _require_group(request.user, "inventory_operator")

    if request.method == "POST":
        form = ReleaseForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            try:
                result = StockService.release(
                    item=cd["item"],
                    location=cd["location"],
                    quantity=cd["quantity"],
                    released_by=request.user,
                    reference=cd.get("reference") or "RELEASE",
                    notes=cd.get("notes") or "",
                )
            except ValidationError as ex:
                _add_validation_error(request, "No se pudo liberar", ex)
                ctx = _ctx_with_perms(request, {"form": form})
                return render(request, "inventory/release_form.html", ctx)
            except Exception as ex:
                _add_unexpected_error(request, ex, public_message="No se pudo liberar")
                ctx = _ctx_with_perms(request, {"form": form})
                return render(request, "inventory/release_form.html", ctx)

            messages.success(
                request,
                f"Liberación aplicada. Reservado: {result.new_reserved} | Disponible: {result.new_available}",
            )
            return redirect("item_detail", cd["item"].id)
        else:
            _add_form_warning(request, form)

    else:
        form = ReleaseForm()

    ctx = _ctx_with_perms(request, {"form": form})
    return render(request, "inventory/release_form.html", ctx)


# ============================================================
# Reportes
# ============================================================

@login_required
def reports_home(request):
    _require_group(request.user, "inventory_admin")

    ctx = {}
    ctx.update(_perm_flags(request.user))
    ctx["today"] = date.today().isoformat()

    return render(request, "inventory/reports_home.html", ctx)


@login_required
def report_movements(request):
    _require_group(request.user, "inventory_admin")

    item_id = (request.GET.get("item_id") or "").strip()
    location_id = (request.GET.get("location_id") or "").strip()
    date_from = (request.GET.get("from") or "").strip()
    date_to = (request.GET.get("to") or "").strip()
    movement_type = (request.GET.get("type") or "").strip().upper()
    show_void = (request.GET.get("void") or "").strip()
    download = (request.GET.get("download") or "").strip() == "1"

    qs = (
        InventoryMovement.objects
        .select_related("item", "location", "registered_by", "location__warehouse")
        .order_by("-occurred_at", "-id")
    )

    if item_id.isdigit():
        qs = qs.filter(item_id=int(item_id))
    if location_id.isdigit():
        qs = qs.filter(location_id=int(location_id))
    if movement_type in {"IN", "OUT", "ADJ"}:
        qs = qs.filter(movement_type=movement_type)
    if date_from:
        qs = qs.filter(occurred_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(occurred_at__date__lte=date_to)
    if show_void != "1":
        qs = qs.filter(is_void=False)

    if download:
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="report_movements.csv"'
        writer = csv.writer(response)
        writer.writerow([
            "id", "occurred_at", "movement_type", "quantity",
            "item_sku", "item_name",
            "warehouse", "location_code",
            "registered_by",
            "reference", "notes", "is_void", "void_of",
        ])

        for m in qs.iterator(chunk_size=2000):
            wh_code = m.location.warehouse.code if m.location_id else ""
            loc_code = m.location.code if m.location_id else ""
            writer.writerow([
                m.id,
                m.occurred_at.isoformat(),
                m.movement_type,
                str(m.quantity),
                m.item.sku,
                m.item.name,
                wh_code,
                loc_code,
                getattr(m.registered_by, "username", str(m.registered_by_id)),
                m.reference,
                (m.notes or "").replace("\n", " ").strip(),
                "1" if m.is_void else "0",
                m.void_of_id or "",
            ])
        return response

    paginator = Paginator(qs, 50)
    page_number = request.GET.get("page") or 1
    page = paginator.get_page(page_number)

    ctx = {
        "movements": page,
        "paginator": paginator,
        "filter_item_id": item_id,
        "filter_location_id": location_id,
        "filter_from": date_from,
        "filter_to": date_to,
        "filter_type": movement_type,
        "filter_void": show_void,
    }
    ctx.update(_perm_flags(request.user))
    return render(request, "inventory/report_movements.html", ctx)


@login_required
def report_snapshots(request):
    _require_group(request.user, "inventory_admin")

    warehouse_id = (request.GET.get("warehouse_id") or "").strip()
    location_id = (request.GET.get("location_id") or "").strip()
    q = (request.GET.get("q") or "").strip()
    only_low = (request.GET.get("low") or "").strip() == "1"
    download = (request.GET.get("download") or "").strip() == "1"

    qs = (
        StockSnapshot.objects
        .select_related("item", "location", "location__warehouse")
        .order_by("item__sku", "location__warehouse__code", "location__code")
    )

    if warehouse_id.isdigit():
        qs = qs.filter(location__warehouse_id=int(warehouse_id))
    if location_id.isdigit():
        qs = qs.filter(location_id=int(location_id))
    if q:
        qs = qs.filter(Q(item__sku__icontains=q) | Q(item__name__icontains=q))
    if only_low:
        qs = qs.filter(on_hand__lte=F("item__min_stock"))

    if download:
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="report_snapshots.csv"'
        writer = csv.writer(response)
        writer.writerow([
            "snapshot_id",
            "item_id", "sku", "item_name",
            "warehouse_code",
            "location_id", "location_code", "location_name",
            "on_hand", "reserved", "available",
            "min_stock",
            "last_movement_at",
            "updated_at",
        ])

        for s in qs.iterator(chunk_size=2000):
            wh_code = s.location.warehouse.code if s.location_id else ""
            loc_code = s.location.code if s.location_id else ""
            loc_name = (s.location.name or "") if s.location_id else ""
            reserved = getattr(s, "reserved", Decimal("0.000"))
            available = s.on_hand - reserved

            writer.writerow([
                s.id,
                s.item_id, s.item.sku, s.item.name,
                wh_code,
                s.location_id or "",
                loc_code,
                loc_name,
                str(s.on_hand),
                str(reserved),
                str(available),
                str(s.item.min_stock),
                s.last_movement_at.isoformat() if s.last_movement_at else "",
                s.updated_at.isoformat() if s.updated_at else "",
            ])
        return response

    paginator = Paginator(qs, 50)
    page_number = request.GET.get("page") or 1
    page = paginator.get_page(page_number)

    ctx = {
        "snapshots": page,
        "paginator": paginator,
        "filter_warehouse_id": warehouse_id,
        "filter_location_id": location_id,
        "filter_q": q,
        "filter_low": "1" if only_low else "",
    }
    ctx.update(_perm_flags(request.user))
    return render(request, "inventory/report_snapshots.html", ctx)
