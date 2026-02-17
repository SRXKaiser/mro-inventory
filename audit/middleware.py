from __future__ import annotations


class AuditContextMiddleware:
    """
    Guarda el contexto mínimo de la request para que tus services puedan auditar:
    request.audit_ctx.ip_address
    request.audit_ctx.user_agent
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        ip = request.META.get("HTTP_X_FORWARDED_FOR") or request.META.get("REMOTE_ADDR") or ""
        ua = request.META.get("HTTP_USER_AGENT") or ""

        request.audit_ctx = type("AuditCtx", (), {"ip_address": ip.split(",")[0].strip(), "user_agent": ua})()
        return self.get_response(request)
