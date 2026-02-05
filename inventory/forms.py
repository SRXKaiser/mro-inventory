from decimal import Decimal
from django import forms
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


class MovementForm(forms.Form):
    item = forms.ModelChoiceField(queryset=Item.objects.order_by("sku"))
    movement_type = forms.ChoiceField(choices=InventoryMovement.MovementType.choices)
    quantity = forms.DecimalField(
        max_digits=12,
        decimal_places=3,
        min_value=Decimal("0.001"),
        help_text="Cantidad decimal, ej. 5.250",
    )
    reference = forms.CharField(required=False, max_length=100)
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def clean(self):
        cleaned = super().clean()
        item = cleaned.get("item")
        if item and not item.is_active:
            raise forms.ValidationError("No puedes registrar movimientos para un artículo inactivo.")
        return cleaned
