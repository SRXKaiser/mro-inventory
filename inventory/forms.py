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
        empty_label="---------",
    )

    class Meta:
        model = InventoryMovement
        fields = ["item", "movement_type", "quantity", "warehouse", "location", "reference", "notes"]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        """
        Regla:
        - location depende de warehouse.
        - En GET (sin data), precargamos el primer warehouse y sus locations.
        - En POST, filtramos locations por el warehouse enviado.
        - Si existe instance.location, precargamos warehouse y locations coherentes.
        """
        super().__init__(*args, **kwargs)

        # Base: no mostrar locations hasta decidir warehouse
        self.fields["location"].queryset = Location.objects.none()

        # 1) Si es edición (instance con location), precarga su warehouse y locations
        if self.instance and getattr(self.instance, "location_id", None):
            wh_id = self.instance.location.warehouse_id
            self.fields["warehouse"].initial = wh_id
            self.fields["location"].queryset = Location.objects.filter(warehouse_id=wh_id).order_by("code")

        # 2) Si viene desde POST, filtra locations por warehouse seleccionado
        if "warehouse" in self.data:
            try:
                wh_id = int(self.data.get("warehouse"))
                self.fields["location"].queryset = Location.objects.filter(warehouse_id=wh_id).order_by("code")
            except (TypeError, ValueError):
                self.fields["location"].queryset = Location.objects.none()

        # 3) Si es GET (sin POST) y no hay instance.location, precarga primer warehouse
        if not self.data and not (self.instance and getattr(self.instance, "location_id", None)):
            first_wh = Warehouse.objects.order_by("code").first()
            if first_wh:
                self.fields["warehouse"].initial = first_wh.id
                self.fields["location"].queryset = Location.objects.filter(warehouse_id=first_wh.id).order_by("code")

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
