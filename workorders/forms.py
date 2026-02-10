from decimal import Decimal
from django import forms

from inventory.models import Item
from locations.models import Location
from .models import WorkOrderLine, Reservation
from django.contrib.auth import get_user_model
from .models import WorkOrder, WorkOrderLine

User = get_user_model()

class ReserveForm(forms.Form):
    line = forms.ModelChoiceField(queryset=WorkOrderLine.objects.none())
    location = forms.ModelChoiceField(queryset=Location.objects.all())
    qty = forms.DecimalField(max_digits=12, decimal_places=3, min_value=Decimal("0.001"))
    reason = forms.CharField(max_length=180, required=False)

    def __init__(self, *args, work_order=None, **kwargs):
        super().__init__(*args, **kwargs)
        if work_order is not None:
            self.fields["line"].queryset = WorkOrderLine.objects.filter(work_order=work_order).select_related("item")


class ConsumeForm(forms.Form):
    item = forms.ModelChoiceField(queryset=Item.objects.all())
    location = forms.ModelChoiceField(queryset=Location.objects.all())
    qty = forms.DecimalField(max_digits=12, decimal_places=3, min_value=Decimal("0.001"))
    reservation = forms.ModelChoiceField(queryset=Reservation.objects.none(), required=False)
    notes = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False)

    def __init__(self, *args, work_order=None, **kwargs):
        super().__init__(*args, **kwargs)
        if work_order is not None:
            # solo items que existan en líneas de la OT
            self.fields["item"].queryset = Item.objects.filter(
                id__in=WorkOrderLine.objects.filter(work_order=work_order).values_list("item_id", flat=True)
            )
            self.fields["reservation"].queryset = Reservation.objects.filter(
                work_order=work_order,
                status=Reservation.Status.ACTIVE
            ).select_related("item", "location")


class ReturnForm(forms.Form):
    item = forms.ModelChoiceField(queryset=Item.objects.all())
    location = forms.ModelChoiceField(queryset=Location.objects.all())
    qty = forms.DecimalField(max_digits=12, decimal_places=3, min_value=Decimal("0.001"))
    notes = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False)

    def __init__(self, *args, work_order=None, **kwargs):
        super().__init__(*args, **kwargs)
        if work_order is not None:
            self.fields["item"].queryset = Item.objects.filter(
                id__in=WorkOrderLine.objects.filter(work_order=work_order).values_list("item_id", flat=True)
            )
class WorkOrderCreateForm(forms.ModelForm):
    class Meta:
        model = WorkOrder
        fields = ["code", "priority", "assigned_to", "notes"]

    def clean_code(self):
        code = (self.cleaned_data.get("code") or "").strip()
        return code


class WorkOrderLineCreateForm(forms.ModelForm):
    class Meta:
        model = WorkOrderLine
        fields = ["item", "qty_required"]

    qty_required = forms.DecimalField(
        max_digits=12,
        decimal_places=3,
        min_value=Decimal("0.001"),
    )