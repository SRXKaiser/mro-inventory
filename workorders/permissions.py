from __future__ import annotations

ROLE_OPERATOR = "inventory_operator"
ROLE_SUPERVISOR = "inventory_supervisor"
ROLE_ADMIN = "inventory_admin"


def _has_group(user, group_name: str) -> bool:
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name=group_name).exists()


def can_operate_inventory(user) -> bool:
    # operator + supervisor + admin
    return (
        _has_group(user, ROLE_OPERATOR)
        or _has_group(user, ROLE_SUPERVISOR)
        or _has_group(user, ROLE_ADMIN)
    )


def can_manage_workorders(user) -> bool:
    # supervisor + admin
    return _has_group(user, ROLE_SUPERVISOR) or _has_group(user, ROLE_ADMIN)
