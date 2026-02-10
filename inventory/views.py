from decimal import Decimal
import csv

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import F, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from locations.models import Warehouse, Location
from .forms import ItemForm, MovementForm
from .forms_extra import TransferForm, AdjustmentForm, VoidMovementForm, CycleCountForm
from .models import InventoryMovement, Item, StockSnapshot
from .services.stock_service import StockService
from .forms_extra import ReservationForm 
from .forms_extra import ReserveForm, ReleaseForm

from django.views.decorators.http import require_http_methods
from django.db import transaction

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


@login_required
def dashboard(request):
    low_stock = (
        StockSnapshot.objects
        .select_related("item", "location", "location__warehouse")
        .filter(on_hand__lte=F("item__min_stock"))
        .order_by("item__sku", "location__warehouse__code", "location__code")
    )

    ctx = {"low_stock": low_stock}
    ctx.update(_perm_flags(request.user))
    return render(request, "dashboard.html", ctx)


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
            item = form.save()
            messages.success(request, f"Artículo creado: {item.sku}")
            return redirect("item_detail", item.id)
    else:
        form = ItemForm()

    return render(request, "inventory/item_form.html", {"form": form, "title": "Nuevo artículo"})


@login_required
def item_edit(request, item_id: int):
    item = get_object_or_404(Item, pk=item_id)

    if request.method == "POST":
        form = ItemForm(request.POST, instance=item)
        if form.is_valid():
            item = form.save()
            messages.success(request, f"Artículo actualizado: {item.sku}")
            return redirect("item_detail", item.id)
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

    # filtros kardex
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

    # paginación
    paginator = Paginator(movements_qs, 50)
    page_number = request.GET.get("page") or 1
    movements_page = paginator.get_page(page_number)

    # options para filtro de location
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
            except Exception as ex:
                messages.error(request, f"No se pudo registrar el movimiento: {ex}")
                return render(request, "inventory/movement_form.html", {"form": form})

            messages.success(
                request,
                f"Movimiento registrado en {cd['location']}. Stock actual: {result.new_on_hand}",
            )
            return redirect("item_detail", cd["item"].id)

    # GET -> prellenado
    initial = {}
    if request.GET.get("item_id"):
        initial["item"] = request.GET.get("item_id")
    if request.GET.get("location_id"):
        initial["location"] = request.GET.get("location_id")

    form = MovementForm(initial=initial)
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
            except Exception as ex:
                messages.error(request, f"No se pudo transferir: {ex}")
                return render(request, "inventory/transfer_form.html", {"form": form})

            messages.success(
                request,
                f"Transferencia OK. Origen nuevo: {result.from_new_on_hand}, Destino nuevo: {result.to_new_on_hand}",
            )
            return redirect("item_detail", cd["item"].id)

    else:
        initial = {}  # <-- SIEMPRE definido, evita UnboundLocalError

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
            except Exception as ex:
                messages.error(request, f"No se pudo aplicar el ajuste: {ex}")
                return render(request, "inventory/adjustment_form.html", {"form": form})

            messages.success(request, f"Ajuste OK. Stock actual: {result.new_on_hand}")
            return redirect("item_detail", cd["item"].id)

    form = AdjustmentForm()
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
            except Exception as ex:
                messages.error(request, f"No se pudo aplicar el conteo: {ex}")
                return render(request, "inventory/cycle_count.html", {"form": form})

            messages.success(request, f"Conteo aplicado. Nuevo stock: {result.new_on_hand}")
            return redirect("item_detail", cd["item"].id)

    form = CycleCountForm()
    return render(request, "inventory/cycle_count.html", {"form": form})


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


@require_http_methods(["GET", "POST"])
@login_required
@transaction.atomic
def movement_void(request, movement_id: int):
    _require_group(request.user, "inventory_admin")

    # 1) LOCK SOLO la fila base (sin joins) -> evita el error de Postgres
    movement_locked = get_object_or_404(
        InventoryMovement.objects.select_for_update(),
        pk=movement_id,
    )

    # 2) Ahora sí carga relaciones (sin FOR UPDATE + outer joins)
    movement = (
        InventoryMovement.objects
        .select_related("item", "location", "location__warehouse", "registered_by")
        .get(pk=movement_locked.pk)
    )

    # 3) Check robusto: ¿existe reverso asociado?
    already_has_reverse = InventoryMovement.objects.filter(void_of_id=movement.id).exists()

    if movement.is_void or already_has_reverse:
        messages.warning(request, "Este movimiento ya está anulado.")
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
            except Exception as ex:
                messages.error(request, f"No se pudo anular: {ex}")
                return render(request, "inventory/movement_void.html", {"form": form, "movement": movement})

            messages.success(request, f"Movimiento anulado. Stock actual: {result.new_on_hand}")
            return redirect("item_detail", movement.item_id)

    form = VoidMovementForm()
    return render(request, "inventory/movement_void.html", {"form": form, "movement": movement})


@login_required
def reservation_manage(request):
    _require_group(request.user, "inventory_supervisor")  # o operator si tú quieres

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
                    messages.success(request, f"Reserva OK. Reservado: {result.new_reserved} | Disponible: {result.new_available}")
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
                    messages.success(request, f"Liberación OK. Reservado: {result.new_reserved} | Disponible: {result.new_available}")

                return redirect("item_detail", cd["item"].id)

            except Exception as ex:
                messages.error(request, f"No se pudo aplicar: {ex}")
                return render(request, "inventory/reservation_form.html", {"form": form})

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
            except Exception as ex:
                messages.error(request, f"No se pudo reservar: {ex}")
                ctx = {"form": form}
                ctx.update(_perm_flags(request.user))
                return render(request, "inventory/reserve_form.html", ctx)

            messages.success(
                request,
                f"Reserva aplicada. Reservado: {result.new_reserved} | Disponible: {result.new_available}"
            )
            return redirect("item_detail", cd["item"].id)
    else:
        form = ReserveForm()

    ctx = {"form": form}
    ctx.update(_perm_flags(request.user))
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
            except Exception as ex:
                messages.error(request, f"No se pudo liberar: {ex}")
                ctx = {"form": form}
                ctx.update(_perm_flags(request.user))
                return render(request, "inventory/release_form.html", ctx)

            messages.success(
                request,
                f"Liberación aplicada. Reservado: {result.new_reserved} | Disponible: {result.new_available}"
            )
            return redirect("item_detail", cd["item"].id)
    else:
        form = ReleaseForm()

    ctx = {"form": form}
    ctx.update(_perm_flags(request.user))
    return render(request, "inventory/release_form.html", ctx)
