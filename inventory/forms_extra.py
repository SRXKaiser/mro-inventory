from decimal import Decimal
from django import forms

from locations.models import Warehouse, Location
from .models import Item



class TransferForm(forms.Form):
    item = forms.ModelChoiceField(queryset=Item.objects.order_by("sku"), required=True)

    from_warehouse = forms.ModelChoiceField(queryset=Warehouse.objects.order_by("code"), required=True, label="Warehouse origen")
    from_location = forms.ModelChoiceField(queryset=Location.objects.none(), required=True, label="Ubicación origen")

    to_warehouse = forms.ModelChoiceField(queryset=Warehouse.objects.order_by("code"), required=True, label="Warehouse destino")
    to_location = forms.ModelChoiceField(queryset=Location.objects.none(), required=True, label="Ubicación destino")

    quantity = forms.DecimalField(min_value=Decimal("0.001"), decimal_places=3, max_digits=12, required=True)
    reference = forms.CharField(max_length=80, required=False)
    notes = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Origen
        if "from_warehouse" in self.data:
            try:
                wid = int(self.data.get("from_warehouse"))
                self.fields["from_location"].queryset = Location.objects.filter(warehouse_id=wid).order_by("code")
            except (TypeError, ValueError):
                self.fields["from_location"].queryset = Location.objects.none()

        # Destino
        if "to_warehouse" in self.data:
            try:
                wid = int(self.data.get("to_warehouse"))
                self.fields["to_location"].queryset = Location.objects.filter(warehouse_id=wid).order_by("code")
            except (TypeError, ValueError):
                self.fields["to_location"].queryset = Location.objects.none()

    def clean(self):
        c = super().clean()
        fl = c.get("from_location")
        tl = c.get("to_location")
        if fl and tl and fl.id == tl.id:
            raise forms.ValidationError("Origen y destino no pueden ser la misma ubicación.")
        return c


class AdjustmentForm(forms.Form):
    MODE_DELTA = "DELTA"
    MODE_SET = "SET"

    item = forms.ModelChoiceField(queryset=Item.objects.order_by("sku"), required=True)

    warehouse = forms.ModelChoiceField(queryset=Warehouse.objects.order_by("code"), required=True)
    location = forms.ModelChoiceField(queryset=Location.objects.none(), required=True)

    mode = forms.ChoiceField(
        choices=[(MODE_DELTA, "Delta (+/-)"), (MODE_SET, "Set (dejar stock exacto)")],
        required=True
    )

    delta = forms.DecimalField(required=False, decimal_places=3, max_digits=12)
    new_on_hand = forms.DecimalField(required=False, decimal_places=3, max_digits=12, min_value=Decimal("0.000"))

    reason = forms.CharField(max_length=200, required=True)
    reference = forms.CharField(max_length=80, required=False)
    notes = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if "warehouse" in self.data:
            try:
                wid = int(self.data.get("warehouse"))
                self.fields["location"].queryset = Location.objects.filter(warehouse_id=wid).order_by("code")
            except (TypeError, ValueError):
                self.fields["location"].queryset = Location.objects.none()

    def clean(self):
        c = super().clean()
        mode = c.get("mode")
        delta = c.get("delta")
        new_on_hand = c.get("new_on_hand")

        if mode == self.MODE_DELTA:
            if delta is None or delta == Decimal("0"):
                raise forms.ValidationError("Para modo DELTA, delta es obligatorio y no puede ser 0.")
        elif mode == self.MODE_SET:
            if new_on_hand is None:
                raise forms.ValidationError("Para modo SET, new_on_hand es obligatorio.")
        else:
            raise forms.ValidationError("Modo inválido.")

        return c



