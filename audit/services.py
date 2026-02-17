from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.db import transaction

from .models import AuditLog


@dataclass(frozen=True)
class AuditContext:
    ip_address: str = ""
    user_agent: str = ""


class AuditService:
    @staticmethod
    @transaction.atomic
    def log(
        *,
        event_type: str,
        actor=None,
        entity_type: str = "",
        entity_id: str | int = "",
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        ctx: AuditContext | None = None,
    ) -> AuditLog:
        ctx = ctx or AuditContext()

        return AuditLog.objects.create(
            event_type=event_type,
            actor=actor if getattr(actor, "is_authenticated", False) else None,
            entity_type=(entity_type or ""),
            entity_id=str(entity_id or ""),
            before=before,
            after=after,
            metadata=metadata,
            ip_address=(ctx.ip_address or ""),
            user_agent=(ctx.user_agent or ""),
        )
