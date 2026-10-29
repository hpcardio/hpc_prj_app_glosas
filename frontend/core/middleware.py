from urllib.parse import urlencode

from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import Resolver404, resolve
from django.utils.cache import patch_cache_control

from .access import (
    can_access_route,
    first_allowed_url,
    is_ti,
)
from .services import (
    ApiError,
    api_get,
    reset_request_api_token,
    set_request_api_token,
)

ACOMPANHAMENTO_PARTICULAR_SOURCE_SCREENS = {
    "follow_up_solicitacoes",
    "emissao_nfse",
}


class ApiSessionMiddleware:
    public_paths = {
        "/login",
        "/login/",
        "/esqueci-senha",
        "/logout/",
        "/esqueci-senha/",
        "/redefinir-senha",
        "/redefinir-senha/",
        "/autenticacao/redefinir-senha",
        "/autenticacao/redefinir-senha/",
    }

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        is_public = (
            request.path in self.public_paths
            or request.path.startswith(f"/{settings.STATIC_URL.lstrip('/')}")
            or request.path == "/favicon.ico"
        )
        access_token = request.session.get("api_access_token")
        if not is_public and not access_token:
            query = urlencode({"next": request.get_full_path()})
            return redirect(f"/login/?{query}")

        if not is_public and access_token:
            try:
                route_name = resolve(request.path_info).url_name
            except Resolver404:
                route_name = None
            spu_route = route_name in {'spu_recaptcha_status', 'spu_recaptcha_viewer', 'spu_novnc_asset'}
            user = request.session.get("api_user") or {}
            telas_permitidas = set(user.get("telas_permitidas") or ())
            deve_atualizar_permissoes = (
                spu_route or "telas_permitidas" not in user
                or (
                    "acompanhamento_particular" not in telas_permitidas
                    and bool(
                        telas_permitidas
                        & ACOMPANHAMENTO_PARTICULAR_SOURCE_SCREENS
                    )
                )
            )
            if deve_atualizar_permissoes:
                try:
                    user = api_get(
                        "/usuarios/me",
                        token=access_token,
                        **({'timeout': 5} if spu_route else {}),
                    )
                    request.session["api_user"] = user
                except ApiError:
                    if spu_route:
                        return HttpResponse('Acesso ao desktop SPU não autorizado.', status=403)
            try:
                route_name = resolve(request.path_info).url_name
            except Resolver404:
                route_name = None
            if route_name == "user_access_management" and not is_ti(user):
                return redirect(
                    f"{first_allowed_url(user)}?acesso_negado=1"
                )
            if not can_access_route(user, route_name):
                return redirect(
                    f"{first_allowed_url(user)}?acesso_negado=1"
                )

        context_token = set_request_api_token(access_token)
        try:
            response = self.get_response(request)
            content_type = response.headers.get("Content-Type", "")
            if (
                not is_public
                and access_token
                and content_type.startswith("text/html")
            ):
                patch_cache_control(
                    response,
                    no_cache=True,
                    no_store=True,
                    must_revalidate=True,
                    private=True,
                )
            return response
        finally:
            reset_request_api_token(context_token)
