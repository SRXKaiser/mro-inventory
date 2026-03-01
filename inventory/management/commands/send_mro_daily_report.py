from __future__ import annotations

from datetime import datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Count, F, Sum, Value, DecimalField
from django.db.models.functions import Coalesce
from django.db.models import ExpressionWrapper
from django.utils import timezone

from audit.models import NotificationEvent, NotificationKind, NotificationStatus
from inventory.models import StockSnapshot, InventoryMovement
from workorders.models import WorkOrder


DEC = DecimalField(max_digits=18, decimal_places=3)


class Command(BaseCommand):
    help = "Envía el daily report MRO (cooldown real + KPIs + movimientos del día)."

    def add_arguments(self, parser):
        parser.add_argument("--date", type=str, default="", help="YYYY-MM-DD (local). Opcional.")
        parser.add_argument("--force", action="store_true", help="Ignora cooldown y re-envía.")

    def _local_day_range(self, local_date):
        tz = timezone.get_current_timezone()
        start = timezone.make_aware(datetime.combine(local_date, time.min), tz)
        end = timezone.make_aware(datetime.combine(local_date, time.max), tz)
        return start, end

    def handle(self, *args, **options):
        force = bool(options["force"])

        now = timezone.now()
        local_date = timezone.localdate()

        if options["date"]:
            y, m, d = map(int, options["date"].split("-"))
            local_date = local_date.replace(year=y, month=m, day=d)

        start, end = self._local_day_range(local_date)

        recipients = list(getattr(settings, "NOTIFY_EMAILS", []) or [])
        if not recipients:
            self.stdout.write(self.style.WARNING("NOTIFY_EMAILS vacío. No se envía correo."))
            return

        key = f"daily_report:{local_date.isoformat()}"
        cooldown_hours = int(getattr(settings, "MRO_DAILY_REPORT_COOLDOWN_HOURS", 20))
        cooldown_until = now + timedelta(hours=cooldown_hours)
        subject = f"[MRO] Daily Report ({local_date.isoformat()})"

        # ===== Cooldown real + idempotencia (DB) =====
        with transaction.atomic():
            ev, created = NotificationEvent.objects.select_for_update().get_or_create(
                kind=NotificationKind.MRO_DAILY_REPORT,
                key=key,
                defaults={
                    "cooldown_until": cooldown_until,
                    "status": NotificationStatus.PENDING,
                    "subject": subject,
                    "recipient": ", ".join(recipients),
                    "meta": {"local_date": local_date.isoformat()},
                },
            )

            if not created:
                if ev.status == NotificationStatus.SENT and not force:
                    self.stdout.write(self.style.SUCCESS(f"Ya enviado: {key}"))
                    return

                if ev.is_in_cooldown() and not force:
                    ev.status = NotificationStatus.SKIPPED
                    ev.meta = {**(ev.meta or {}), "skip_reason": "cooldown_active"}
                    ev.save(update_fields=["status", "meta"])
                    self.stdout.write(self.style.WARNING(f"Cooldown activo hasta {ev.cooldown_until}. Skip."))
                    return

                # Reintento / force
                ev.cooldown_until = cooldown_until
                ev.status = NotificationStatus.PENDING
                ev.error = ""
                ev.subject = subject
                ev.recipient = ", ".join(recipients)
                ev.save(update_fields=["cooldown_until", "status", "error", "subject", "recipient"])

        # ===== 1) Tabla de alertas: Stock bajo mínimo =====
        qs_alerts = (
            StockSnapshot.objects
            .select_related("item", "location", "location__warehouse")
            .annotate(reserved0=Coalesce("reserved", Value(Decimal("0.000")), output_field=DEC))
            .annotate(available=ExpressionWrapper(F("on_hand") - F("reserved0"), output_field=DEC))
            .filter(on_hand__lte=F("item__min_stock"))
            .order_by("location__warehouse__code", "location__code", "item__sku")
        )

        alert_count = qs_alerts.count()

        alert_rows = []
        for s in qs_alerts[:500]:
            wh = s.location.warehouse.code if s.location and s.location.warehouse else "N/A"
            loc = s.location.code if s.location else "N/A"
            alert_rows.append(
                "<tr>"
                f"<td>{wh}</td>"
                f"<td>{loc}</td>"
                f"<td><b>{s.item.sku}</b></td>"
                f"<td>{s.item.name}</td>"
                f"<td align='right'>{s.on_hand}</td>"
                f"<td align='right'>{s.reserved0}</td>"
                f"<td align='right'><b>{s.available}</b></td>"
                f"<td align='right'>{s.item.min_stock}</td>"
                "</tr>"
            )

        # ===== 2) KPIs del día (movimientos + WOs) =====
        mv_qs = InventoryMovement.objects.filter(occurred_at__range=(start, end))
        mv_total = mv_qs.count()
        mv_void_total = mv_qs.filter(is_void=True).count()

        mv_by_type = (
            mv_qs.values("movement_type")
            .annotate(cnt=Count("id"), qty=Coalesce(Sum("quantity"), Value(Decimal("0.000")), output_field=DEC))
            .order_by("movement_type")
        )

        wo_created = WorkOrder.objects.filter(created_at__range=(start, end)).count()
        wo_closed = WorkOrder.objects.filter(closed_at__range=(start, end)).count()
        wo_open_now = WorkOrder.objects.exclude(status__in=[WorkOrder.Status.COMPLETED, WorkOrder.Status.CANCELLED]).count()

        # ===== 3) Movimientos del día (tabla) =====
        mv_rows = []
        mv_list = (
            mv_qs.select_related("item", "location", "location__warehouse", "registered_by")
            .order_by("-occurred_at")[:300]
        )
        for m in mv_list:
            wh = m.location.warehouse.code if m.location and m.location.warehouse else "N/A"
            loc = m.location.code if m.location else "N/A"
            when = timezone.localtime(m.occurred_at).strftime("%H:%M")
            who = getattr(m.registered_by, "username", str(m.registered_by_id))
            tag_void = " <span style='color:#b00;font-weight:bold'>(VOID)</span>" if m.is_void else ""
            mv_rows.append(
                "<tr>"
                f"<td>{when}</td>"
                f"<td>{m.movement_type}{tag_void}</td>"
                f"<td>{wh}</td>"
                f"<td>{loc}</td>"
                f"<td><b>{m.item.sku}</b></td>"
                f"<td align='right'>{m.quantity}</td>"
                f"<td>{m.reference or ''}</td>"
                f"<td>{who}</td>"
                "</tr>"
            )

        # Render KPIs “por tipo”
        mv_type_rows = []
        for r in mv_by_type:
            mv_type_rows.append(
                "<tr>"
                f"<td><b>{r['movement_type']}</b></td>"
                f"<td align='right'>{r['cnt']}</td>"
                f"<td align='right'>{r['qty']}</td>"
                "</tr>"
            )

        text = (
            f"MRO Daily Report {local_date.isoformat()}\n"
            f"Alertas bajo mínimo: {alert_count}\n"
            f"Movimientos: {mv_total} (void: {mv_void_total})\n"
            f"WO creadas: {wo_created} | WO cerradas: {wo_closed} | WO abiertas ahora: {wo_open_now}\n"
        )

        html = f"""
        <div style="font-family:Arial,sans-serif">
          <h2 style="margin:0 0 8px 0;">MRO Daily Report — {local_date.isoformat()}</h2>
          <p style="margin:0 0 12px 0;color:#555;">
            Ventana local: {start.strftime('%Y-%m-%d %H:%M')} → {end.strftime('%Y-%m-%d %H:%M')}
          </p>

          <h3 style="margin:14px 0 8px 0;">KPIs</h3>
          <table cellpadding="6" cellspacing="0" border="1" style="border-collapse:collapse;">
            <tr><th align="left">Alertas bajo mínimo</th><td><b>{alert_count}</b></td></tr>
            <tr><th align="left">Movimientos del día</th><td><b>{mv_total}</b> (VOID: {mv_void_total})</td></tr>
            <tr><th align="left">WO creadas hoy</th><td>{wo_created}</td></tr>
            <tr><th align="left">WO cerradas hoy</th><td>{wo_closed}</td></tr>
            <tr><th align="left">WO abiertas (ahora)</th><td><b>{wo_open_now}</b></td></tr>
          </table>

          <h4 style="margin:14px 0 8px 0;">Movimientos por tipo</h4>
          <table cellpadding="6" cellspacing="0" border="1" style="border-collapse:collapse;">
            <thead><tr><th>Tipo</th><th>Cantidad</th><th>Total qty</th></tr></thead>
            <tbody>
              {''.join(mv_type_rows) if mv_type_rows else "<tr><td colspan='3'>Sin movimientos.</td></tr>"}
            </tbody>
          </table>

          <h3 style="margin:18px 0 8px 0;">Alertas de stock (bajo mínimo)</h3>
          <table cellpadding="6" cellspacing="0" border="1" style="border-collapse:collapse;width:100%;">
            <thead>
              <tr style="background:#111;color:#fff;">
                <th>WH</th><th>Loc</th><th>SKU</th><th>Item</th>
                <th>On Hand</th><th>Reserved</th><th>Available</th><th>Min</th>
              </tr>
            </thead>
            <tbody>
              {''.join(alert_rows) if alert_rows else "<tr><td colspan='8'>Sin alertas.</td></tr>"}
            </tbody>
          </table>

          <h3 style="margin:18px 0 8px 0;">Movimientos del día</h3>
          <table cellpadding="6" cellspacing="0" border="1" style="border-collapse:collapse;width:100%;">
            <thead>
              <tr style="background:#222;color:#fff;">
                <th>Hora</th><th>Tipo</th><th>WH</th><th>Loc</th><th>SKU</th>
                <th>Qty</th><th>Ref</th><th>By</th>
              </tr>
            </thead>
            <tbody>
              {''.join(mv_rows) if mv_rows else "<tr><td colspan='8'>Sin movimientos.</td></tr>"}
            </tbody>
          </table>

          <p style="margin-top:12px;color:#777;font-size:12px;">
            Notificación automática MRO Inventory.
          </p>
        </div>
        """

        try:
            send_mail(
                subject=subject,
                message=text,
                from_email=getattr(settings, "NOTIFY_FROM_EMAIL", None),
                recipient_list=recipients,
                fail_silently=False,
                html_message=html,
            )
            NotificationEvent.objects.filter(kind=NotificationKind.MRO_DAILY_REPORT, key=key).update(
                status=NotificationStatus.SENT,
                sent_at=timezone.now(),
                error="",
            )
            self.stdout.write(self.style.SUCCESS("Daily report enviado."))

        except Exception as ex:
            NotificationEvent.objects.filter(kind=NotificationKind.MRO_DAILY_REPORT, key=key).update(
                status=NotificationStatus.FAILED,
                error=str(ex),
            )
            raise