class VoidMovementForm(forms.Form):
    reason = forms.CharField(
        label="Motivo de anulación",
        max_length=180,
        required=True,
        widget=forms.TextInput(attrs={"placeholder": "Ej. captura errónea, duplicado, etc."}),
    )
    reference = forms.CharField(label="Referencia", max_length=80, required=False)
    notes = forms.CharField(
        label="Notas",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    
class CycleCountForm(forms.Form):
    item = forms.ModelChoiceField(queryset=Item.objects.all())
    location = forms.ModelChoiceField(queryset=Location.objects.all())
    counted_qty = forms.DecimalField(min_value=0)
    reference = forms.CharField(required=False)
    notes = forms.CharField(required=False, widget=forms.Textarea)

class ReservationForm(forms.Form):
    MODE_RESERVE = "RESERVE"
    MODE_RELEASE = "RELEASE"

    MODE_CHOICES = [
        (MODE_RESERVE, "Reservar"),
        (MODE_RELEASE, "Liberar"),
    ]

    mode = forms.ChoiceField(choices=MODE_CHOICES, required=True, label="Acción")
    item = forms.ModelChoiceField(queryset=Item.objects.order_by("sku"), required=True, label="Artículo")

    warehouse = forms.ModelChoiceField(queryset=Warehouse.objects.all(), required=True, label="Warehouse")
    location = forms.ModelChoiceField(queryset=Location.objects.none(), required=True, label="Ubicación")

    quantity = forms.DecimalField(min_value=Decimal("0.001"), decimal_places=3, max_digits=12, label="Cantidad")
    reference = forms.CharField(max_length=80, required=False, label="Referencia")
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}), label="Notas")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Si location viene precargada (initial), precarga warehouse
        loc = None
        if self.initial.get("location"):
            try:
                loc = Location.objects.select_related("warehouse").get(pk=self.initial["location"])
            except Location.DoesNotExist:
                loc = None

        if loc:
            self.fields["warehouse"].initial = loc.warehouse_id
            self.fields["location"].queryset = Location.objects.filter(warehouse_id=loc.warehouse_id).order_by("code")
        else:
            self.fields["location"].queryset = Location.objects.none()

        # POST warehouse
        if "warehouse" in self.data:
            try:
                wid = int(self.data.get("warehouse"))
                self.fields["location"].queryset = Location.objects.filter(warehouse_id=wid).order_by("code")
            except (TypeError, ValueError):
                self.fields["location"].queryset = Location.objects.none()

    def clean(self):
        cleaned = super().clean()
        wh = cleaned.get("warehouse")
        loc = cleaned.get("location")
        if wh and loc and loc.warehouse_id != wh.id:
            raise forms.ValidationError("La ubicación seleccionada no pertenece al Warehouse.")
        return cleaned
    
class ReserveForm(forms.Form):
    item = forms.ModelChoiceField(queryset=Item.objects.all(), required=True, label="Artículo")
    warehouse = forms.ModelChoiceField(queryset=Warehouse.objects.all(), required=True, label="Warehouse")
    location = forms.ModelChoiceField(queryset=Location.objects.none(), required=True, label="Ubicación")

    quantity = forms.DecimalField(
        required=True,
        min_value=Decimal("0.001"),
        decimal_places=3,
        max_digits=12,
        label="Cantidad a reservar",
        help_text="Reserva stock (no cambia on_hand, solo reserved).",
    )

    reference = forms.CharField(required=False, max_length=80, label="Referencia")
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}), label="Notas")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Inicializa location según warehouse
        if "warehouse" in self.data:
            try:
                wid = int(self.data.get("warehouse"))
                self.fields["location"].queryset = Location.objects.filter(warehouse_id=wid).order_by("code")
            except (TypeError, ValueError):
                self.fields["location"].queryset = Location.objects.none()
        else:
            self.fields["location"].queryset = Location.objects.none()

    def clean(self):
        cleaned = super().clean()
        wh = cleaned.get("warehouse")
        loc = cleaned.get("location")
        if wh and loc and loc.warehouse_id != wh.id:
            raise forms.ValidationError("La ubicación no pertenece al warehouse seleccionado.")
        return cleaned


class ReleaseForm(forms.Form):
    item = forms.ModelChoiceField(queryset=Item.objects.all(), required=True, label="Artículo")
    warehouse = forms.ModelChoiceField(queryset=Warehouse.objects.all(), required=True, label="Warehouse")
    location = forms.ModelChoiceField(queryset=Location.objects.none(), required=True, label="Ubicación")

    quantity = forms.DecimalField(
        required=True,
        min_value=Decimal("0.001"),
        decimal_places=3,
        max_digits=12,
        label="Cantidad a liberar",
        help_text="Libera stock reservado (disminuye reserved).",
    )

    reference = forms.CharField(required=False, max_length=80, label="Referencia")
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}), label="Notas")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if "warehouse" in self.data:
            try:
                wid = int(self.data.get("warehouse"))
                self.fields["location"].queryset = Location.objects.filter(warehouse_id=wid).order_by("code")
            except (TypeError, ValueError):
                self.fields["location"].queryset = Location.objects.none()
        else:
            self.fields["location"].queryset = Location.objects.none()

    def clean(self):
        cleaned = super().clean()
        wh = cleaned.get("warehouse")
        loc = cleaned.get("location")
        if wh and loc and loc.warehouse_id != wh.id:
            raise forms.ValidationError("La ubicación no pertenece al warehouse seleccionado.")
        return cleaned