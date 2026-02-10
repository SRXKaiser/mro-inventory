# inventory/forms.py
from decimal import Decimal

from django import forms

from locations.models import Warehouse, Location
from .models import Item, InventoryMovement


class ItemForm(forms.ModelForm):
    class Meta:
        model = Item
        fields = [
            "sku",
            "name",
            "description",
            "item_type",
            "criticality",
            "uom",
            "min_stock",
            "is_active",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
        }


class MovementForm(forms.ModelForm):
    warehouse = forms.ModelChoiceField(
        queryset=Warehouse.objects.all().order_by("code"),
        required=True,
        label="Warehouse",
    )

    class Meta:
        model = InventoryMovement
        fields = ["item", "movement_type", "quantity", "warehouse", "location", "reference", "notes"]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Por defecto, no muestres locations hasta que haya warehouse
        self.fields["location"].queryset = Location.objects.none()

        # Si editas un movimiento (o vienes con instance) precarga warehouse y locations
        if self.instance and getattr(self.instance, "location_id", None):
            wh_id = self.instance.location.warehouse_id
            self.fields["warehouse"].initial = wh_id
            self.fields["location"].queryset = Location.objects.filter(warehouse_id=wh_id).order_by("code")

        # Si viene desde POST, filtra locations por warehouse seleccionado
        if "warehouse" in self.data:
            try:
                wh_id = int(self.data.get("warehouse"))
                self.fields["location"].queryset = Location.objects.filter(warehouse_id=wh_id).order_by("code")
            except (TypeError, ValueError):
                self.fields["location"].queryset = Location.objects.none()

    def clean_quantity(self):
        qty = self.cleaned_data.get("quantity")
        if qty is None:
            raise forms.ValidationError("La cantidad es obligatoria.")
        if qty <= Decimal("0"):
            raise forms.ValidationError("La cantidad debe ser mayor a 0.")
        return qty

    def clean(self):
        cleaned = super().clean()
        warehouse = cleaned.get("warehouse")
        location = cleaned.get("location")

        if warehouse and location and location.warehouse_id != warehouse.id:
            raise forms.ValidationError("La ubicación seleccionada no pertenece al Warehouse.")
        return cleaned
