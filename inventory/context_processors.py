# inventory/context_processors.py
from __future__ import annotations

from typing import Dict, Any


GROUP_OPERATOR = "inventory_operator"
GROUP_SUPERVISOR = "inventory_supervisor"
GROUP_ADMIN = "inventory_admin"


def inventory_permissions(request) -> Dict[str, Any]:
    """
    Contexto global para templates:
      - can_transfer / can_adjust / can_export
      - inventory_roles (lista de roles en texto)
      - inventory_role_label (texto corto principal)
    """
    user = getattr(request, "user", None)

    can_transfer = False
    can_adjust = False
    can_export = False
    roles = []

    if user and user.is_authenticated:
        if user.is_superuser:
            can_transfer = True
            can_adjust = True
            can_export = True
            roles = ["Superuser"]
        else:
            can_transfer = user.groups.filter(name=GROUP_OPERATOR).exists()
            can_adjust = user.groups.filter(name=GROUP_SUPERVISOR).exists()
            can_export = user.groups.filter(name=GROUP_ADMIN).exists()

            if can_export:
                roles.append("Admin")
            if can_adjust:
                roles.append("Supervisor")
            if can_transfer:
                roles.append("Operator")

    role_label = roles[0] if roles else ""

    return {
        "can_transfer": can_transfer,
        "can_adjust": can_adjust,
        "can_export": can_export,
        "inventory_roles": roles,
        "inventory_role_label": role_label,
    }
