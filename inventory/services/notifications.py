from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Iterable

from django.conf import settings
from django.core.cache import cache
from django.core.mail import send_mail
from django.utils import timezone


@dataclass(frozen=True)
class Severity:
    MEDIUM: int = 1
    HIGH: int = 2
    CRITICAL: int = 3


class InventoryNotifier:
    """
    Notificador simple:
    - Throttle por (item_id, location_id, severity) usando cache (sin migraciones).
    - Envío de correo HTML + texto.
    """

    @staticmethod
    def _cooldown_seconds(severity: int) -> int:
        if severity == Severity.CRITICAL:
            return int(getattr(settings, "NOTIFY_COOLDOWN_CRITICAL_MIN", 60)) * 60
        if severity == Severity.HIGH:
            return int(getattr(settings, "NOTIFY_COOLDOWN_HIGH_MIN", 240)) * 60
        return int(getattr(settings, "NOTIFY_COOLDOWN_MEDIUM_MIN", 720)) * 60

    @staticmethod
    def _severity_label(severity: int) -> str:
        if severity == Severity.CRITICAL:
            return "CRÍTICO"
        if severity == Severity.HIGH:
            return "ALTO"
        return "MEDIO"

    @staticmethod
    def _cache_key(item_id: int, location_id: int, severity: int) -> str:
        return f"mro:stock-alert:{item_id}:{location_id}:{severity}"

    @staticmethod
    def compute_severity(*, on_hand: Decimal, reserved: Decimal, min_stock: Decimal) -> Optional[int]:
        """
        Reusa tu semántica actual del dashboard:
        - Solo evaluamos severidad si está bajo mínimo.
        - CRÍTICO: available <= 0
        - ALTO: reserved > 0
        - MEDIO: lo demás
        """
        if on_hand is None:
            on_hand = Decimal("0.000")
        if reserved is None:
            reserved = Decimal("0.000")
        if min_stock is None:
            min_stock = Decimal("0.000")

        if on_hand > min_stock:
            return None  # No alerta

        available = on_hand - reserved

        if available <= Decimal("0.000"):
            return Severity.CRITICAL
        if reserved > Decimal("0.000"):
            return Severity.HIGH
        return Severity.MEDIUM

    @staticmethod
    def should_send(*, item_id: int, location_id: int, severity: int) -> bool:
        key = InventoryNotifier._cache_key(item_id, location_id, severity)
        if cache.get(key):
            return False
        cache.set(key, True, timeout=InventoryNotifier._cooldown_seconds(severity))
        return True

    @staticmethod
    def send_stock_alert(*, snapshot, severity: int, reason: str = "") -> bool:
        recipients: list[str] = list(getattr(settings, "NOTIFY_EMAILS", []) or [])
        if not recipients:
            return False

        item = snapshot.item
        loc = snapshot.location
        wh_code = getattr(getattr(loc, "warehouse", None), "code", "N/A") if loc else "N/A"
        loc_code = getattr(loc, "code", "N/A") if loc else "N/A"

        reserved = snapshot.reserved or Decimal("0.000")
        available = (snapshot.on_hand or Decimal("0.000")) - reserved

        sev_label = InventoryNotifier._severity_label(severity)

        subject = f"[MRO] Alerta {sev_label}: {item.sku} ({wh_code}/{loc_code})"
        text = (
            f"Severidad: {sev_label}\n"
            f"SKU: {item.sku}\n"
            f"Item: {item.name}\n"
            f"WH/Loc: {wh_code}/{loc_code}\n"
            f"On Hand: {snapshot.on_hand}\n"
            f"Reserved: {reserved}\n"
            f"Available: {available}\n"
            f"Min: {item.min_stock}\n"
            f"Motivo: {reason}\n"
            f"Fecha: {timezone.localtime().strftime('%Y-%m-%d %H:%M:%S')}\n"
        )

        html = f"""
        <div style="font-family:Arial,sans-serif">
          <h2 style="margin:0 0 8px 0;">Alerta de Stock: <span>{sev_label}</span></h2>
          <p style="margin:0 0 12px 0;color:#555;">{timezone.localtime().strftime('%Y-%m-%d %H:%M:%S')}</p>

          <table cellpadding="6" cellspacing="0" border="1" style="border-collapse:collapse;">
            <tr><th align="left">SKU</th><td>{item.sku}</td></tr>
            <tr><th align="left">Artículo</th><td>{item.name}</td></tr>
            <tr><th align="left">Warehouse</th><td>{wh_code}</td></tr>
            <tr><th align="left">Ubicación</th><td>{loc_code}</td></tr>
            <tr><th align="left">On Hand</th><td><b>{snapshot.on_hand}</b></td></tr>
            <tr><th align="left">Reserved</th><td>{reserved}</td></tr>
            <tr><th align="left">Available</th><td><b>{available}</b></td></tr>
            <tr><th align="left">Min</th><td>{item.min_stock}</td></tr>
            <tr><th align="left">Motivo</th><td>{reason or '-'}</td></tr>
          </table>

          <p style="margin-top:12px;color:#777;font-size:12px;">
            Notificación automática MRO Inventory.
          </p>
        </div>
        """

        send_mail(
            subject=subject,
            message=text,
            from_email=getattr(settings, "NOTIFY_FROM_EMAIL", None),
            recipient_list=recipients,
            fail_silently=False,
            html_message=html,
        )
        return True

    @staticmethod
    def on_snapshot_changed(*, snapshot, reason: str = "") -> bool:
        """
        Punto único a llamar desde StockService.
        Decide severidad + throttle + envío.
        """
        sev = InventoryNotifier.compute_severity(
            on_hand=snapshot.on_hand,
            reserved=snapshot.reserved or Decimal("0.000"),
            min_stock=snapshot.item.min_stock or Decimal("0.000"),
        )
        if sev is None:
            return False

        if not InventoryNotifier.should_send(item_id=snapshot.item_id, location_id=snapshot.location_id, severity=sev):
            return False

        return InventoryNotifier.send_stock_alert(snapshot=snapshot, severity=sev, reason=reason)
    
    @staticmethod
    def scan_and_notify(*, qs, reason: str = "Scan programado") -> int:
        """
        Recorre snapshots (queryset) y dispara alertas aplicando severidad + throttle.
        Retorna cuántas alertas se intentaron enviar (después de throttle).
        """
        sent = 0
        for snapshot in qs:
            sev = InventoryNotifier.compute_severity(
                on_hand=snapshot.on_hand,
                reserved=snapshot.reserved or Decimal("0.000"),
                min_stock=snapshot.item.min_stock or Decimal("0.000"),
            )
            if sev is None:
                continue

            if not InventoryNotifier.should_send(
                item_id=snapshot.item_id,
                location_id=snapshot.location_id,
                severity=sev,
            ):
                continue

            InventoryNotifier.send_stock_alert(snapshot=snapshot, severity=sev, reason=reason)
            sent += 1

        return sent
