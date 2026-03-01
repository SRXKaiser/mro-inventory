from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone


class AuditLog(models.Model):
    """
    Bitácora de auditoría (forense):
    - Que paso (event_type)
    - Quien lo hizo (actor)
    - A que le pego (entity_type/entity_id)
    - Antes / Despues (JSON)
    - Contexto (ip, user_agent, metadata)
    """
    created_at = models.DateTimeField(auto_now_add=True)

    event_type = models.CharField(max_length=80)

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
    )

    entity_type = models.CharField(max_length=80, blank=True)
    entity_id = models.CharField(max_length=80, blank=True)

    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    metadata = models.JSONField(null=True, blank=True)

    ip_address = models.CharField(max_length=64, blank=True)
    user_agent = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["event_type", "created_at"]),
            models.Index(fields=["entity_type", "entity_id", "created_at"]),
            models.Index(fields=["actor", "created_at"]),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.created_at} {self.event_type} {self.entity_type}:{self.entity_id}"


class NotificationKind(models.TextChoices):
    MRO_DAILY_REPORT = "MRO_DAILY_REPORT", "MRO Daily Report"


class NotificationStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    SENT = "SENT", "Sent"
    SKIPPED = "SKIPPED", "Skipped"
    FAILED = "FAILED", "Failed"


class NotificationEvent(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)

    kind = models.CharField(max_length=64, choices=NotificationKind.choices)
    key = models.CharField(max_length=128)  # ej: daily_report:2026-02-21

    cooldown_until = models.DateTimeField(null=True, blank=True)

    status = models.CharField(
        max_length=16,
        choices=NotificationStatus.choices,
        default=NotificationStatus.PENDING,
    )
    sent_at = models.DateTimeField(null=True, blank=True)

    recipient = models.CharField(max_length=256, blank=True, default="")
    subject = models.CharField(max_length=256, blank=True, default="")
    error = models.TextField(blank=True, default="")
    meta = models.JSONField(blank=True, default=dict)

    class Meta:
        indexes = [
            models.Index(fields=["kind", "created_at"]),
            models.Index(fields=["kind", "key"]),
            models.Index(fields=["cooldown_until"]),
        ]
        constraints = [
            models.UniqueConstraint(fields=["kind", "key"], name="uq_notification_kind_key"),
        ]

    def is_in_cooldown(self) -> bool:
        return bool(self.cooldown_until and self.cooldown_until > timezone.now())