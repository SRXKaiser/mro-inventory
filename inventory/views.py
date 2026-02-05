from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import F, Q
from django.shortcuts import get_object_or_404, redirect, render

from .forms import ItemForm, MovementForm
from .models import InventoryMovement, Item, StockSnapshot
from .services.stock_service import StockService


@login_required
def dashboard(request):
    low_stock = (
        StockSnapshot.objects.select_related("item")
        .filter(on_hand__lte=F("item__min_stock"))
        .order_by("on_hand")[:20]
    )
    return render(request, "dashboard.html", {"low_stock": low_stock})


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

    snapshot = StockSnapshot.objects.filter(item=item).first()

    movements = (
        InventoryMovement.objects.select_related("registered_by")
        .filter(item=item)
        .order_by("-occurred_at", "-id")[:50]
    )

    return render(
        request,
        "inventory/item_detail.html",
        {"item": item, "snapshot": snapshot, "movements": movements},
    )


@login_required
def movement_create(request):
    if request.method == "POST":
        form = MovementForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data

            try:
                result = StockService.register_movement(
                    item=cd["item"],
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
                f"Movimiento registrado. Stock actual: {result.snapshot.on_hand}",
            )
            return redirect("item_detail", cd["item"].id)
    else:
        form = MovementForm()

    return render(request, "inventory/movement_form.html", {"form": form})
