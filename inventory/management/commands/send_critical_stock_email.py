from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db.models import F
from django.utils import timezone

from inventory.models import StockSnapshot
from inventory.services.notifications import InventoryNotifier


class Command(BaseCommand):
    help = "Escanea snapshots bajo mínimo y envía alertas con throttle (MEDIUM/HIGH/CRITICAL)."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=500, help="Máximo de snapshots a evaluar.")
        parser.add_argument("--reason", type=str, default="Scan programado", help="Motivo a incluir en el correo.")

    def handle(self, *args, **options):
        limit = int(options["limit"])
        reason = str(options["reason"])

        qs = (
            StockSnapshot.objects
            .select_related("item", "location", "location__warehouse")
            .filter(on_hand__lte=F("item__min_stock"))  # misma semántica que tu notifier
            .order_by("location__warehouse__code", "location__code", "item__sku")[:limit]
        )

        sent = InventoryNotifier.scan_and_notify(qs=qs, reason=reason)

        self.stdout.write(self.style.SUCCESS(
            f"[{timezone.localtime().strftime('%Y-%m-%d %H:%M:%S')}] Alertas enviadas: {sent}"
        ))