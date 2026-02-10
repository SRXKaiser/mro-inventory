from django import forms
from .models import Warehouse, Location


def _normalize_code(value: str) -> str:
    return (value or "").strip().upper()


class WarehouseForm(forms.ModelForm):
    class Meta:
        model = Warehouse
        fields = ["code", "name", "is_active"]

    def clean_code(self):
        return _normalize_code(self.cleaned_data.get("code"))


class LocationForm(forms.ModelForm):
    class Meta:
        model = Location
        fields = ["warehouse", "code", "name", "description", "is_active"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
        }

    def clean_code(self):
        return _normalize_code(self.cleaned_data.get("code"))
