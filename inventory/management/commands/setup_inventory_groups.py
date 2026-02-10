from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType

from inventory.models import Item, StockSnapshot, InventoryMovement


class Command(BaseCommand):
    help = "Crea grupos y asigna permisos para el módulo de inventario."

    def handle(self, *args, **kwargs):
        operator, _ = Group.objects.get_or_create(name="inventory_operator")
        supervisor, _ = Group.objects.get_or_create(name="inventory_supervisor")
        admin, _ = Group.objects.get_or_create(name="inventory_admin")

        # Content types
        ct_item = ContentType.objects.get_for_model(Item)
        ct_ss = ContentType.objects.get_for_model(StockSnapshot)
        ct_mv = ContentType.objects.get_for_model(InventoryMovement)

        # Permisos base: ver
        perms_view = []
        for ct in [ct_item, ct_ss, ct_mv]:
            perms_view += list(Permission.objects.filter(content_type=ct, codename__startswith="view_"))

        operator.permissions.set(perms_view)
        supervisor.permissions.set(perms_view)
        admin.permissions.set(perms_view)

        # Supervisor: agregar movimientos (vía forms/servicio) + ver
        # (No uses add/delete directo de InventoryMovement desde admin normalmente, pero D2 lo permite para admin)
        sup_extra = Permission.objects.filter(content_type=ct_mv, codename__in=["add_inventorymovement"])
        supervisor.permissions.add(*sup_extra)

        # Admin: todo (incluye change/delete por D2)
        admin.permissions.add(*Permission.objects.filter(content_type__in=[ct_item, ct_ss, ct_mv]))

        self.stdout.write(self.style.SUCCESS("Grupos creados/actualizados: inventory_operator, inventory_supervisor, inventory_admin"))
