from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from .forms import WarehouseForm, LocationForm
from .models import Warehouse, Location


# -----------------------
# Warehouses
# -----------------------

@login_required
def warehouse_list(request):
    q = (request.GET.get("q") or "").strip()

    qs = Warehouse.objects.order_by("code")
    if q:
        qs = qs.filter(Q(code__icontains=q) | Q(name__icontains=q))

    return render(request, "locations/warehouse_list.html", {"warehouses": qs, "q": q})


@login_required
def warehouse_create(request):
    if request.method == "POST":
        form = WarehouseForm(request.POST)
        if form.is_valid():
            wh = form.save()
            messages.success(request, f"Almacén creado: {wh.code}")
            return redirect("warehouse_list")
    else:
        form = WarehouseForm()

    return render(request, "locations/warehouse_form.html", {"form": form, "title": "Nuevo almacén"})


@login_required
def warehouse_edit(request, warehouse_id: int):
    wh = get_object_or_404(Warehouse, pk=warehouse_id)

    if request.method == "POST":
        form = WarehouseForm(request.POST, instance=wh)
        if form.is_valid():
            wh = form.save()
            messages.success(request, f"Almacén actualizado: {wh.code}")
            return redirect("warehouse_list")
    else:
        form = WarehouseForm(instance=wh)

    return render(
        request,
        "locations/warehouse_form.html",
        {"form": form, "title": f"Editar almacén {wh.code}"},
    )


# -----------------------
# Locations
# -----------------------

@login_required
def location_list(request):
    q = (request.GET.get("q") or "").strip()
    warehouse_id = (request.GET.get("warehouse") or "").strip()

    qs = Location.objects.select_related("warehouse").order_by("warehouse__code", "code")

    if warehouse_id:
        qs = qs.filter(warehouse_id=warehouse_id)

    if q:
        qs = qs.filter(
            Q(code__icontains=q)
            | Q(name__icontains=q)
            | Q(warehouse__code__icontains=q)
            | Q(warehouse__name__icontains=q)
        )

    warehouses = Warehouse.objects.order_by("code")
    return render(
        request,
        "locations/location_list.html",
        {
            "locations": qs,
            "q": q,
            "warehouses": warehouses,
            "warehouse_id": warehouse_id,
        },
    )


@login_required
def location_create(request):
    if request.method == "POST":
        form = LocationForm(request.POST)
        if form.is_valid():
            loc = form.save()
            messages.success(request, f"Ubicación creada: {loc}")
            return redirect("location_list")
    else:
        form = LocationForm()

    return render(request, "locations/location_form.html", {"form": form, "title": "Nueva ubicación"})


@login_required
def location_edit(request, location_id: int):
    loc = get_object_or_404(Location, pk=location_id)

    if request.method == "POST":
        form = LocationForm(request.POST, instance=loc)
        if form.is_valid():
            loc = form.save()
            messages.success(request, f"Ubicación actualizada: {loc}")
            return redirect("location_list")
    else:
        form = LocationForm(instance=loc)

    return render(
        request,
        "locations/location_form.html",
        {"form": form, "title": f"Editar ubicación {loc.warehouse.code}:{loc.code}"},
    )
