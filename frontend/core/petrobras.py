"""Same-origin bridge; API enforces permissions and all business rules."""

import json
import re
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_http_methods
from .services import ApiError, api_request

PATHS = {
    "GET": r"(status|glosas|protocolos|execucoes|glosas/[1-9][0-9]*/(anexos|historico)|glosas/[1-9][0-9]*/anexos/[1-9][0-9]*)",
    "POST": r"(protocolos|competencias/sincronizar|protocolos/[1-9][0-9]*/sincronizar|glosas/[1-9][0-9]*/(cruzar|anexos|enviar|consultar-recurso))",
    "PUT": r"glosas/[1-9][0-9]*/rascunho",
}


@require_http_methods(["GET", "POST", "PUT"])
def bridge(request, endpoint):
    if not settings.PETROBRAS_ENABLED or not re.fullmatch(
        PATHS[request.method], endpoint
    ):
        return JsonResponse({"detail": "Integração não disponível."}, status=404)
    if (
        request.method != "GET"
        and request.session.get("api_user", {}).get("perfil") == "leitura"
    ):
        return JsonResponse({"detail": "Perfil de leitura."}, status=403)
    kwargs = {"params": request.GET.dict()} if request.method == "GET" else {}
    if request.method != "GET":
        try:
            kwargs["json"] = json.loads(request.body or b"{}")
        except (ValueError, UnicodeDecodeError):
            return JsonResponse({"detail": "Solicitação inválida."}, status=400)
    try:
        upstream = api_request(
            request.method, "/app_glosas/petrobras/" + endpoint, timeout=120, **kwargs
        )
    except ApiError as exc:
        # Backend messages are safe; never expose HTML/proxy traces to the browser.
        try:
            detail = json.loads(str(exc)).get("detail")
            if not isinstance(detail, str):
                detail = "Revise os campos informados."
        except (ValueError, AttributeError):
            detail = "Integração indisponível. Os dados anteriores foram preservados."
        return JsonResponse({"detail": detail}, status=exc.status_code or 502)
    try:
        response = HttpResponse(
            upstream.content,
            status=upstream.status_code,
            content_type=upstream.headers.get("Content-Type", "application/json"),
        )
        for key in ("Content-Disposition", "X-Content-Type-Options"):
            if key in upstream.headers:
                response[key] = upstream.headers[key]
        response["Cache-Control"] = "no-store"
        return response
    finally:
        upstream.close()
