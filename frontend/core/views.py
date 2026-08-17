from calendar import monthrange
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import json
from math import ceil, log10
from hashlib import sha256
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl

from django.contrib import messages
from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import redirect, render
from django.urls import Resolver404, resolve
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST

from .access import (
    ROUTE_PERMISSIONS,
    SCREEN_KEYS,
    build_screen_groups,
    can_access_screen,
    first_allowed_url,
    is_ti,
)
from .services import (
    ApiError,
    api_authenticate,
    api_delete,
    api_get,
    api_get_stream,
    api_patch,
    api_post,
    api_put,
)

PATIENTS_PER_PAGE = 10
TIPOS_ATENDIMENTO = ('Ambulatório', 'Externo', 'Urgência', 'Internação')
PRAZOS_RECURSO_CONVENIO_PATH = "/app_glosas/prazos-recurso-convenio"
CONVENIOS_PATH = "/app_glosas/convenios"
DASHBOARD_GLOSAS_CACHE_KEY = "dashboard:registros-glosa"
DASHBOARD_PRAZOS_CACHE_KEY = "dashboard:prazos-recurso-convenio"
DASHBOARD_CONVENIOS_CACHE_KEY = "dashboard:convenios"
DASHBOARD_TISS_CACHE_KEY = "dashboard:tiss-motivos"
ACOMPANHAMENTO_GLOSAS_CACHE_KEY = DASHBOARD_GLOSAS_CACHE_KEY
CONTA_TISS_CACHE_KEY = "conta-atendimento:tiss"
DEFAULT_DASHBOARD_PERIOD_MONTHS = 12
DASHBOARD_GLOSAS_LIMIT = 25000
CONCILIACAO_FATURAMENTO_PATH = (
    "/app_glosas/financeiro/conciliacao-faturamento"
)
CONCILIACOES_SEM_RECEBIMENTO_PATH = (
    f"{CONCILIACAO_FATURAMENTO_PATH}/sem-recebimento"
)
CONCILIACOES_GERENCIAMENTO_PATH = (
    f"{CONCILIACAO_FATURAMENTO_PATH}/conciliacoes"
)
FOLLOW_UP_GLOSAS_PATH = f"{CONCILIACAO_FATURAMENTO_PATH}/glosas-pendentes"
ASSOCIACOES_REMESSAS_IPM_PATH = (
    "/app_glosas/financeiro/associacoes-remessas-ipm"
)
CONTAS_BANCARIAS_PATH = "/app_glosas/financeiro/contas-bancarias"
LANCAMENTOS_EXTRATO_PATH = "/app_glosas/financeiro/lancamentos-extrato"
REQUISICOES_NOTA_PATH = "/app_glosas/requisicoes"
ATENDIMENTO_NOTA_CACHE_NAMESPACE = "solicitacao-nota:atendimento"
WORKFLOW_SOLICITACOES_PATH = (
    f"{REQUISICOES_NOTA_PATH}/solicitacoes-nota/workflow"
)
EMISSOES_NFSE_PATH = f"{REQUISICOES_NOTA_PATH}/emissoes-nfse"
NFSE_EXTERNAS_PATH = f"{REQUISICOES_NOTA_PATH}/nfse-externas"
ACOMPANHAMENTO_PARTICULAR_PATH = (
    f"{REQUISICOES_NOTA_PATH}/acompanhamento-particular"
)
ACOMPANHAMENTO_PARTICULAR_CALENDARIO_SESSION_KEY = (
    "acompanhamento_particular_calendario"
)
EMPRESAS_EMISSORAS_PATH = f"{REQUISICOES_NOTA_PATH}/empresas-emissoras"
LOCAIS_SOLICITACAO_NOTA = {
    "Clinica 1": "Clínica 1",
    "Clinica 2": "Clínica 2",
    "Emergencia": "Emergência",
}
STATUS_SOLICITACAO_NOTA = {
    "PENDENTE_VALIDACAO": ("Pendente de validação", "pendente"),
    "VALIDADA": ("Validada", "validada"),
    "RECUSADA": ("Recusada", "recusada"),
    "EMISSAO_SOLICITADA": ("Emissão solicitada", "emissao"),
    "EMITIDA": ("NFS-e emitida", "emitida"),
    "ERRO_EMISSAO": ("Erro na emissão", "erro"),
}
STATUS_EMISSAO_NFSE = {
    "PENDENTE": "Aguardando processamento",
    "PROCESSANDO": "Em processamento",
    "EMITIDA": "NFS-e emitida",
    "ERRO": "Erro na emissão",
}
STATUS_ACOMPANHAMENTO_PARTICULAR = {
    "SEM_SOLICITACAO": ("Sem solicitação", "sem-solicitacao"),
    "PENDENTE_VALIDACAO": ("Aguardando validação", "pendente"),
    "RECUSADA": ("Solicitação recusada", "recusada"),
    "VALIDADA": ("Validada para emissão", "validada"),
    "PENDENTE_EMISSAO": ("Aguardando emissão", "emissao"),
    "PROCESSANDO": ("Em processamento", "processando"),
    "EMITIDA": ("NFS-e emitida", "emitida"),
    "EMITIDA_DIRETAMENTE_ISS": (
        "Emitida diretamente no ISS",
        "emitida",
    ),
    "ERRO_EMISSAO": ("Erro na emissão", "erro"),
    "INATIVA": ("Solicitação inativa", "inativa"),
}
CONVENIOS_ACOMPANHAMENTO_PARTICULAR = {
    "PARTICULAR": "Particular",
    "PRONTOREDE": "Prontorede",
}


def format_cnpj(value):
    digits = "".join(
        character
        for character in str(value or "")
        if character.isdigit()
    )
    if len(digits) != 14:
        return str(value or "")
    return (
        f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/"
        f"{digits[8:12]}-{digits[12:]}"
    )


def _carregar_empresas_emissoras(request, incluir_inativas=False):
    try:
        payload = api_get(
            EMPRESAS_EMISSORAS_PATH,
            {"incluir_inativas": "true"} if incluir_inativas else None,
        )
        empresas = payload.get("empresas") or []
    except ApiError as exc:
        empresas = []
        messages.error(
            request,
            f"Empresas emissoras: {extract_api_error_message(exc)}",
        )
    return _preparar_empresas_emissoras(empresas)


def _preparar_empresas_emissoras(empresas):
    for empresa in empresas:
        empresa["cnpj_formatado"] = format_cnpj(empresa.get("cnpj"))
        empresa["data_atualizacao_formatada"] = format_api_datetime(
            empresa.get("data_atualizacao")
        )
    return empresas


def get_cached_atendimento_nota(codigo_atendimento):
    path = (
        f"{REQUISICOES_NOTA_PATH}/atendimentos/{codigo_atendimento}"
    )
    cache_key = build_api_cache_key(
        ATENDIMENTO_NOTA_CACHE_NAMESPACE,
        path,
    )
    payload = cache.get(cache_key)
    if payload is None:
        payload = api_get(path)
        cache.set(
            cache_key,
            payload,
            getattr(settings, "SOLICITACAO_NOTA_CACHE_SECONDS", 300),
        )
    payload = deepcopy(payload)
    historico = api_get(f"{path}/solicitacoes")
    payload["solicitacoes_existentes"] = (
        historico.get("solicitacoes") or []
    )
    return payload


def _preparar_historico_solicitacoes(solicitacoes):
    for solicitacao in solicitacoes:
        solicitacao["data_criacao_formatada"] = format_api_datetime(
            solicitacao.get("data_criacao")
        )
        solicitacao["valor_nota_formatado"] = format_brl_input(
            solicitacao.get("valor_nota")
        )
        solicitacao["local_label"] = LOCAIS_SOLICITACAO_NOTA.get(
            solicitacao.get("local"),
            solicitacao.get("local") or "Não informado",
        )
        if solicitacao.get("ativo") is False:
            status_label, status_classe = "Inativa", "inativo"
        else:
            status_label, status_classe = STATUS_SOLICITACAO_NOTA.get(
                solicitacao.get("status"),
                ("Status não informado", "pendente"),
            )
        solicitacao["status_label"] = status_label
        solicitacao["status_classe"] = status_classe
        solicitacao["status_emissao_label"] = STATUS_EMISSAO_NFSE.get(
            solicitacao.get("status_emissao"),
            "",
        )
    return solicitacoes


def _somar_procedimentos_atendimento(procedimentos):
    total = Decimal("0")
    for procedimento in procedimentos:
        try:
            total += Decimal(str(procedimento.get("valor_total") or "0"))
        except (InvalidOperation, TypeError, ValueError):
            continue
    return total


def _procedimento_elegivel_nfse(procedimento):
    if procedimento.get("convenio_elegivel_nfse") is True:
        return True
    convenio = str(procedimento.get("convenio") or "").strip().upper()
    return convenio in {"PARTICULAR", "PRONTOREDE", "PRONTOCARDIO REDE"}


def _preparar_procedimentos_atendimento(procedimentos):
    for procedimento in procedimentos:
        procedimento["convenio_elegivel_nfse"] = (
            _procedimento_elegivel_nfse(procedimento)
        )
    return procedimentos


def _procedimentos_elegiveis_nfse(procedimentos):
    return [
        procedimento
        for procedimento in procedimentos
        if _procedimento_elegivel_nfse(procedimento)
    ]


def _somar_procedimentos_elegiveis_nfse(procedimentos):
    return _somar_procedimentos_atendimento(
        _procedimentos_elegiveis_nfse(procedimentos)
    )


def _descricao_procedimentos_atendimento(procedimentos):
    linhas = []
    for procedimento in procedimentos:
        codigo = str(procedimento.get("codigo") or "").strip()
        descricao = str(procedimento.get("descricao") or "").strip()
        if descricao or codigo:
            linhas.append(descricao or codigo)
    return "\n".join(linhas)


def _safe_login_redirect(request):
    next_url = request.POST.get("next") or request.GET.get("next") or "/"
    if url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return "/"


def _successful_login_redirect(user, next_url):
    split_url = urlsplit(next_url)
    query = urlencode(
        [
            (key, value)
            for key, value in parse_qsl(
                split_url.query,
                keep_blank_values=True,
            )
            if key != "acesso_negado"
        ]
    )
    next_url = urlunsplit(split_url._replace(query=query))
    path = split_url.path
    if path in {"/", "/login", "/login/"}:
        return first_allowed_url(user)

    try:
        route_name = resolve(path).url_name
    except Resolver404:
        return next_url

    if route_name == "user_access_management" and not is_ti(user):
        return first_allowed_url(user)
    screen_key = ROUTE_PERMISSIONS.get(route_name)
    if screen_key and not can_access_screen(user, screen_key):
        return first_allowed_url(user)
    return next_url


@require_http_methods(["GET", "POST"])
def login_view(request):
    next_url = _safe_login_redirect(request)
    if request.method == "GET" and request.session.get("api_access_token"):
        return redirect(next_url)

    email = ""
    error = ""
    if request.method == "POST":
        email = (request.POST.get("email") or "").strip().lower()
        password = request.POST.get("password") or ""
        if not email or not password:
            error = "Informe seu e-mail e sua senha."
        else:
            try:
                auth_payload = api_authenticate(email, password)
                access_token = auth_payload.get("access_token")
                if not access_token:
                    raise ApiError("Token de acesso não retornado pela API.")
                user = api_get("/usuarios/me", token=access_token)
                request.session.cycle_key()
                request.session["api_access_token"] = access_token
                request.session["api_user"] = user
                if request.POST.get("remember") == "1":
                    request.session.set_expiry(settings.SESSION_COOKIE_AGE)
                else:
                    request.session.set_expiry(0)
                return redirect(
                    _successful_login_redirect(user, next_url)
                )
            except ApiError as exc:
                if exc.status_code == 401:
                    error = "E-mail ou senha incorretos."
                else:
                    error = "Não foi possível acessar o sistema agora. Tente novamente."

    return render(
        request,
        "login.html",
        {"email": email, "error": error, "next": next_url},
    )


@require_http_methods(["GET", "POST"])
def forgot_password(request):
    sent = False
    email = ""
    if request.method == "POST":
        email = (request.POST.get("email") or "").strip().lower()
        if email:
            try:
                api_post("/autenticacao/esqueci-senha", {"email": email})
                sent = True
            except ApiError:
                sent = True
    return render(
        request,
        "forgot_password.html",
        {"email": email, "sent": sent},
    )


@require_http_methods(["GET", "POST"])
def reset_password(request):
    token = request.POST.get("token") or request.GET.get("token") or ""
    success = False
    error = ""
    if request.method == "POST":
        password = request.POST.get("password") or ""
        confirmation = request.POST.get("password_confirmation") or ""
        if len(password) < 8:
            error = "A senha deve ter pelo menos 8 caracteres."
        elif password != confirmation:
            error = "As senhas não coincidem."
        else:
            try:
                api_post(
                    "/autenticacao/redefinir-senha",
                    {"token": token, "nova_senha": password},
                )
                success = True
            except ApiError:
                error = "Este link é inválido ou expirou. Solicite um novo."
    return render(
        request,
        "reset_password.html",
        {"token": token, "success": success, "error": error},
    )


@require_http_methods(["GET", "POST"])
def user_access_management(request):
    current_user = request.session.get("api_user") or {}
    if current_user.get("perfil") != "ti":
        messages.error(request, "Acesso restrito à equipe de TI.")
        return redirect("dashboard")

    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "create":
                telas_permitidas = request.POST.getlist(
                    "telas_permitidas"
                )
                api_post(
                    "/usuarios/",
                    {
                        "nome": (request.POST.get("nome") or "").strip(),
                        "email": (request.POST.get("email") or "").strip().lower(),
                        "senha": request.POST.get("senha") or "",
                        "perfil": request.POST.get("perfil") or "usuario",
                        "telas_permitidas": telas_permitidas,
                    },
                )
                messages.success(request, "Acesso criado com sucesso.")
            elif action == "status":
                user_id = int(request.POST.get("user_id") or 0)
                api_patch(
                    f"/usuarios/{user_id}/status",
                    {"ativo": request.POST.get("ativo") == "true"},
                )
                messages.success(request, "Status do acesso atualizado.")
            elif action == "password":
                user_id = int(request.POST.get("user_id") or 0)
                api_patch(
                    f"/usuarios/{user_id}/senha",
                    {"senha": request.POST.get("senha_temporaria") or ""},
                )
                messages.success(request, "Senha temporária atualizada.")
            elif action == "permissions":
                user_id = int(request.POST.get("user_id") or 0)
                api_patch(
                    f"/usuarios/{user_id}/permissoes",
                    {
                        "telas_permitidas": request.POST.getlist(
                            "telas_permitidas"
                        )
                    },
                )
                messages.success(
                    request,
                    "Telas visíveis atualizadas com sucesso.",
                )
            return redirect("user_access_management")
        except (ApiError, ValueError):
            messages.error(
                request,
                "Não foi possível concluir a operação. Verifique os dados.",
            )

    try:
        users = api_get("/usuarios/", {"limit": 200}).get("usuarios", [])
    except ApiError:
        users = []
        messages.error(request, "Não foi possível carregar os acessos.")
    for user in users:
        user["permission_groups"] = build_screen_groups(
            user.get("telas_permitidas"),
            full_access=user.get("perfil") == "ti",
        )
    return render(
        request,
        "user_access_management.html",
        {
            "users": users,
            "screen_groups": build_screen_groups(SCREEN_KEYS),
        },
    )


def access_denied(request):
    return render(request, "access_denied.html", status=403)


@require_POST
def logout_view(request):
    request.session.flush()
    return redirect("login")


@require_http_methods(["GET", "POST"])
def solicitacao_nota(request):
    retorno = (
        request.POST.get("retorno")
        if request.method == "POST"
        else request.GET.get("retorno")
    )
    retorno = str(retorno or "").strip()
    if retorno and not url_has_allowed_host_and_scheme(
        retorno,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        retorno = ""
    codigo_atendimento = (
        request.POST.get("codigo_atendimento")
        if request.method == "POST"
        else request.GET.get("codigo_atendimento")
    )
    codigo_atendimento = str(codigo_atendimento or "").strip()
    local = (request.POST.get("local") or "").strip()
    procedimento = (request.POST.get("procedimento") or "").strip()
    valor_nota = (request.POST.get("valor_nota") or "").strip()
    valor_nota_numerico = as_float_or_none(valor_nota)
    atendimento = None

    if request.method == "POST":
        try:
            codigo = int(codigo_atendimento)
        except ValueError:
            codigo = 0

        if codigo <= 0:
            messages.error(request, "Informe um código de atendimento válido.")
        elif local not in LOCAIS_SOLICITACAO_NOTA:
            messages.error(request, "Selecione o local da emissão.")
        elif not procedimento:
            messages.error(request, "Informe o procedimento.")
        elif valor_nota_numerico is None or valor_nota_numerico <= 0:
            messages.error(request, "Informe um valor da nota maior que zero.")
        else:
            try:
                api_post(
                    f"{REQUISICOES_NOTA_PATH}/solicitacoes-nota",
                    {
                        "codigo_atendimento": codigo,
                        "local": local,
                        "procedimento": procedimento,
                        "valor_nota": f"{valor_nota_numerico:.2f}",
                    },
                )
                messages.success(
                    request,
                    "Solicitação de nota cadastrada com sucesso.",
                )
                atendimento_path = (
                    f"{REQUISICOES_NOTA_PATH}/atendimentos/{codigo}"
                )
                cache.delete(
                    build_api_cache_key(
                        ATENDIMENTO_NOTA_CACHE_NAMESPACE,
                        atendimento_path,
                    )
                )
                request.session.pop(
                    ACOMPANHAMENTO_PARTICULAR_CALENDARIO_SESSION_KEY,
                    None,
                )
                return redirect(retorno or "solicitacao_nota")
            except ApiError as exc:
                messages.error(
                    request,
                    f"Cadastro da solicitação: {extract_api_error_message(exc)}",
                )

    if codigo_atendimento:
        try:
            codigo = int(codigo_atendimento)
            if codigo > 0:
                atendimento = get_cached_atendimento_nota(codigo)
        except ValueError:
            atendimento = None
        except ApiError as exc:
            if request.method == "GET":
                messages.error(
                    request,
                    f"Consulta do atendimento: {extract_api_error_message(exc)}",
                )

    if atendimento:
        procedimentos_atendimento = _preparar_procedimentos_atendimento(
            atendimento.get("procedimentos_atendimento") or []
        )
        total_procedimentos = atendimento.get(
            "valor_total_procedimentos"
        )
        if total_procedimentos is None:
            total_procedimentos = _somar_procedimentos_atendimento(
                procedimentos_atendimento
            )
            atendimento["valor_total_procedimentos"] = str(
                total_procedimentos
            )
        procedimentos_elegiveis = _procedimentos_elegiveis_nfse(
            procedimentos_atendimento
        )
        total_procedimentos_elegiveis = atendimento.get(
            "valor_total_procedimentos_elegiveis_nfse"
        )
        if total_procedimentos_elegiveis is None:
            total_procedimentos_elegiveis = (
                _somar_procedimentos_elegiveis_nfse(
                    procedimentos_atendimento
                )
            )
            atendimento[
                "valor_total_procedimentos_elegiveis_nfse"
            ] = str(total_procedimentos_elegiveis)
        if not procedimento:
            procedimento = _descricao_procedimentos_atendimento(
                procedimentos_elegiveis
            )
        if not valor_nota:
            valor_nota = format_brl_input(total_procedimentos_elegiveis)
        _preparar_historico_solicitacoes(
            atendimento.get("solicitacoes_existentes") or []
        )

    return render(
        request,
        "solicitacao_nota.html",
        {
            "atendimento": atendimento,
            "codigo_atendimento": codigo_atendimento,
            "local": local,
            "procedimento": procedimento,
            "valor_nota": valor_nota,
            "locais": LOCAIS_SOLICITACAO_NOTA.items(),
            "retorno": retorno,
        },
    )


@require_http_methods(["GET", "POST"])
def solicitacoes_nota(request):
    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        solicitacao_id = as_int_or_zero(
            request.POST.get("solicitacao_id")
        )
        try:
            if solicitacao_id <= 0:
                raise ValueError
            if action == "editar":
                local = (request.POST.get("local") or "").strip()
                procedimento = (
                    request.POST.get("procedimento") or ""
                ).strip()
                valor_nota = as_float_or_none(
                    request.POST.get("valor_nota")
                )
                if local not in LOCAIS_SOLICITACAO_NOTA:
                    raise ValueError("Selecione o local da emissão.")
                if not procedimento:
                    raise ValueError("Informe o procedimento.")
                if valor_nota is None or valor_nota <= 0:
                    raise ValueError(
                        "Informe um valor da nota maior que zero."
                    )
                api_patch(
                    f"{REQUISICOES_NOTA_PATH}/solicitacoes-nota/"
                    f"{solicitacao_id}",
                    {
                        "local": local,
                        "procedimento": procedimento,
                        "valor_nota": f"{valor_nota:.2f}",
                    },
                )
                messages.success(
                    request,
                    "Solicitação atualizada e reenviada para validação.",
                )
            elif action == "inativar":
                api_delete(
                    f"{REQUISICOES_NOTA_PATH}/solicitacoes-nota/"
                    f"{solicitacao_id}"
                )
                messages.success(
                    request,
                    "Solicitação inativada com sucesso.",
                )
            else:
                raise ValueError("Ação inválida.")
        except ValueError as exc:
            messages.error(request, str(exc) or "Solicitação inválida.")
        except ApiError as exc:
            messages.error(
                request,
                f"Alteração da solicitação: "
                f"{extract_api_error_message(exc)}",
            )
        query_string = request.GET.urlencode()
        redirect_url = request.path
        if query_string:
            redirect_url = f"{redirect_url}?{query_string}"
        return redirect(redirect_url)

    page = as_positive_int(request.GET.get("page"), 1)
    limit = 10
    offset = (page - 1) * limit
    filtros = {
        "codigo_atendimento": (
            request.GET.get("codigo_atendimento") or ""
        ).strip(),
        "nome_paciente": (
            request.GET.get("nome_paciente") or ""
        ).strip(),
        "convenio": (request.GET.get("convenio") or "").strip(),
        "local": (request.GET.get("local") or "").strip(),
        "status": (request.GET.get("status") or "").strip(),
    }
    if filtros["local"] not in LOCAIS_SOLICITACAO_NOTA:
        filtros["local"] = ""
    if filtros["status"] not in STATUS_SOLICITACAO_NOTA:
        filtros["status"] = ""
    solicitacoes = []
    total_solicitacoes = 0
    resumo_api = []
    api_params = {
        key: value for key, value in filtros.items() if value
    }
    api_params.update({"limit": limit, "offset": offset})

    try:
        response = api_get(
            f"{REQUISICOES_NOTA_PATH}/solicitacoes-nota",
            api_params,
        )
        solicitacoes = response.get("solicitacoes") or []
        resumo_api = response.get("resumo_status") or []
        total_solicitacoes = as_int_or_zero(response.get("total"))
        limit = as_positive_int(response.get("limit"), limit)
        offset = as_int_or_zero(response.get("offset"))
    except ApiError as exc:
        messages.error(
            request,
            f"Consulta das solicitações: {extract_api_error_message(exc)}",
        )

    for solicitacao in solicitacoes:
        solicitacao["data_criacao_formatada"] = format_api_datetime(
            solicitacao.get("data_criacao")
        )
        solicitacao["valor_nota_formatado"] = format_brl_input(
            solicitacao.get("valor_nota")
        )
        solicitacao["local_label"] = LOCAIS_SOLICITACAO_NOTA.get(
            solicitacao.get("local"),
            solicitacao.get("local") or "Não informado",
        )
        status_label, status_classe = STATUS_SOLICITACAO_NOTA.get(
            solicitacao.get("status"),
            ("Status não informado", "pendente"),
        )
        solicitacao["status_label"] = status_label
        solicitacao["status_classe"] = status_classe
        solicitacao["pode_alterar"] = solicitacao.get("status") in {
            "PENDENTE_VALIDACAO",
            "RECUSADA",
        }

    resumo_por_status = {
        str(item.get("status") or ""): item
        for item in resumo_api
    }
    resumo_status = []
    for status, (label, status_classe) in STATUS_SOLICITACAO_NOTA.items():
        item = resumo_por_status.get(status) or {}
        resumo_status.append(
            {
                "status": status,
                "label": label,
                "status_classe": status_classe,
                "quantidade": as_int_or_zero(item.get("quantidade")),
                "valor_total_formatado": (
                    format_brl_input(item.get("valor_total")) or "R$ 0,00"
                ),
            }
        )

    base_query = {
        key: value for key, value in filtros.items() if value
    }
    total_pages = max(ceil(total_solicitacoes / limit), 1)
    if page > total_pages:
        return redirect(
            f"{request.path}?"
            f"{urlencode({**base_query, 'page': total_pages})}"
        )
    pagination = {
        "page": page,
        "total_pages": total_pages,
        "page_options": [
            {"number": number, "selected": number == page}
            for number in range(1, total_pages + 1)
        ],
        "has_previous": page > 1,
        "has_next": page < total_pages,
        "previous_url": (
            f"?{urlencode({**base_query, 'page': page - 1})}"
            if page > 1
            else ""
        ),
        "next_url": (
            f"?{urlencode({**base_query, 'page': page + 1})}"
            if page < total_pages
            else ""
        ),
        "start": offset + 1 if solicitacoes and total_solicitacoes else 0,
        "end": min(offset + len(solicitacoes), total_solicitacoes),
        "total": total_solicitacoes,
        "query": base_query,
    }
    return render(
        request,
        "solicitacoes_nota.html",
        {
            "solicitacoes": solicitacoes,
            "pagination": pagination,
            "filtros": filtros,
            "convenios": get_convenio_dropdown_options(
                filtros["convenio"]
            ),
            "locais": LOCAIS_SOLICITACAO_NOTA.items(),
            "status_options": [
                (status, label)
                for status, (label, _status_classe)
                in STATUS_SOLICITACAO_NOTA.items()
            ],
            "resumo_status": resumo_status,
        },
    )


def _carregar_fila_solicitacoes(
    request,
    status,
    filtros=None,
    incluir_inativas=False,
):
    filtros = filtros or {}
    page = as_positive_int(request.GET.get("page"), 1)
    limit = 10
    offset = (page - 1) * limit
    api_params = {
        "status": status,
        "limit": limit,
        "offset": offset,
        **{
            key: value
            for key, value in filtros.items()
            if value
        },
    }
    if incluir_inativas:
        api_params["incluir_inativas"] = "true"
    solicitacoes = []
    total = 0
    try:
        response = api_get(WORKFLOW_SOLICITACOES_PATH, api_params)
        solicitacoes = response.get("solicitacoes") or []
        total = as_int_or_zero(response.get("total"))
        limit = as_positive_int(response.get("limit"), limit)
        offset = as_int_or_zero(response.get("offset"))
    except ApiError as exc:
        messages.error(
            request,
            f"Consulta do workflow: {extract_api_error_message(exc)}",
        )

    for solicitacao in solicitacoes:
        solicitacao["ativo"] = solicitacao.get("ativo") is not False
        solicitacao["data_criacao_formatada"] = format_api_datetime(
            solicitacao.get("data_criacao")
        )
        solicitacao["valor_nota_formatado"] = format_brl_input(
            solicitacao.get("valor_nota")
        )
        solicitacao["validado_em_formatada"] = format_api_datetime(
            solicitacao.get("validado_em")
        )
        solicitacao["inativado_em_formatada"] = format_api_datetime(
            solicitacao.get("inativado_em")
        )
        for procedimento in (
            solicitacao.get("procedimentos_atendimento") or []
        ):
            procedimento["realizado_em_formatado"] = (
                format_api_datetime(procedimento.get("realizado_em"))
            )
            procedimento["convenio_elegivel_nfse"] = (
                _procedimento_elegivel_nfse(procedimento)
            )
        procedimentos_atendimento = (
            solicitacao.get("procedimentos_atendimento") or []
        )
        total_procedimentos = solicitacao.get(
            "valor_total_procedimentos"
        )
        if total_procedimentos is None:
            total_procedimentos = _somar_procedimentos_atendimento(
                procedimentos_atendimento
            )
        solicitacao["valor_total_procedimentos_formatado"] = (
            format_brl_input(total_procedimentos) or "R$ 0,00"
        )
        _preparar_historico_solicitacoes(
            solicitacao.get("solicitacoes_anteriores") or []
        )
        solicitacao["local_label"] = LOCAIS_SOLICITACAO_NOTA.get(
            solicitacao.get("local"),
            solicitacao.get("local") or "Não informado",
        )
        status_label, _status_classe = STATUS_SOLICITACAO_NOTA.get(
            solicitacao.get("status"),
            ("Status não informado", "pendente"),
        )
        solicitacao["status_label"] = status_label
        solicitacao["situacao_recusa_label"] = (
            "Recusa" if solicitacao.get("ativo", True) else "Inativo"
        )
        solicitacao["situacao_recusa_classe"] = (
            "recusa" if solicitacao.get("ativo", True) else "inativo"
        )

    base_query = {
        key: value for key, value in filtros.items() if value
    }
    total_pages = max(ceil(total / limit), 1)
    if page > total_pages:
        return {
            "redirect_url": (
                f"{request.path}?"
                f"{urlencode({**base_query, 'page': total_pages})}"
            )
        }
    pagination = {
        "page": page,
        "total_pages": total_pages,
        "page_options": [
            {"number": number, "selected": number == page}
            for number in range(1, total_pages + 1)
        ],
        "has_previous": page > 1,
        "has_next": page < total_pages,
        "previous_url": (
            f"?{urlencode({**base_query, 'page': page - 1})}"
            if page > 1
            else ""
        ),
        "next_url": (
            f"?{urlencode({**base_query, 'page': page + 1})}"
            if page < total_pages
            else ""
        ),
        "start": offset + 1 if solicitacoes and total else 0,
        "end": min(offset + len(solicitacoes), total),
        "total": total,
        "query": base_query,
    }
    return {
        "solicitacoes": solicitacoes,
        "pagination": pagination,
        "filtros": filtros,
        "locais": LOCAIS_SOLICITACAO_NOTA.items(),
        "tipos_atendimento": TIPOS_ATENDIMENTO,
    }


def _carregar_emissoes_nfse(request, filtros=None):
    filtros = filtros or {}
    page = as_positive_int(request.GET.get("page"), 1)
    limit = 10
    offset = (page - 1) * limit
    api_params = {
        "limit": limit,
        "offset": offset,
        **{
            key: value
            for key, value in filtros.items()
            if value
        },
    }
    solicitacoes = []
    total = 0
    try:
        response = api_get(EMISSOES_NFSE_PATH, api_params)
        solicitacoes = response.get("solicitacoes") or []
        total = as_int_or_zero(response.get("total"))
        limit = as_positive_int(response.get("limit"), limit)
        offset = as_int_or_zero(response.get("offset"))
    except ApiError as exc:
        messages.error(
            request,
            f"Consulta das emissões: {extract_api_error_message(exc)}",
        )

    for solicitacao in solicitacoes:
        solicitacao["data_criacao_formatada"] = format_api_datetime(
            solicitacao.get("data_criacao")
        )
        solicitacao["valor_nota_formatado"] = format_brl_input(
            solicitacao.get("valor_nota")
        )
        solicitacao["validado_em_formatada"] = format_api_datetime(
            solicitacao.get("validado_em")
        )
        solicitacao["emissao_criada_em_formatada"] = format_api_datetime(
            solicitacao.get("emissao_criada_em")
        )
        solicitacao["emissao_atualizada_em_formatada"] = (
            format_api_datetime(
                solicitacao.get("emissao_atualizada_em")
            )
        )
        solicitacao["local_label"] = LOCAIS_SOLICITACAO_NOTA.get(
            solicitacao.get("local"),
            solicitacao.get("local") or "Não informado",
        )
        status = str(solicitacao.get("status") or "").strip()
        status_label, status_classe = STATUS_SOLICITACAO_NOTA.get(
            status,
            ("Status não informado", "pendente"),
        )
        solicitacao["status_label"] = status_label
        solicitacao["status_classe"] = status_classe
        solicitacao["pode_emitir"] = status in {
            "VALIDADA",
            "ERRO_EMISSAO",
        }
        status_emissao = str(
            solicitacao.get("status_emissao") or ""
        ).strip()
        solicitacao["status_emissao_label"] = (
            STATUS_EMISSAO_NFSE.get(
                status_emissao,
                status_emissao or "Não iniciada",
            )
        )
        solicitacao["cnpj_emissor_formatado"] = format_cnpj(
            solicitacao.get("cnpj_emissor")
        )
        solicitacao["emissao_processando"] = (
            status == "EMISSAO_SOLICITADA"
            or status_emissao in {"PENDENTE", "PROCESSANDO"}
        )

    base_query = {
        key: value for key, value in filtros.items() if value
    }
    total_pages = max(ceil(total / limit), 1)
    if page > total_pages:
        return {
            "redirect_url": (
                f"{request.path}?"
                f"{urlencode({**base_query, 'page': total_pages})}"
            )
        }
    pagination = {
        "page": page,
        "total_pages": total_pages,
        "page_options": [
            {"number": number, "selected": number == page}
            for number in range(1, total_pages + 1)
        ],
        "has_previous": page > 1,
        "has_next": page < total_pages,
        "previous_url": (
            f"?{urlencode({**base_query, 'page': page - 1})}"
            if page > 1
            else ""
        ),
        "next_url": (
            f"?{urlencode({**base_query, 'page': page + 1})}"
            if page < total_pages
            else ""
        ),
        "start": offset + 1 if solicitacoes and total else 0,
        "end": min(offset + len(solicitacoes), total),
        "total": total,
        "query": base_query,
    }
    return {
        "solicitacoes": solicitacoes,
        "pagination": pagination,
        "filtros": filtros,
        "locais": LOCAIS_SOLICITACAO_NOTA.items(),
        "tipos_atendimento": TIPOS_ATENDIMENTO,
        "ha_emissoes_processando": any(
            solicitacao["emissao_processando"]
            for solicitacao in solicitacoes
        ),
    }


@require_http_methods(["GET", "POST"])
def workflow_solicitacoes(request):
    if request.method == "POST":
        solicitacao_id = as_int_or_zero(
            request.POST.get("solicitacao_id")
        )
        decisao = (request.POST.get("decisao") or "").strip()
        motivo_recusa = (
            request.POST.get("motivo_recusa") or ""
        ).strip()
        empresa_emissora_id = as_int_or_zero(
            request.POST.get("empresa_emissora_id")
        )
        try:
            if solicitacao_id <= 0:
                raise ValueError
            if decisao == "VALIDADA":
                if empresa_emissora_id <= 0:
                    raise ValueError("Selecione o CNPJ emissor.")
                api_put(
                    f"{REQUISICOES_NOTA_PATH}/solicitacoes-nota/"
                    f"{solicitacao_id}/empresa-emissora",
                    {"empresa_emissora_id": empresa_emissora_id},
                )
            api_post(
                f"{REQUISICOES_NOTA_PATH}/solicitacoes-nota/"
                f"{solicitacao_id}/validacao",
                {
                    "decisao": decisao,
                    "motivo_recusa": motivo_recusa or None,
                },
            )
            if decisao == "VALIDADA":
                messages.success(
                    request,
                    "Solicitação validada e encaminhada para emissão.",
                )
            else:
                messages.success(
                    request,
                    "Solicitação recusada e encaminhada para recusas.",
                )
            return redirect("workflow_solicitacoes")
        except ValueError as exc:
            messages.error(
                request,
                str(exc) or "Solicitação inválida.",
            )
        except ApiError as exc:
            messages.error(
                request,
                f"Validação da solicitação: "
                f"{extract_api_error_message(exc)}",
            )

    filtros = {
        "codigo_atendimento": (
            request.GET.get("codigo_atendimento") or ""
        ).strip(),
        "nome_paciente": (
            request.GET.get("nome_paciente") or ""
        ).strip(),
        "cpf": (request.GET.get("cpf") or "").strip(),
        "convenio": (request.GET.get("convenio") or "").strip(),
        "tipo_atendimento": (
            request.GET.get("tipo_atendimento") or ""
        ).strip(),
        "local": (request.GET.get("local") or "").strip(),
        "data_inicio": (
            request.GET.get("data_inicio") or ""
        ).strip(),
        "data_fim": (request.GET.get("data_fim") or "").strip(),
    }
    if filtros["tipo_atendimento"] not in TIPOS_ATENDIMENTO:
        filtros["tipo_atendimento"] = ""
    if filtros["local"] not in LOCAIS_SOLICITACAO_NOTA:
        filtros["local"] = ""

    context = _carregar_fila_solicitacoes(
        request,
        "PENDENTE_VALIDACAO",
        filtros,
    )
    if redirect_url := context.get("redirect_url"):
        return redirect(redirect_url)
    context["convenios"] = get_convenio_dropdown_options(
        filtros["convenio"]
    )
    context["empresas_emissoras"] = _carregar_empresas_emissoras(
        request
    )
    return render(request, "workflow_solicitacoes.html", context)


@require_http_methods(["GET"])
def solicitacoes_recusas(request):
    context = _carregar_fila_solicitacoes(
        request,
        "RECUSADA",
        incluir_inativas=True,
    )
    if redirect_url := context.get("redirect_url"):
        return redirect(redirect_url)
    return render(request, "solicitacoes_recusas.html", context)


@require_http_methods(["GET", "POST"])
def acompanhamento_particular(request):
    if request.method == "POST":
        solicitacao_id = as_int_or_zero(
            request.POST.get("solicitacao_id")
        )
        decisao = (request.POST.get("decisao") or "").strip()
        motivo_recusa = (
            request.POST.get("motivo_recusa") or ""
        ).strip()
        empresa_emissora_id = as_int_or_zero(
            request.POST.get("empresa_emissora_id")
        )
        try:
            if solicitacao_id <= 0:
                raise ValueError
            if decisao == "VALIDADA":
                if empresa_emissora_id <= 0:
                    raise ValueError("Selecione o CNPJ emissor.")
                api_put(
                    f"{REQUISICOES_NOTA_PATH}/solicitacoes-nota/"
                    f"{solicitacao_id}/empresa-emissora",
                    {"empresa_emissora_id": empresa_emissora_id},
                )
            api_post(
                f"{REQUISICOES_NOTA_PATH}/solicitacoes-nota/"
                f"{solicitacao_id}/validacao",
                {
                    "decisao": decisao,
                    "motivo_recusa": motivo_recusa or None,
                },
            )
            if decisao == "VALIDADA":
                messages.success(
                    request,
                    "Solicitação validada e encaminhada para emissão.",
                )
            else:
                messages.success(
                    request,
                    "Solicitação recusada e encaminhada para recusas.",
                )
            request.session.pop(
                ACOMPANHAMENTO_PARTICULAR_CALENDARIO_SESSION_KEY,
                None,
            )
            return redirect(request.get_full_path())
        except ValueError as exc:
            messages.error(
                request,
                str(exc) or "Solicitação inválida.",
            )
        except ApiError as exc:
            messages.error(
                request,
                "Validação da solicitação: "
                f"{extract_api_error_message(exc)}",
            )

    hoje = date.today()
    referencia_raw = (
        request.GET.get("data_referencia")
        or request.GET.get("data_fim")
        or hoje.isoformat()
    ).strip()
    try:
        data_referencia = date.fromisoformat(referencia_raw)
    except ValueError:
        data_referencia = hoje
        messages.error(
            request,
            "Informe uma data de referência válida.",
        )

    data_selecionada_raw = (
        request.GET.get("data_selecionada") or hoje.isoformat()
    ).strip()
    try:
        data_selecionada = date.fromisoformat(data_selecionada_raw)
    except ValueError:
        data_selecionada = hoje
        messages.error(request, "Informe um dia válido.")

    data_inicio = data_referencia.replace(day=1)
    data_fim = data_referencia.replace(
        day=monthrange(data_referencia.year, data_referencia.month)[1]
    )
    if not data_inicio <= data_selecionada <= data_fim:
        data_referencia = data_selecionada
        data_inicio = data_referencia.replace(day=1)
        data_fim = data_referencia.replace(
            day=monthrange(data_referencia.year, data_referencia.month)[1]
        )
    visao = "mes"

    codigo_raw = (
        request.GET.get("codigo_atendimento") or ""
    ).strip()
    codigo_atendimento = ""
    if codigo_raw:
        codigo_atendimento = as_int_or_zero(codigo_raw)
        if codigo_atendimento <= 0:
            codigo_atendimento = ""
            messages.error(
                request,
                "Informe um código de atendimento válido.",
            )

    filtros = {
        "data_inicio": data_inicio.isoformat(),
        "data_fim": data_fim.isoformat(),
        "codigo_atendimento": codigo_atendimento,
        "nome_paciente": (
            request.GET.get("nome_paciente") or ""
        ).strip(),
        "tipo_atendimento": (
            request.GET.get("tipo_atendimento") or ""
        ).strip(),
        "convenio": (request.GET.get("convenio") or "").strip().upper(),
        "status": (request.GET.get("status") or "").strip(),
    }
    if filtros["tipo_atendimento"] not in TIPOS_ATENDIMENTO:
        filtros["tipo_atendimento"] = ""
    if filtros["convenio"] not in CONVENIOS_ACOMPANHAMENTO_PARTICULAR:
        filtros["convenio"] = ""
    if filtros["status"] not in STATUS_ACOMPANHAMENTO_PARTICULAR:
        filtros["status"] = ""

    page = as_positive_int(request.GET.get("page"), 1)
    limit = 10
    offset = (page - 1) * limit
    api_params_calendario = {
        **filtros,
        "limit": 1,
        "offset": 0,
    }
    api_params_dia = {
        **filtros,
        "data_inicio": data_selecionada.isoformat(),
        "data_fim": data_selecionada.isoformat(),
        "limit": limit,
        "offset": offset,
    }
    atendimentos = []
    resumo_dia_selecionado_api = []
    resumo_diario_api = []
    total = 0
    total_dia_selecionado = 0
    valor_total_dia_selecionado = 0
    empresas_emissoras = []
    try:
        calendario_cache_key = build_api_cache_key(
            "acompanhamento-particular:calendario",
            ACOMPANHAMENTO_PARTICULAR_PATH,
            api_params_calendario,
        )
        calendario_cache = request.session.get(
            ACOMPANHAMENTO_PARTICULAR_CALENDARIO_SESSION_KEY
        ) or {}
        calendario_cache_valido = (
            calendario_cache.get("key") == calendario_cache_key
            and calendario_cache.get("expires_at", 0)
            > datetime.now().timestamp()
            and isinstance(calendario_cache.get("payload"), dict)
        )
        payload_calendario = (
            deepcopy(calendario_cache["payload"])
            if calendario_cache_valido
            else None
        )
        contexto_calendario = (
            None if calendario_cache_valido else copy_context()
        )
        contexto_dia = copy_context()
        contexto_empresas = copy_context()
        with ThreadPoolExecutor(
            max_workers=3,
            thread_name_prefix="acompanhamento-particular",
        ) as executor:
            consulta_calendario = (
                executor.submit(
                    contexto_calendario.run,
                    api_get,
                    ACOMPANHAMENTO_PARTICULAR_PATH,
                    api_params_calendario,
                )
                if contexto_calendario is not None
                else None
            )
            consulta_dia = executor.submit(
                contexto_dia.run,
                api_get,
                ACOMPANHAMENTO_PARTICULAR_PATH,
                api_params_dia,
            )
            consulta_empresas = executor.submit(
                contexto_empresas.run,
                api_get,
                EMPRESAS_EMISSORAS_PATH,
                None,
            )
            if consulta_calendario is not None:
                payload_calendario = consulta_calendario.result()
                request.session[
                    ACOMPANHAMENTO_PARTICULAR_CALENDARIO_SESSION_KEY
                ] = {
                    "key": calendario_cache_key,
                    "expires_at": (
                        datetime.now().timestamp()
                        + max(
                            getattr(
                                settings,
                                "APP_FILTER_CACHE_SECONDS",
                                45,
                            ),
                            1,
                        )
                    ),
                    "payload": payload_calendario,
                }
            payload_dia = consulta_dia.result()
            try:
                payload_empresas = consulta_empresas.result()
                empresas_emissoras = _preparar_empresas_emissoras(
                    payload_empresas.get("empresas") or []
                )
            except ApiError as exc:
                messages.error(
                    request,
                    "Empresas emissoras: "
                    f"{extract_api_error_message(exc)}",
                )
        resumo_diario_api = (
            payload_calendario.get("resumo_diario") or []
        )
        resumo_dia_selecionado_api = (
            payload_dia.get("resumo_status") or []
        )
        total_dia_selecionado = as_int_or_zero(
            payload_dia.get("total_periodo")
        )
        valor_total_dia_selecionado = (
            payload_dia.get("valor_total_periodo") or 0
        )
        atendimentos = payload_dia.get("atendimentos") or []
        total = as_int_or_zero(payload_dia.get("total"))
        limit = as_positive_int(payload_dia.get("limit"), limit)
        offset = as_int_or_zero(payload_dia.get("offset"))

        selecionar_dia_resultado = (
            request.GET.get("selecionar_dia_resultado") == "1"
        )
        if selecionar_dia_resultado and total == 0:
            datas_com_resultado = []
            for resumo_dia in resumo_diario_api:
                quantidade_dia = as_int_or_zero(
                    resumo_dia.get("total")
                )
                if filtros["status"]:
                    quantidade_dia = next(
                        (
                            as_int_or_zero(item.get("quantidade"))
                            for item in (
                                resumo_dia.get("resumo_status") or []
                            )
                            if str(item.get("status") or "")
                            == filtros["status"]
                        ),
                        0,
                    )
                if quantidade_dia <= 0:
                    continue
                try:
                    data_resultado = date.fromisoformat(
                        str(resumo_dia.get("data") or "")[:10]
                    )
                except ValueError:
                    continue
                if data_inicio <= data_resultado <= data_fim:
                    datas_com_resultado.append(data_resultado)

            if datas_com_resultado:
                data_resultado = max(datas_com_resultado)
                if data_resultado != data_selecionada:
                    filtros_redirecionamento = {
                        key: value
                        for key, value in filtros.items()
                        if key not in {"data_inicio", "data_fim"} and value
                    }
                    query_redirecionamento = urlencode({
                        **filtros_redirecionamento,
                        "data_referencia": data_referencia.isoformat(),
                        "data_selecionada": data_resultado.isoformat(),
                    })
                    return redirect(
                        f"{request.path}?{query_redirecionamento}"
                    )
    except ApiError as exc:
        messages.error(
            request,
            "Acompanhamento particular: "
            f"{extract_api_error_message(exc)}",
        )

    solicitacoes_acompanhamento = []
    for atendimento in atendimentos:
        status = str(atendimento.get("status") or "")
        status_label, status_classe = (
            STATUS_ACOMPANHAMENTO_PARTICULAR.get(
                status,
                ("Status não informado", "pendente"),
            )
        )
        atendimento["status_label"] = status_label
        atendimento["status_classe"] = status_classe
        atendimento["data_atendimento_formatada"] = format_api_date(
            atendimento.get("data_atendimento")
        )
        atendimento["solicitada_em_formatada"] = format_api_datetime(
            atendimento.get("solicitada_em")
        )
        atendimento["atualizada_em_formatada"] = format_api_datetime(
            atendimento.get("atualizada_em")
        )
        atendimento["valor_conta_formatado"] = (
            format_brl_input(atendimento.get("valor_conta"))
            or "R$ 0,00"
        )
        solicitacao_api = atendimento.get("solicitacao") or {}
        solicitacao = dict(solicitacao_api)
        solicitacao.setdefault(
            "codigo_atendimento",
            atendimento.get("codigo_atendimento"),
        )
        solicitacao.setdefault(
            "nm_paciente",
            atendimento.get("nome_paciente"),
        )
        solicitacao.setdefault("nr_cpf", atendimento.get("nr_cpf"))
        solicitacao.setdefault(
            "tipo_atendimento",
            atendimento.get("tipo_atendimento"),
        )
        solicitacao.setdefault(
            "convenio",
            atendimento.get("convenio"),
        )
        solicitacao["id"] = as_int_or_zero(
            solicitacao.get("id")
        )
        solicitacao["data_criacao_formatada"] = format_api_datetime(
            solicitacao.get("data_criacao")
        )
        solicitacao["valor_nota_formatado"] = (
            format_brl_input(solicitacao.get("valor_nota"))
            or atendimento["valor_conta_formatado"]
        )
        solicitacao["validado_em_formatada"] = format_api_datetime(
            solicitacao.get("validado_em")
        )
        solicitacao["inativado_em_formatada"] = format_api_datetime(
            solicitacao.get("inativado_em")
        )
        procedimentos_atendimento = (
            solicitacao.get("procedimentos_atendimento") or []
        )
        for procedimento in procedimentos_atendimento:
            procedimento["realizado_em_formatado"] = (
                format_api_datetime(procedimento.get("realizado_em"))
            )
        total_procedimentos = solicitacao.get(
            "valor_total_procedimentos"
        )
        if total_procedimentos is None:
            total_procedimentos = _somar_procedimentos_atendimento(
                procedimentos_atendimento
            )
        solicitacao["valor_total_procedimentos_formatado"] = (
            format_brl_input(total_procedimentos) or "R$ 0,00"
        )
        _preparar_historico_solicitacoes(
            solicitacao.get("solicitacoes_anteriores") or []
        )
        local_label = LOCAIS_SOLICITACAO_NOTA.get(
            solicitacao.get("local"),
            solicitacao.get("local") or "Não informado",
        )
        if str(local_label).strip().casefold() in {
            "não informado",
            "nao informado",
        }:
            local_label = "SEM"
        solicitacao["local_label"] = local_label
        solicitacao["status"] = status
        solicitacao["status_label"] = status_label
        solicitacao["status_classe"] = status_classe
        solicitacao["pode_validar"] = bool(
            solicitacao["id"]
            and status == "PENDENTE_VALIDACAO"
        )
        solicitacao["pode_solicitar"] = bool(
            not solicitacao["id"] and status == "SEM_SOLICITACAO"
        )
        solicitacao["nfse_emitida"] = status in {
            "EMITIDA",
            "EMITIDA_DIRETAMENTE_ISS",
        }
        solicitacao["nfse_externa"] = (
            status == "EMITIDA_DIRETAMENTE_ISS"
        )
        solicitacao["emissao_id"] = atendimento.get("emissao_id")
        solicitacao["lote_id"] = atendimento.get("lote_id")
        solicitacao["status_emissao"] = atendimento.get(
            "emissao_status"
        )
        solicitacao["status_emissao_label"] = STATUS_EMISSAO_NFSE.get(
            str(atendimento.get("emissao_status") or ""),
            "Não iniciada",
        )
        solicitacao["cnpj_emissor_formatado"] = format_cnpj(
            atendimento.get("cnpj_emissor")
            or solicitacao.get("cnpj_emissor")
        )
        solicitacao["razao_social_emissor"] = (
            atendimento.get("razao_social_emissor")
            or solicitacao.get("razao_social_emissor")
        )
        solicitacao["arquivo_disponivel"] = atendimento.get(
            "arquivo_disponivel"
        )
        solicitacao["numero_nfse"] = atendimento.get("numero_nfse")
        solicitacao["codigo_verificacao_nfse"] = atendimento.get(
            "codigo_verificacao_nfse"
        )
        solicitacao["valor_nfse_formatado"] = (
            format_brl_input(atendimento.get("valor_nfse"))
            or solicitacao["valor_nota_formatado"]
        )
        solicitacao["nfse_externa_row_hash"] = atendimento.get(
            "nfse_externa_row_hash"
        )
        solicitacao["protocolo"] = atendimento.get("protocolo")
        solicitacao["erro_emissao"] = atendimento.get("erro_emissao")
        solicitacao["emissao_atualizada_em_formatada"] = (
            format_api_datetime(
                atendimento.get("emissao_atualizada_em")
                or atendimento.get("atualizada_em")
            )
        )
        solicitacao["data_atendimento_formatada"] = atendimento[
            "data_atendimento_formatada"
        ]
        solicitacoes_acompanhamento.append(solicitacao)

    resumo_por_status = {
        str(item.get("status") or ""): item
        for item in resumo_dia_selecionado_api
    }

    def quantidade_status(*status):
        return sum(
            as_int_or_zero(
                (resumo_por_status.get(item) or {}).get("quantidade")
            )
            for item in status
        )

    def valor_status(*status):
        total_status = Decimal("0")
        for item in status:
            try:
                total_status += Decimal(
                    str(
                        (resumo_por_status.get(item) or {}).get(
                            "valor_total"
                        )
                        or "0"
                    )
                )
            except (InvalidOperation, TypeError, ValueError):
                continue
        return total_status

    def detalhe_valor(valor):
        valor_formatado = format_brl_input(valor) or "R$ 0,00"
        return f"Valor total: {valor_formatado}"

    resumo_cards = [{
        "label": "Total Particular + Prontorede",
        "quantidade": total_dia_selecionado,
        "detalhe": detalhe_valor(valor_total_dia_selecionado),
        "classe": "total",
    }]
    configuracao_cards_status = (
        ("SEM_SOLICITACAO", "Sem solicitação", "sem-solicitacao", True),
        ("PENDENTE_VALIDACAO", "Aguardando validação", "pendente", False),
        ("RECUSADA", "Solicitações recusadas", "recusada", False),
        ("VALIDADA", "Atendimentos validados", "validada", True),
        ("ERRO_EMISSAO", "Emissões com erro", "erro", True),
        ("PENDENTE_EMISSAO", "Aguardando emissão", "emissao", False),
        ("PROCESSANDO", "Em processamento", "emissao", False),
        ("EMITIDA", "Com nota emitida", "emitida", True),
        (
            "EMITIDA_DIRETAMENTE_ISS",
            "Emitidas diretamente no ISS",
            "emitida",
            True,
        ),
        ("INATIVA", "Solicitações inativas", "inativa", False),
    )
    for status, label, classe, exibir_sem_registros in (
        configuracao_cards_status
    ):
        quantidade = quantidade_status(status)
        if not exibir_sem_registros and not quantidade:
            continue
        resumo_cards.append({
            "label": label,
            "quantidade": quantidade,
            "detalhe": detalhe_valor(valor_status(status)),
            "classe": classe,
        })

    valor_emitido = valor_status(
        "EMITIDA",
        "EMITIDA_DIRETAMENTE_ISS",
    )
    try:
        valor_total_dia_decimal = Decimal(
            str(valor_total_dia_selecionado or "0")
        )
    except (InvalidOperation, TypeError, ValueError):
        valor_total_dia_decimal = Decimal("0")
    valor_nao_emitido = max(
        valor_total_dia_decimal - valor_emitido,
        Decimal("0"),
    )
    quantidade_emitida = quantidade_status(
        "EMITIDA",
        "EMITIDA_DIRETAMENTE_ISS",
    )
    quantidade_nao_emitida = max(
        total_dia_selecionado - quantidade_emitida,
        0,
    )
    percentual_emitido = (
        min(
            max(
                quantidade_emitida / total_dia_selecionado * 100,
                0,
            ),
            100,
        )
        if total_dia_selecionado > 0
        else 0
    )
    emissao_dia = {
        "percentual": round(percentual_emitido, 1),
        "angulo": round(percentual_emitido * 1.8, 2),
        "valor_emitido": (
            format_brl_input(valor_emitido) or "R$ 0,00"
        ),
        "valor_nao_emitido": (
            format_brl_input(valor_nao_emitido) or "R$ 0,00"
        ),
        "quantidade_emitida": quantidade_emitida,
        "quantidade_nao_emitida": quantidade_nao_emitida,
    }

    resumo_diario = {
        str(item.get("data")): item for item in resumo_diario_api
    }
    inicio_grade = data_inicio - timedelta(
        days=(data_inicio.weekday() + 1) % 7
    )
    fim_grade = data_fim + timedelta(
        days=(5 - data_fim.weekday()) % 7
    )
    dias_grade = []
    cursor = inicio_grade
    while cursor <= fim_grade:
        resumo_dia = resumo_diario.get(cursor.isoformat()) or {}
        total_dia = as_int_or_zero(resumo_dia.get("total"))
        emitidas_dia = as_int_or_zero(resumo_dia.get("emitidas"))
        pacientes_dia = []
        cores_pacientes = set()
        for paciente in resumo_dia.get("pacientes") or []:
            status_paciente = str(paciente.get("status") or "")
            _label, classe_paciente = (
                STATUS_ACOMPANHAMENTO_PARTICULAR.get(
                    status_paciente,
                    ("Status não informado", "pendente"),
                )
            )
            nome_paciente = str(
                paciente.get("nome")
                or paciente.get("inicial")
                or ""
            )
            indice_cor = (
                int(
                    sha256(
                        nome_paciente.upper().encode("utf-8")
                    ).hexdigest()[:2],
                    16,
                )
                % 8
            ) + 1
            while indice_cor in cores_pacientes:
                indice_cor = (indice_cor % 8) + 1
            cores_pacientes.add(indice_cor)
            pacientes_dia.append({
                **paciente,
                "classe": classe_paciente,
                "cor": f"cor-{indice_cor}",
            })
        dias_grade.append({
            "data": cursor,
            "dia": cursor.day,
            "data_formatada": cursor.strftime("%d/%m/%Y"),
            "fora_periodo": cursor < data_inicio or cursor > data_fim,
            "hoje": cursor == hoje,
            "selecionado": cursor == data_selecionada,
            "total": total_dia,
            "emitidas": emitidas_dia,
            "pendentes": max(total_dia - emitidas_dia, 0),
            "pacientes": pacientes_dia[:3],
            "pacientes_restantes": as_int_or_zero(
                resumo_dia.get("pacientes_restantes")
            ),
            "valor_total": (
                format_brl_input(resumo_dia.get("valor_total"))
                or "R$ 0,00"
            ),
        })
        cursor += timedelta(days=1)

    filtros_navegacao = {
        key: value
        for key, value in filtros.items()
        if key not in {"data_inicio", "data_fim"} and value
    }
    referencia_anterior = data_inicio - timedelta(days=1)
    referencia_proxima = data_fim + timedelta(days=1)

    def dia_equivalente_no_mes(referencia):
        ultimo_dia = monthrange(
            referencia.year,
            referencia.month,
        )[1]
        return referencia.replace(
            day=min(data_selecionada.day, ultimo_dia)
        )

    def url_calendario(referencia, dia_selecionado):
        return "?" + urlencode({
            **filtros_navegacao,
            "data_referencia": referencia.isoformat(),
            "data_selecionada": dia_selecionado.isoformat(),
        })

    for dia_grade in dias_grade:
        if dia_grade["selecionado"]:
            dia_grade["url"] = url_calendario(hoje, hoje)
            dia_grade["titulo_acao"] = (
                "Retirar seleção e exibir o dia atual"
            )
        else:
            dia_grade["url"] = url_calendario(
                dia_grade["data"],
                dia_grade["data"],
            )
            dia_grade["titulo_acao"] = (
                f"Exibir atendimentos de {dia_grade['data_formatada']}"
            )

    base_query = {
        **filtros_navegacao,
        "data_referencia": data_referencia.isoformat(),
        "data_selecionada": data_selecionada.isoformat(),
    }
    total_pages = max(ceil(total / limit), 1)
    if page > total_pages:
        return redirect(
            f"{request.path}?"
            f"{urlencode({**base_query, 'page': total_pages})}"
        )
    pagination = {
        "page": page,
        "total_pages": total_pages,
        "page_options": [
            {"number": number, "selected": number == page}
            for number in range(1, total_pages + 1)
        ],
        "has_previous": page > 1,
        "has_next": page < total_pages,
        "previous_url": (
            f"?{urlencode({**base_query, 'page': page - 1})}"
            if page > 1
            else ""
        ),
        "next_url": (
            f"?{urlencode({**base_query, 'page': page + 1})}"
            if page < total_pages
            else ""
        ),
        "start": offset + 1 if atendimentos and total else 0,
        "end": min(offset + len(atendimentos), total),
        "total": total,
        "query": base_query,
    }
    periodo_formatado = data_referencia.strftime("%m/%Y")
    if not any(
        solicitacao["pode_validar"]
        for solicitacao in solicitacoes_acompanhamento
    ):
        empresas_emissoras = []
    return render(
        request,
        "acompanhamento_particular.html",
        {
            "atendimentos": atendimentos,
            "solicitacoes": solicitacoes_acompanhamento,
            "filtros": filtros,
            "tipos_atendimento": TIPOS_ATENDIMENTO,
            "convenios_options": (
                CONVENIOS_ACOMPANHAMENTO_PARTICULAR.items()
            ),
            "status_options": [
                (status, label)
                for status, (label, _classe)
                in STATUS_ACOMPANHAMENTO_PARTICULAR.items()
            ],
            "resumo_cards": resumo_cards,
            "emissao_dia": emissao_dia,
            "dias_grade": dias_grade,
            "pagination": pagination,
            "periodo_formatado": periodo_formatado,
            "visao": visao,
            "data_referencia": data_referencia.isoformat(),
            "data_selecionada": data_selecionada,
            "data_selecionada_formatada": (
                data_selecionada.strftime("%d/%m/%Y")
            ),
            "anterior_url": url_calendario(
                referencia_anterior,
                dia_equivalente_no_mes(referencia_anterior),
            ),
            "proxima_url": url_calendario(
                referencia_proxima,
                dia_equivalente_no_mes(referencia_proxima),
            ),
            "hoje_url": url_calendario(hoje, hoje),
            "limpar_url": "?" + urlencode({
                "data_referencia": hoje.isoformat(),
                "data_selecionada": hoje.isoformat(),
            }),
            "empresas_emissoras": empresas_emissoras,
            "weekday_labels": ("Dom", "Seg", "Ter", "Qua", "Qui", "Sex", "Sáb"),
            "ha_processamento": any(
                atendimento.get("status")
                in {"PENDENTE_EMISSAO", "PROCESSANDO"}
                for atendimento in atendimentos
            ),
        },
    )


@require_http_methods(["GET", "POST"])
def emissao_nfse(request):
    if request.method == "POST":
        form_action = (
            request.POST.get("form_action") or "emitir"
        ).strip()
        if form_action == "recusar":
            solicitacao_id = as_int_or_zero(
                request.POST.get("solicitacao_id")
            )
            motivo_recusa = (
                request.POST.get("motivo_recusa") or ""
            ).strip()
            try:
                if solicitacao_id <= 0 or not motivo_recusa:
                    raise ValueError
                api_post(
                    f"{REQUISICOES_NOTA_PATH}/solicitacoes-nota/"
                    f"{solicitacao_id}/validacao",
                    {
                        "decisao": "RECUSADA",
                        "motivo_recusa": motivo_recusa,
                    },
                )
                messages.success(
                    request,
                    "Solicitação revertida para recusa com sucesso.",
                )
                return redirect(request.get_full_path())
            except ValueError:
                messages.error(
                    request,
                    "Informe o motivo para reverter a solicitação.",
                )
            except ApiError as exc:
                messages.error(
                    request,
                    f"Reversão da solicitação: "
                    f"{extract_api_error_message(exc)}",
                )
        else:
            ids_raw = request.POST.getlist("solicitacao_ids")
            if not ids_raw and request.POST.get("solicitacao_id"):
                ids_raw = [request.POST.get("solicitacao_id")]
            try:
                solicitacao_ids = [
                    int(value) for value in ids_raw if int(value) > 0
                ]
                if not solicitacao_ids:
                    raise ValueError
                response = api_post(
                    EMISSOES_NFSE_PATH,
                    {"solicitacao_ids": solicitacao_ids},
                )
                messages.success(
                    request,
                    response.get("message")
                    or "Emissão encaminhada ao Airflow.",
                )
                return redirect("emissao_nfse")
            except (TypeError, ValueError):
                messages.error(
                    request,
                    "Selecione pelo menos uma solicitação para emissão.",
                )
            except ApiError as exc:
                messages.error(
                    request,
                    f"Emissão de NFS-e: {extract_api_error_message(exc)}",
                )

    filtros = {
        "nome_paciente": (
            request.GET.get("nome_paciente") or ""
        ).strip(),
        "cpf": (request.GET.get("cpf") or "").strip(),
        "tipo_atendimento": (
            request.GET.get("tipo_atendimento") or ""
        ).strip(),
        "local": (request.GET.get("local") or "").strip(),
        "cnpj_emissor": (
            request.GET.get("cnpj_emissor") or ""
        ).strip(),
    }
    context = _carregar_emissoes_nfse(request, filtros)
    if redirect_url := context.get("redirect_url"):
        return redirect(redirect_url)
    context["empresas_emissoras"] = _carregar_empresas_emissoras(
        request,
        incluir_inativas=True,
    )
    return render(request, "emissao_nfse.html", context)


@require_http_methods(["GET"])
def emissao_nfse_pdf(request, emissao_id):
    download = (
        "true"
        if (request.GET.get("download") or "").strip().lower()
        in {"1", "true", "yes", "on"}
        else "false"
    )
    try:
        upstream = api_get_stream(
            f"{EMISSOES_NFSE_PATH}/itens/{emissao_id}/pdf",
            {"download": download},
        )
    except ApiError as exc:
        status_code = exc.status_code or 502
        if not 400 <= status_code <= 599:
            status_code = 502
        return HttpResponse(
            f"Download da NFS-e: {extract_api_error_message(exc)}",
            status=status_code,
            content_type="text/plain; charset=utf-8",
        )

    def iter_pdf():
        try:
            for chunk in upstream.iter_content(chunk_size=64 * 1024):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    response = StreamingHttpResponse(
        iter_pdf(),
        content_type=(
            upstream.headers.get("Content-Type")
            or "application/pdf"
        ),
    )
    response["Content-Disposition"] = (
        upstream.headers.get("Content-Disposition")
        or (
            f'{"attachment" if download == "true" else "inline"}; '
            f'filename="nfse-{emissao_id}.pdf"'
        )
    )
    if content_length := upstream.headers.get("Content-Length"):
        response["Content-Length"] = content_length
    response["X-Content-Type-Options"] = "nosniff"
    return response


def nfse_externa_pdf(request, row_hash):
    download = (
        "true"
        if (request.GET.get("download") or "").strip().lower()
        in {"1", "true", "yes", "on"}
        else "false"
    )
    try:
        upstream = api_get_stream(
            f"{NFSE_EXTERNAS_PATH}/{row_hash}/pdf",
            {"download": download},
        )
    except ApiError as exc:
        status_code = exc.status_code or 502
        if not 400 <= status_code <= 599:
            status_code = 502
        return HttpResponse(
            f"PDF da NFS-e externa: {extract_api_error_message(exc)}",
            status=status_code,
            content_type="text/plain; charset=utf-8",
        )

    def iter_pdf():
        try:
            for chunk in upstream.iter_content(chunk_size=64 * 1024):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    response = StreamingHttpResponse(
        iter_pdf(),
        content_type=(
            upstream.headers.get("Content-Type")
            or "application/pdf"
        ),
    )
    response["Content-Disposition"] = (
        upstream.headers.get("Content-Disposition")
        or (
            f'{"attachment" if download == "true" else "inline"}; '
            f'filename="nfse-iss-{row_hash[:12]}.pdf"'
        )
    )
    if content_length := upstream.headers.get("Content-Length"):
        response["Content-Length"] = content_length
    response["X-Content-Type-Options"] = "nosniff"
    return response


@require_http_methods(["GET"])
def consultar_atendimento_nota(request, codigo_atendimento):
    try:
        payload = get_cached_atendimento_nota(codigo_atendimento)
        return JsonResponse(payload)
    except ApiError as exc:
        return JsonResponse(
            {"detail": extract_api_error_message(exc)},
            status=exc.status_code or 502,
        )


def format_api_date(value):
    if not value:
        return "-"
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y")
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")

    text = str(value).strip()
    if not text:
        return "-"

    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).strftime("%d/%m/%Y")
    except ValueError:
        pass

    if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-":
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
        except ValueError:
            return text[:10]

    return text


def format_api_month_year(value):
    if not value:
        return "-"
    if isinstance(value, datetime | date):
        return value.strftime("%m/%Y")

    text = str(value).strip()
    if not text:
        return "-"

    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).strftime("%m/%Y")
    except ValueError:
        pass

    if len(text) >= 7 and text[4:5] == "-":
        try:
            return datetime.strptime(text[:7], "%Y-%m").strftime("%m/%Y")
        except ValueError:
            return text

    return text


def format_api_date_input(value):
    if not value:
        return ""
    if isinstance(value, datetime | date):
        return value.strftime("%Y-%m-%d")

    text = str(value).strip()
    if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-":
        return text[:10]
    return ""


def format_api_datetime(value):
    if not value:
        return "-"
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y %H:%M:%S")

    text = str(value).strip()
    if not text:
        return "-"

    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).strftime("%d/%m/%Y %H:%M:%S")
    except ValueError:
        pass

    return text


def format_lancamento_datetime(dt_lancamento, hr_lancamento):
    formatted = format_api_datetime(hr_lancamento)
    if formatted != "-" and "/" in formatted:
        return formatted

    data = format_api_date(dt_lancamento)
    if data == "-":
        return formatted
    if formatted == "-":
        return data
    return f"{data} {formatted}"


def format_conta_bancaria_label(conta, conta_bancaria_id):
    if not conta:
        return f"Conta #{conta_bancaria_id}"

    agencia = str(conta.get("agencia") or "-")
    if conta.get("digito_agencia"):
        agencia += f"-{conta['digito_agencia']}"
    numero_conta = str(conta.get("conta") or "-")
    if conta.get("digito"):
        numero_conta += f"-{conta['digito']}"
    return (
        f"{conta.get('banco') or 'Banco'} · "
        f"Ag. {agencia} · C/C {numero_conta}"
    )


def format_api_error(exc: ApiError, endpoint_name: str) -> str:
    if exc.status_code == 401:
        return f"{endpoint_name}: sua sessão não é mais válida. Entre novamente."
    if exc.status_code == 404:
        return f"{endpoint_name}: endpoint ainda nao encontrado na API."
    return f"{endpoint_name}: {extract_api_error_message(exc)}"


def clean_api_validation_message(message):
    text = str(message or "").strip()
    prefixes = ("Value error, ", "value_error, ")
    for prefix in prefixes:
        if text.startswith(prefix):
            return text[len(prefix) :].strip()
    return text


def extract_api_error_message(exc: ApiError) -> str:
    text = str(exc).strip()
    if not text:
        return "A API não retornou detalhes do erro."

    try:
        payload = json.loads(text)
    except ValueError:
        return text

    detail = payload.get("detail") if isinstance(payload, dict) else payload
    if isinstance(detail, str):
        return clean_api_validation_message(detail)
    if isinstance(detail, dict):
        message = detail.get("msg") or detail.get("message") or detail.get("detail")
        if message:
            return clean_api_validation_message(message)
    if isinstance(detail, list):
        messages = []
        for item in detail:
            if isinstance(item, dict):
                message = item.get("msg") or item.get("message") or item.get("detail")
                if message:
                    messages.append(clean_api_validation_message(message))
            elif item:
                messages.append(clean_api_validation_message(item))
        if messages:
            return " ".join(messages)

    return text


def contextualize_registro_glosa_error(message, is_acatar=False):
    if is_acatar:
        replacements = {
            "valor glosado/acatado": "valor acatado",
            "Valor glosado/acatado": "Valor acatado",
            "quantidade glosada/acatada": "quantidade acatada",
            "Quantidade glosada/acatada": "Quantidade acatada",
        }
    else:
        replacements = {
            "valor glosado/acatado": "valor recursado",
            "Valor glosado/acatado": "Valor recursado",
            "quantidade glosada/acatada": "quantidade recusada",
            "Quantidade glosada/acatada": "Quantidade recusada",
        }

    text = str(message or "")
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def is_service_unavailable_error(exc: ApiError) -> bool:
    text = str(exc).lower()
    unavailable_terms = (
        "timeout",
        "timed out",
        "ora-",
        "oracle",
        "banco",
        "database",
        "connection",
    )
    return exc.status_code is None or exc.status_code >= 500 or any(term in text for term in unavailable_terms)


def is_browser_reload(request):
    if request.GET.get("_modal_action") == "1":
        return False

    cache_control = request.headers.get("Cache-Control", "").lower()
    pragma = request.headers.get("Pragma", "").lower()
    return (
        "max-age=0" in cache_control
        or "no-cache" in cache_control
        or pragma == "no-cache"
    )


def with_modal_action_marker(full_path):
    parts = urlsplit(full_path)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["_modal_action"] = "1"
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            urlencode(query),
            parts.fragment,
        )
    )


def is_ajax_request(request):
    return request.headers.get("x-requested-with") == "XMLHttpRequest"


def modal_action_response(request, message, tag, status=200, api_payload=None):
    if is_ajax_request(request):
        return JsonResponse(
            {
                "ok": status < 400,
                "message": message,
                "tag": tag,
                "payload": api_payload,
            },
            status=status,
        )

    getattr(messages, "error" if tag == "error" else tag)(request, message)
    return redirect(with_modal_action_marker(request.get_full_path()))


def _group_itens_by_grupo_faturamento(itens):
    grupos = {}
    ordem = []
    for item in itens:
        grupo = item.get("ds_gru_fat") or "Grupo nao informado"
        if grupo not in grupos:
            grupos[grupo] = []
            ordem.append(grupo)
        grupos[grupo].append(item)

    return [
        {
            "ds_gru_fat": grupo,
            "itens": grupos[grupo],
            "num_lancamentos": len(grupos[grupo]),
        }
        for grupo in ordem
    ]


def _group_contas(contas):
    """Group a flat list of contas by nm_paciente, cd_remessa and cd_atendimento."""
    by_paciente = {}
    order_paciente = []
    for conta in contas:
        pac = conta.get("nm_paciente") or "-"
        rem = str(conta.get("cd_remessa") or "-")
        atd = str(conta.get("cd_atendimento") or "-")
        if pac not in by_paciente:
            by_paciente[pac] = {}
            order_paciente.append(pac)
        if rem not in by_paciente[pac]:
            by_paciente[pac][rem] = {}
        if atd not in by_paciente[pac][rem]:
            by_paciente[pac][rem][atd] = []
        by_paciente[pac][rem][atd].append(conta)

    result = []
    for pac in order_paciente:
        remessas = []
        pac_total = 0.0
        pac_lancamentos = 0
        pac_convenios = set()
        pac_atendimentos = 0
        for rem, atendimentos_por_remessa in by_paciente[pac].items():
            atendimentos = []
            rem_total = 0.0
            rem_lancamentos = 0
            rem_convenios = set()
            rem_procedimentos = set()
            for atd, itens in atendimentos_por_remessa.items():
                atd_total = 0.0
                atd_convenios = set()
                atd_procedimentos = set()
                for item in itens:
                    try:
                        atd_total += float(item.get("vl_total_conta") or 0)
                    except (TypeError, ValueError):
                        pass
                    conv = item.get("nm_convenio")
                    if conv:
                        atd_convenios.add(conv)
                    proc = item.get("cd_pro_fat")
                    if proc:
                        atd_procedimentos.add(str(proc))
                rem_total += atd_total
                rem_lancamentos += len(itens)
                rem_convenios |= atd_convenios
                rem_procedimentos |= atd_procedimentos
                primeiro_item = itens[0] if itens else {}
                atendimentos.append({
                    "cd_atendimento": atd,
                    "itens": itens,
                    "total": atd_total,
                    "num_lancamentos": len(itens),
                    "convenios": sorted(atd_convenios),
                    "procedimentos": sorted(atd_procedimentos),
                    "grupos_faturamento": _group_itens_by_grupo_faturamento(
                        itens
                    ),
                    "dt_atendimento": primeiro_item.get(
                        "dt_atendimento_formatada"
                    ),
                    "dt_alta": primeiro_item.get("dt_alta_formatada"),
                })
            pac_total += rem_total
            pac_lancamentos += rem_lancamentos
            pac_atendimentos += len(atendimentos)
            pac_convenios |= rem_convenios
            remessas.append({
                "cd_remessa": rem,
                "atendimentos": atendimentos,
                "num_atendimentos": len(atendimentos),
                "num_lancamentos": rem_lancamentos,
                "total": rem_total,
                "convenios": sorted(rem_convenios),
                "procedimentos": sorted(rem_procedimentos),
            })
        result.append({
            "nm_paciente": pac,
            "remessas": remessas,
            "num_remessas": len(remessas),
            "num_atendimentos": pac_atendimentos,
            "num_lancamentos": pac_lancamentos,
            "total": pac_total,
            "convenios": sorted(pac_convenios),
        })
    return result


def as_list(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("atendimentos", "items", "results", "contas", "dados", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
        return [payload]
    return []


def as_positive_int(value, default=1):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def as_int_or_zero(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def as_int_or_none(value):
    if value in (None, ""):
        return None

    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def as_float_or_zero(value):
    text = str(value or "").strip()
    text = "".join(char for char in text if char.isdigit() or char in ",.-")
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return float(text)
    except (TypeError, ValueError):
        return 0.0


def as_float_or_none(value):
    if value in (None, ""):
        return None

    text = str(value).strip()
    text = "".join(char for char in text if char.isdigit() or char in ",.-")
    if "," in text:
        text = text.replace(".", "").replace(",", ".")

    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def format_brl_input(value):
    if value in (None, ""):
        return ""

    try:
        amount = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return ""

    formatted = f"{amount:,.2f}"
    return f"R$ {formatted}".replace(",", "X").replace(".", ",").replace("X", ".")


def format_brl_compact(value):
    amount = as_float_or_zero(value)
    if amount >= 1_000_000:
        text = f"{amount / 1_000_000:.1f} mi"
    elif amount >= 1_000:
        text = f"{amount / 1_000:.1f} mil"
    else:
        text = f"{amount:.0f}"
    return f"R$ {text}".replace(".", ",")


def parse_api_date(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    text = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        pass

    if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-":
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


def bucket_reference_date(registro):
    return (
        parse_api_date(registro.get("dt_pagamento"))
        or parse_api_date(registro.get("data_criacao"))
        or parse_api_date(registro.get("dt_recurso"))
        or parse_api_date(registro.get("data_glosa"))
        or date.today()
    )


def age_bucket(registro):
    reference_date = bucket_reference_date(registro)
    days = max((date.today() - reference_date).days, 0)
    if days < 30:
        return "ate_30"
    if days < 60:
        return "ate_60"
    if days <= 90:
        return "ate_90"
    return "mais_90"


def valor_registro_recurso(registro):
    return as_float_or_zero(
        registro.get("valor_recursado")
        if registro.get("valor_recursado") not in (None, "")
        else registro.get("valor_glosado")
        if registro.get("valor_glosado") not in (None, "")
        else registro.get("valor")
    )


def qtd_registro_recurso(registro):
    return as_float_or_zero(
        registro.get("qtd_recursado")
        if registro.get("qtd_recursado") not in (None, "")
        else registro.get("qtd_recursada")
        if registro.get("qtd_recursada") not in (None, "")
        else registro.get("qtd_glosada")
        if registro.get("qtd_glosada") not in (None, "")
        else 1
    )


def qtd_registro_glosada(registro):
    return as_float_or_zero(
        registro.get("qtd_registro")
        if registro.get("qtd_registro") not in (None, "")
        else registro.get(
            "qtd_recursado",
            registro.get("qtd_recursada", registro.get("qtd_glosada")),
        )
    )


def processo_card_key(registro):
    if registro.get("tratativa_pendente") and registro.get(
        "conciliacao_remessa_id"
    ):
        return f"conciliacao-{registro['conciliacao_remessa_id']}"
    return (
        registro.get("processo_recurso")
        or registro.get("processo_controle_fatura_gab")
        or f"registro-{registro.get('id')}"
    )


def build_acompanhamento_rows(registros):
    rows = []
    for registro in registros:
        if not isinstance(registro, dict):
            continue
        if not is_recurso_registro(registro):
            continue
        if not has_internal_treatment(registro):
            continue

        row = dict(registro)
        row["tratativa_pendente"] = False
        row["paciente_label"] = (
            row.get("nm_paciente")
            or f"Paciente {row.get('codigo_paciente') or '-'}"
        )
        row["idade_bucket"] = age_bucket(row)
        row["idade_bucket_label"] = ACOMPANHAMENTO_BUCKETS[row["idade_bucket"]]
        row["qtd_recurso"] = qtd_registro_recurso(row)
        row["qtd_glosada"] = qtd_registro_glosada(row)
        if row.get("qtd_recebida") not in (None, ""):
            row["qtd_recebida"] = as_float_or_zero(row.get("qtd_recebida"))
        else:
            row["qtd_recebida"] = 0
        row["valor_item"] = as_float_or_zero(row.get("valor"))
        row["valor_glosado_total"] = as_float_or_zero(row.get("valor"))
        row["valor_recurso"] = valor_registro_recurso(row)
        row["valor_recebido"] = as_float_or_zero(row.get("valor_recebido"))
        row["possui_recebimento"] = possui_recebimento_registro(row)
        row["recebido"] = is_recebimento_integral_registro(row)
        row["recebimento_parcial"] = is_recebimento_parcial_registro(row)
        row["recebido_label"] = (
            "Sim"
            if row["recebido"]
            else "Parcial"
            if row["recebimento_parcial"]
            else "Não"
        )
        row["valor_em_aberto"] = valor_em_aberto_registro(row)
        row["valor_glosado_total_formatado"] = format_brl_input(
            row["valor_glosado_total"]
        )
        row["valor_item_formatado"] = format_brl_input(row["valor_item"])
        row["valor_recurso_formatado"] = format_brl_input(row["valor_recurso"])
        row["valor_recebido_formatado"] = format_brl_input(row["valor_recebido"])
        row["valor_em_aberto_formatado"] = format_brl_input(
            row["valor_em_aberto"]
        )
        row["dt_recebimento_input"] = format_api_date_input(
            row.get("dt_recebimento")
        )
        row["dt_recebimento_formatada"] = format_api_date(
            row.get("dt_recebimento")
        )
        row["dt_recebimento_modal"] = (
            row["dt_recebimento_input"] if row["possui_recebimento"] else ""
        )
        row["valor_recebido_modal"] = (
            row["valor_recebido_formatado"]
            if row["possui_recebimento"]
            else ""
        )
        row["qtd_recebida_modal"] = (
            str(int(row["qtd_recebida"]))
            if row["possui_recebimento"]
            else ""
        )
        row["observacao_recebimento_modal"] = (
            (row.get("observacao_recebimento") or "")
            if row["possui_recebimento"]
            else ""
        )
        row["data_glosa_formatada"] = format_api_date(row.get("data_glosa"))
        rows.append(row)
    return rows


ACOMPANHAMENTO_BUCKETS = {
    "ate_30": "Até 30 dias",
    "ate_60": "Até 60 dias",
    "ate_90": "Até 90 dias",
    "mais_90": "Há +90 dias",
    "recebidas": "Glosas recebidas",
}


def unique_join(values):
    normalized = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in normalized:
            normalized.append(text)
    return ", ".join(normalized) or "-"


def build_acompanhamento_cards(rows):
    grouped = {}
    for row in rows:
        key = processo_card_key(row)
        grouped.setdefault(key, []).append(row)

    cards = []
    for key, itens in grouped.items():
        single_item = itens[0] if len(itens) == 1 else None
        all_received = all(
            possui_recebimento_registro(item) for item in itens
        )
        oldest_item = min(itens, key=bucket_reference_date)
        bucket_key = (
            "recebidas"
            if all_received
            else age_bucket(oldest_item)
        )
        total_recurso = sum(item["valor_recurso"] for item in itens)
        valor_recebimento_maximo = min(
            (item["valor_recurso"] for item in itens),
            default=0,
        )
        total_recebido = sum(
            as_float_or_zero(item.get("valor_recebido"))
            for item in itens
            if possui_recebimento_registro(item)
        )
        total_em_aberto = sum(
            valor_em_aberto_registro(item) for item in itens
        )
        total = total_recebido if bucket_key == "recebidas" else total_em_aberto
        qtd = sum(item["qtd_recurso"] for item in itens)
        reference_date = bucket_reference_date(oldest_item)
        cards.append(
            {
                "key": str(key),
                "bucket": bucket_key,
                "reference_date": reference_date.isoformat(),
                "ids": ",".join(str(item["id"]) for item in itens if item.get("id")),
                "processos_originais": unique_join(
                    item.get("processo_controle_fatura_gab") for item in itens
                ),
                "processo_recurso": unique_join(
                    item.get("processo_recurso") for item in itens
                ),
                "tratativa_pendente": False,
                "remessas": unique_join(item.get("cd_remessa") for item in itens),
                "atendimentos": unique_join(
                    item.get("cd_atendimento") for item in itens
                ),
                "datas_glosa": unique_join(
                    item.get("data_glosa_formatada") for item in itens
                ),
                "pacientes": unique_join(item.get("paciente_label") for item in itens),
                "convenios": unique_join(item.get("convenio") for item in itens),
                "qtd_total": qtd,
                "valor_total": total,
                "valor_recurso_total": total_recurso,
                "valor_em_aberto_total": total_em_aberto,
                "valor_recebimento_maximo": valor_recebimento_maximo,
                "valor_recebido_total": total_recebido,
                "valor_total_formatado": format_brl_input(total),
                "possui_recebimento": bool(
                    single_item
                    and single_item.get("possui_recebimento")
                ),
                "dt_recebimento_modal": (
                    single_item.get("dt_recebimento_modal", "")
                    if single_item
                    else ""
                ),
                "valor_recebido_modal": (
                    single_item.get("valor_recebido_modal", "")
                    if single_item
                    else ""
                ),
                "qtd_recebida_modal": (
                    single_item.get("qtd_recebida_modal", "")
                    if single_item
                    else ""
                ),
                "observacao_recebimento_modal": (
                    single_item.get("observacao_recebimento_modal", "")
                    if single_item
                    else ""
                ),
                "itens": itens,
                "has_mini_table": len(itens) > 1,
            }
        )
    return cards


def build_kanban_columns(cards):
    columns = []
    for key, label in ACOMPANHAMENTO_BUCKETS.items():
        column_cards = [card for card in cards if card["bucket"] == key]
        valor_total = sum(card["valor_total"] for card in column_cards)
        columns.append(
            {
                "key": key,
                "label": label,
                "cards": column_cards,
                "valor_total": valor_total,
                "valor_total_formatado": format_brl_input(valor_total),
            }
        )
    return columns


def build_acompanhamento_resumo(rows):
    cards = build_acompanhamento_cards(rows)
    rows_recebidas = [
        row for row in rows if possui_recebimento_registro(row)
    ]
    rows_com_recebimento = [
        row for row in rows if possui_recebimento_registro(row)
    ]
    cards_em_aberto = [
        card
        for card in cards
        if card["bucket"] != "recebidas" and card["valor_total"] > 0
    ]
    return {
        "processos": len(cards),
        "registros": len(rows),
        "valor_total": sum(row["valor_recurso"] for row in rows),
        "recebidos": len(rows_recebidas),
        "em_aberto": len(cards_em_aberto),
        "valor_recebido_total": sum(
            row["valor_recebido"] for row in rows_com_recebimento
        ),
        "valor_em_aberto_total": sum(
            card["valor_total"] for card in cards_em_aberto
        ),
    }


def normalize_glosa_match_text(value):
    text = str(value or "").strip()
    if text in {"-", "None", "none", "NULL", "null"}:
        return ""
    return text


def _glosa_match_key(item):
    return (
        str(as_int_or_zero(item.get("cd_remessa"))),
        str(as_int_or_zero(item.get("cd_atendimento"))),
        str(as_int_or_zero(item.get("cd_reg") or item.get("conta"))),
        normalize_glosa_match_text(
            item.get("cd_pro_fat") or item.get("procedimento")
        ),
        normalize_glosa_match_text(
            item.get("nr_guia") or item.get("cd_guia") or item.get("guia")
        ),
        str(as_int_or_zero(item.get("cd_lancamento"))),
    )


def _glosa_match_key_without_guia(item):
    key = _glosa_match_key(item)
    return (*key[:4], key[5])


def _glosa_legacy_match_key(item):
    return _glosa_match_key(item)[:5]


def _glosa_legacy_match_key_without_guia(item):
    return _glosa_match_key(item)[:4]


def _prepare_registro_glosa(registro):
    prepared = dict(registro)
    qtd_recursada = registro.get(
        "qtd_recursado",
        registro.get("qtd_recursada", registro.get("qtd_glosada")),
    )
    prepared["data_glosa_input"] = format_api_date_input(registro.get("data_glosa"))
    prepared["dt_recurso_input"] = format_api_date_input(registro.get("dt_recurso"))
    prepared["dt_pagamento_input"] = format_api_date_input(registro.get("dt_pagamento"))
    prepared["valor_glosado_input"] = format_brl_input(
        registro.get("valor_recursado", registro.get("valor_glosado"))
    )
    try:
        prepared["qtd_glosada_input"] = int(
            float(str(qtd_recursada).replace(",", "."))
        )
    except (TypeError, ValueError):
        prepared["qtd_glosada_input"] = ""
    return prepared


def canonical_glosa_status(registro):
    if registro.get("conciliacao_remessa_id") and not has_internal_treatment(
        registro
    ):
        return "pending"
    if is_acato_registro(registro):
        return "not"
    if is_recurso_registro(registro):
        return "true"
    return normalize_flag(registro.get("sn_glosado"))


def attach_registros_glosa(contas, filtros):
    if not contas:
        return

    params = {
        key: value
        for key, value in filtros.items()
        if key in {"cd_remessa", "cd_atendimento", "cd_reg", "tp_atendimento"} and value
    }
    params["limit"] = 5000
    payload = get_cached_api_payload(
        "conta-atendimento:registros-glosa",
        settings.API_REGISTRO_GLOSA_PATH,
        params,
    )
    registros = payload.get("glosas", []) if isinstance(payload, dict) else []

    registros_por_linha = {}
    registros_por_linha_sem_guia = {}
    registros_legados_por_linha = {}
    registros_legados_por_linha_sem_guia = {}
    chaves_ambiguas_sem_guia = set()
    chaves_legadas_ambiguas_sem_guia = set()
    for registro in registros:
        if not isinstance(registro, dict):
            continue
        key = _glosa_match_key(registro)
        if key not in registros_por_linha:
            prepared = _prepare_registro_glosa(registro)
            registros_por_linha[key] = prepared
            key_sem_guia = _glosa_match_key_without_guia(registro)
            if key_sem_guia in registros_por_linha_sem_guia:
                chaves_ambiguas_sem_guia.add(key_sem_guia)
            else:
                registros_por_linha_sem_guia[key_sem_guia] = prepared
            if as_int_or_zero(registro.get("cd_lancamento")) == 0:
                key_legada = _glosa_legacy_match_key(registro)
                registros_legados_por_linha.setdefault(key_legada, prepared)
                key_legada_sem_guia = (
                    _glosa_legacy_match_key_without_guia(registro)
                )
                if (
                    key_legada_sem_guia
                    in registros_legados_por_linha_sem_guia
                ):
                    chaves_legadas_ambiguas_sem_guia.add(
                        key_legada_sem_guia
                    )
                else:
                    registros_legados_por_linha_sem_guia[
                        key_legada_sem_guia
                    ] = prepared

    for conta in contas:
        if not isinstance(conta, dict):
            continue
        conta["registro_recusa"] = {}
        conta["registro_acato"] = {}
        key_sem_guia = _glosa_match_key_without_guia(conta)
        registro = registros_por_linha.get(_glosa_match_key(conta))
        if not registro and key_sem_guia not in chaves_ambiguas_sem_guia:
            registro = registros_por_linha_sem_guia.get(key_sem_guia)
        if not registro:
            registro = registros_legados_por_linha.get(
                _glosa_legacy_match_key(conta)
            )
        key_legada_sem_guia = _glosa_legacy_match_key_without_guia(conta)
        if (
            not registro
            and key_legada_sem_guia
            not in chaves_legadas_ambiguas_sem_guia
        ):
            registro = registros_legados_por_linha_sem_guia.get(
                key_legada_sem_guia
            )
        if registro:
            conta["registro_glosa"] = registro
            conta["registro_glosa_id"] = registro.get("id")
            conta["registro_glosa_status"] = canonical_glosa_status(registro)
            if is_acato_registro(registro):
                conta["registro_acato"] = registro
            else:
                conta["registro_recusa"] = registro


def build_registro_glosa_payload(data):
    motivo_glosa = str(data.get("motivo_glosa") or "").strip()
    motivo_glosa_codigo = motivo_glosa.split(" - ", 1)[0].strip()
    return {
        "codigo_paciente": as_int_or_zero(data.get("cd_paciente")),
        "nm_paciente": data.get("nm_paciente") or None,
        "cd_remessa": as_int_or_zero(data.get("cd_remessa")),
        "cd_atendimento": as_int_or_zero(data.get("cd_atendimento")),
        "conta": as_int_or_zero(data.get("cd_reg")),
        "cd_lancamento": as_int_or_none(data.get("cd_lancamento")),
        "cd_prestador": as_int_or_zero(data.get("cd_prestador")),
        "cd_convenio": as_int_or_zero(data.get("cd_convenio")),
        "tp_atendimento": data.get("tp_atendimento") or "",
        "procedimento": str(data.get("cd_pro_fat") or ""),
        "cd_tuss": str(data.get("cd_tuss") or "").strip() or None,
        "convenio": data.get("nm_convenio") or "",
        "guia": str(data.get("nr_guia") or data.get("cd_guia") or ""),
        "prestador": data.get("nm_prestador") or "",
        "data_atendimento": data.get("dt_atendimento")
        or data.get("dt_lancamento")
        or None,
        "valor": as_float_or_zero(data.get("vl_total_conta")),
        "sn_glosado": data.get("sn_glosado") or None,
        "processo_controle_fatura_gab": data.get("processo_controle_fatura_gab") or "",
        "processo_recurso": data.get("processo_recurso") or None,
        "data_glosa": data.get("data_glosa") or None,
        "motivo_glosa": motivo_glosa_codigo,
        "descricao_glosa": data.get("descricao_glosa") or "",
        "qtd_registro": as_float_or_none(data.get("qt_lancamento")),
        "descricao_item": data.get("descricao") or None,
        "data_alta": data.get("dt_alta") or None,
        "data_lancamento": data.get("dt_lancamento") or None,
        "cd_gru_pro": as_int_or_none(data.get("cd_gru_pro")),
        "ds_gru_pro": data.get("ds_gru_pro") or None,
        "cd_gru_fat": as_int_or_none(data.get("cd_gru_fat")),
        "ds_gru_fat": data.get("ds_gru_fat") or None,
        "qtd_recursado": as_int_or_none(data.get("qtd_glosada")),
        "valor_recursado": as_float_or_none(data.get("valor_glosado")),
        "dt_recurso": data.get("dt_recurso") or None,
        "dt_pagamento": data.get("dt_pagamento") or None,
    }


def prepare_follow_up_glosas_cards(cards):
    prepared_cards = []
    for card_data in cards:
        card = dict(card_data)
        card["data_competencia_formatada"] = format_api_month_year(
            card.get("data_competencia")
        )
        card["data_entrega_formatada"] = format_api_date(
            card.get("data_entrega")
        )
        card["detalhe_dom_id"] = (
            str(card.get("conciliacao_remessa_id"))
            if card.get("conciliacao_remessa_id")
            else f"cogestao-{card.get('cd_remessa') or 'sem-remessa'}"
        )
        processo = dict(card.get("processo") or {})
        processo["data_abertura_formatada"] = format_api_date(
            processo.get("data_abertura")
        )
        card["processo"] = processo
        fiscal = dict(card.get("fiscal") or {})
        fiscal["data_emissao_formatada"] = format_api_date(
            fiscal.get("data_emissao")
        )
        card["fiscal"] = fiscal
        pacientes = []
        atendimentos_paciente = []
        total_itens = 0
        for paciente_data in card.get("pacientes") or []:
            paciente = dict(paciente_data)
            itens = []
            atendimentos = {}
            ordem_atendimentos = []
            for item_data in paciente.get("itens") or []:
                item = dict(item_data)
                registro_original = item.get("registro_glosa") or {}
                status_original = canonical_glosa_status(
                    registro_original
                )
                registro = _prepare_registro_glosa(
                    registro_original
                )
                registro_recusa = _prepare_registro_glosa(
                    item.get("registro_recusa")
                    or (registro_original if status_original == "true" else {})
                )
                registro_acato = _prepare_registro_glosa(
                    item.get("registro_acato")
                    or (registro_original if status_original == "not" else {})
                )
                registro["dt_pagamento_oculto"] = (
                    registro.get("dt_pagamento_input")
                    or registro.get("data_glosa_input")
                )
                item["registro_glosa"] = registro
                item["registro_recusa"] = registro_recusa
                item["registro_acato"] = registro_acato
                item["registro_glosa_id"] = registro.get("id")
                item["registro_glosa_status"] = canonical_glosa_status(registro)
                item["processo_origem"] = (
                    processo.get("numero_processo")
                    or registro.get("processo_controle_fatura_gab")
                    or item.get("processo_controle_fatura_gab")
                    or ""
                )
                item["data_glosa_input"] = format_api_date_input(
                    item.get("data_glosa")
                )
                item["dt_pagamento_input"] = format_api_date_input(
                    item.get("dt_pagamento")
                )
                item["dt_alta_formatada"] = format_api_date(
                    item.get("dt_alta")
                )
                item["dt_lancamento_formatada"] = format_api_datetime(
                    item.get("dt_lancamento")
                )
                item["dt_atendimento_formatada"] = format_api_date(
                    item.get("dt_atendimento")
                )
                item["codigo_item"] = (
                    str(item.get("cd_tuss") or "").strip()
                    or str(item.get("cd_pro_fat") or "").strip()
                    or str(item.get("codigo_servico") or "").strip()
                )
                itens.append(item)
                atendimento_key = (
                    item.get("cd_atendimento") or 0,
                    item.get("dt_atendimento") or "",
                    item.get("dt_alta") or "",
                    item.get("tp_atendimento") or "",
                )
                if atendimento_key not in atendimentos:
                    atendimentos[atendimento_key] = {
                        "codigo_paciente": paciente.get("codigo_paciente"),
                        "nm_paciente": paciente.get("nm_paciente"),
                        "cd_atendimento": item.get("cd_atendimento") or 0,
                        "tp_atendimento": item.get("tp_atendimento"),
                        "dt_atendimento_formatada": item.get(
                            "dt_atendimento_formatada"
                        ),
                        "dt_alta_formatada": item.get("dt_alta_formatada"),
                        "nm_convenio": item.get("nm_convenio"),
                        "grupos_procedimento_map": {},
                        "ordem_grupos_procedimento": [],
                        "total_itens": 0,
                    }
                    ordem_atendimentos.append(atendimento_key)
                atendimento = atendimentos[atendimento_key]
                grupo_key = (
                    item.get("cd_gru_fat") or 0,
                    item.get("ds_gru_fat") or "Grupo não informado",
                )
                if grupo_key not in atendimento["grupos_procedimento_map"]:
                    atendimento["grupos_procedimento_map"][grupo_key] = {
                        "cd_gru_fat": grupo_key[0],
                        "ds_gru_fat": grupo_key[1],
                        "itens": [],
                    }
                    atendimento["ordem_grupos_procedimento"].append(grupo_key)
                atendimento["grupos_procedimento_map"][grupo_key][
                    "itens"
                ].append(item)
                atendimento["total_itens"] += 1
            atendimentos_preparados = []
            for atendimento_key in ordem_atendimentos:
                atendimento = atendimentos[atendimento_key]
                grupos_procedimento = []
                for grupo_key in atendimento.pop(
                    "ordem_grupos_procedimento"
                ):
                    grupo = atendimento["grupos_procedimento_map"][grupo_key]
                    grupo["total_itens"] = len(grupo["itens"])
                    grupos_procedimento.append(grupo)
                atendimento.pop("grupos_procedimento_map")
                atendimento["grupos_procedimento"] = grupos_procedimento
                atendimento["total_grupos"] = len(grupos_procedimento)
                atendimentos_preparados.append(atendimento)
                atendimentos_paciente.append(atendimento)
            paciente["itens"] = itens
            paciente["atendimentos"] = atendimentos_preparados
            paciente["total_atendimentos"] = len(atendimentos_preparados)
            paciente["total_itens"] = len(itens)
            total_itens += len(itens)
            pacientes.append(paciente)
        card["pacientes"] = pacientes
        card["atendimentos_paciente"] = atendimentos_paciente
        card["total_pacientes"] = len(pacientes)
        card["total_itens"] = total_itens
        prepared_cards.append(card)
    return prepared_cards


def group_follow_up_glosas_by_process(cards):
    grouped = {}
    order = []
    for card in cards:
        processo = dict(card.get("processo") or {})
        numero_processo = str(
            processo.get("numero_processo") or ""
        ).strip()
        key = (
            f"processo:{numero_processo.casefold()}"
            if numero_processo
            else f"remessa:{card.get('conciliacao_remessa_id')}"
        )
        if key not in grouped:
            grouped[key] = {
                "processo": processo,
                "convenio": "-",
                "valor_total": 0.0,
                "valor_glosado": 0.0,
                "valor_total_tratado": 0.0,
                "valor_glosa_pendente": 0.0,
                "remessas": [],
            }
            order.append(key)

        group = grouped[key]
        group["remessas"].append(card)
        group["valor_total"] += as_float_or_zero(card.get("valor_itens"))
        group["valor_glosado"] += as_float_or_zero(card.get("valor_glosado"))
        group["valor_total_tratado"] += as_float_or_zero(
            card.get("valor_total_tratado")
        )
        group["valor_glosa_pendente"] += as_float_or_zero(
            card.get("valor_glosa_pendente")
        )

    process_groups = []
    for key in order:
        group = grouped[key]
        group["remessas"].sort(
            key=lambda card: str(card.get("data_competencia") or ""),
            reverse=True,
        )
        group["convenio"] = unique_join(
            card.get("convenio") for card in group["remessas"]
        )
        group["competencia_producao"] = unique_join(
            card.get("data_competencia_formatada")
            for card in group["remessas"]
        )
        group["total_remessas"] = len(group["remessas"])
        process_groups.append(group)
    process_groups.sort(
        key=lambda group: max(
            (
                str(card.get("data_competencia") or "")
                for card in group["remessas"]
            ),
            default="",
        ),
        reverse=True,
    )
    return process_groups


def normalize_flag(value):
    return str(value or "").strip().lower()


def is_active_registro(registro):
    return normalize_flag(registro.get("sn_ativo")) in {"true", "sim", "s", "1"}


def is_recurso_registro(registro):
    return normalize_flag(registro.get("sn_glosado")) in {"true", "sim", "s", "1"}


def has_internal_treatment(registro):
    return bool(
        registro.get("dt_recurso")
        and (
            is_acato_registro(registro)
            or registro.get("processo_recurso")
        )
    )


def is_pending_conciliation_registro(registro):
    valor_pendente = registro.get("valor_indicador")
    if valor_pendente in (None, ""):
        valor_pendente = registro.get("valor_glosa_pendente")
    return bool(
        registro.get("conciliacao_remessa_id")
        and not has_internal_treatment(registro)
        and as_float_or_zero(valor_pendente) > 0
    )


def is_acato_registro(registro):
    return normalize_flag(registro.get("sn_glosado")) in {
        "not",
        "false",
        "não",
        "nao",
        "n",
        "0",
    }


def possui_recebimento_registro(registro):
    return bool(
        registro.get("dt_recebimento")
        and as_float_or_zero(registro.get("valor_recebido")) > 0
        and as_float_or_zero(registro.get("qtd_recebida")) > 0
    )


def valor_em_aberto_registro(registro):
    valor_recursado = as_float_or_zero(
        registro.get("valor_recurso")
        if registro.get("valor_recurso") not in (None, "")
        else registro_valor_glosado(registro)
    )
    valor_recebido = (
        as_float_or_zero(registro.get("valor_recebido"))
        if possui_recebimento_registro(registro)
        else 0
    )
    return max(valor_recursado - valor_recebido, 0)


def is_recebido_registro(registro):
    return possui_recebimento_registro(registro)


def is_recebimento_integral_registro(registro):
    return bool(
        is_recebido_registro(registro)
        and valor_em_aberto_registro(registro) <= 0.005
    )


def is_recebimento_parcial_registro(registro):
    return bool(
        is_recebido_registro(registro)
        and valor_em_aberto_registro(registro) > 0.005
    )


def registro_valor_glosado(registro):
    return as_float_or_zero(
        registro.get("valor_indicador")
        if registro.get("valor_indicador") not in (None, "")
        else registro.get("valor_recursado")
        if registro.get("valor_recursado") not in (None, "")
        else registro.get("valor_glosado")
        if registro.get("valor_glosado") not in (None, "")
        else registro.get("valor")
    )


def percent_value(part, total):
    if not total:
        return 0
    return round((part / total) * 100, 1)


def percent_int(part, total):
    if not total:
        return 0
    return max(min(round((part / total) * 100), 100), 0)


def aging_days(registro):
    reference = (
        parse_api_date(registro.get("data_glosa"))
        or parse_api_date(registro.get("data_criacao"))
        or date.today()
    )
    return max((date.today() - reference).days, 0)


def aging_bucket_key(days):
    if days <= 5:
        return "0_5"
    if days <= 10:
        return "6_10"
    if days <= 15:
        return "11_15"
    if days <= 30:
        return "16_30"
    if days <= 60:
        return "31_60"
    return "mais_60"


AGING_BUCKETS = {
    "0_5": "0 a 5 dias",
    "6_10": "6 a 10 dias",
    "11_15": "11 a 15 dias",
    "16_30": "16 a 30 dias",
    "31_60": "31 a 60 dias",
    "mais_60": "Acima de 60 dias",
}


def month_key(value):
    parsed = parse_api_date(value)
    if not parsed:
        return "Sem data"
    return parsed.strftime("%Y-%m")


def month_label(key):
    if key == "Sem data":
        return key
    try:
        return datetime.strptime(key, "%Y-%m").strftime("%m/%Y")
    except ValueError:
        return key


def normalize_motivo_label(value):
    text = " ".join(str(value or "").strip().split())
    if not text:
        return "Não informado"

    parts = text.split(maxsplit=1)
    if len(parts) == 2:
        raw_code = parts[0].strip(":-–—")
        if any(char.isdigit() for char in raw_code) and len(raw_code) <= 12:
            return parts[1].strip(":-–— ") or text
    return text


def enrich_dashboard_motivos_glosa(registros, tiss_rows):
    termos_por_codigo = {
        str(item.get("codigo_termo") or "").strip(): " ".join(
            str(item.get("termo") or "").strip().split()
        )
        for item in (tiss_rows or [])
        if item.get("codigo_termo") and item.get("termo")
    }
    enriched = []
    for registro in registros:
        item = dict(registro)
        motivo = " ".join(str(item.get("motivo_glosa") or "").strip().split())
        codigo = motivo.split(maxsplit=1)[0].strip(":-–—") if motivo else ""
        descricao = termos_por_codigo.get(codigo)
        if descricao:
            item["motivo_glosa"] = f"{codigo} - {descricao}"
        enriched.append(item)
    return enriched


def period_month_keys(period_start=None, period_end=None):
    end_date = parse_api_date(period_end) or date.today()
    start_date = parse_api_date(period_start) or subtract_months(
        end_date,
        DEFAULT_DASHBOARD_PERIOD_MONTHS - 1,
    )
    if start_date > end_date:
        start_date, end_date = end_date, start_date
    return month_keys_between(start_date, end_date)


def period_label_from_month_keys(month_keys):
    if not month_keys:
        return "Sem período"
    if len(month_keys) == 1:
        return month_label(month_keys[0])
    return f"{month_label(month_keys[0])} a {month_label(month_keys[-1])}"


def build_motivos_indicators(rows, series_limit=5, period_start=None, period_end=None):
    motivo_groups = {}
    for registro in rows:
        label = normalize_motivo_label(registro.get("motivo_glosa"))
        current = motivo_groups.setdefault(label, {"label": label, "count": 0, "value": 0})
        current["count"] += 1
        current["value"] += registro_valor_glosado(registro)

    sorted_pareto_items = sorted(
        motivo_groups.values(),
        key=lambda item: (item["value"], item["count"]),
        reverse=True,
    )
    total_value = sum(item["value"] for item in motivo_groups.values())
    pareto_items = []
    priority_value = 0
    for item in sorted_pareto_items:
        pareto_items.append(dict(item))
        priority_value += item["value"]
        if percent_value(priority_value, total_value) >= 80:
            break

    remaining_items = sorted_pareto_items[len(pareto_items):]
    if remaining_items:
        other_value = sum(item["value"] for item in remaining_items)
        other_details = [
            (
                f"{item['label']}: "
                f"{percent_value(item['value'], total_value)}%"
            )
            for item in remaining_items
        ]
        pareto_items.append(
            {
                "label": "Outros",
                "count": sum(item["count"] for item in remaining_items),
                "value": other_value,
                "is_other": True,
                "tooltip": "Outros motivos:\n" + "\n".join(other_details),
            }
        )

    max_value = max((item["value"] for item in pareto_items), default=0)
    accumulated = 0
    pareto = []
    pareto_cut_index = None
    for index, item in enumerate(pareto_items, start=1):
        accumulated += item["value"]
        accumulated_pct = percent_value(accumulated, total_value)
        if pareto_cut_index is None and accumulated_pct >= 80:
            pareto_cut_index = index
        pareto.append(
            {
                "label": item["label"],
                "count": item["count"],
                "value": item["value"],
                "value_formatado": format_brl_input(item["value"]),
                "bar_width": percent_value(item["value"], max_value),
                "bar_height": percent_int(item["value"], max_value),
                "share_pct": percent_value(item["value"], total_value),
                "accumulated_pct": accumulated_pct,
                "marker_left": percent_int(accumulated_pct, 100),
                "is_cut": pareto_cut_index == index,
                "is_other": item.get("is_other", False),
                "tooltip": item.get("tooltip", item["label"]),
            }
        )
    point_count = max(len(pareto) - 1, 1)
    pareto_line_points = " ".join(
        f"{round((index / point_count) * 100, 2)},{round(92 - (item['accumulated_pct'] * 0.84), 2)}"
        for index, item in enumerate(pareto)
    )
    pareto_cut_left = (
        round(((pareto_cut_index - 1) / point_count) * 100, 2)
        if pareto_cut_index
        else None
    )
    pareto_cut_left_css = (
        f"{pareto_cut_left:.2f}"
        if pareto_cut_left is not None
        else ""
    )
    line_points = [
        (
            round((index / point_count) * 100, 2),
            round(92 - (item["accumulated_pct"] * 0.84), 2),
        )
        for index, item in enumerate(pareto)
    ]
    pareto_accumulated_labels = []
    for index, item in enumerate(pareto):
        left = round(((index + 1) / max(len(pareto), 1)) * 100, 2)
        top = line_points[-1][1] if line_points else 0
        for point_index, (x1, y1) in enumerate(line_points[:-1]):
            x2, y2 = line_points[point_index + 1]
            if x1 <= left <= x2:
                ratio = (left - x1) / max(x2 - x1, 1)
                top = round(y1 + ((y2 - y1) * ratio), 2)
                break
        pareto_accumulated_labels.append(
            {
                "left": left,
                "top": top,
                "left_css": f"{left:.2f}",
                "top_css": f"{top:.2f}",
                "value": item["accumulated_pct"],
            }
        )

    top_series_labels = [
        item["label"]
        for item in sorted(
            motivo_groups.values(),
            key=lambda item: (item["count"], item["value"]),
            reverse=True,
        )[:series_limit]
    ]

    month_keys = period_month_keys(period_start, period_end)
    monthly = {
        label: {key: 0 for key in month_keys}
        for label in top_series_labels
    }
    for registro in rows:
        label = normalize_motivo_label(registro.get("motivo_glosa"))
        if label not in monthly:
            continue
        key = month_key(registro.get("data_glosa"))
        if key in monthly[label]:
            monthly[label][key] += 1

    max_count = max(
        (count for counts in monthly.values() for count in counts.values()),
        default=0,
    )
    colors = ["#1f6f86", "#d58a22", "#2f8a5f", "#8069a8", "#c56d86"]
    divisor = max(max_count, 1)
    y_ticks = [
        max_count,
        round(max_count * 0.75),
        round(max_count * 0.5),
        round(max_count * 0.25),
        0,
    ]
    series = []
    point_count = max(len(month_keys) - 1, 1)
    for index, label in enumerate(top_series_labels):
        points = []
        values = []
        for month_index, key in enumerate(month_keys):
            count = monthly[label][key]
            x = round(4 + ((month_index / point_count) * 92), 2)
            y = round(92 - ((count / divisor) * 76), 2)
            points.append(f"{x},{y}")
            values.append(
                {
                    "label": month_label(key),
                    "count": count,
                    "x": f"{x:.2f}",
                    "y": f"{y:.2f}",
                }
            )
        series.append(
            {
                "label": label,
                "color": colors[index % len(colors)],
                "points": " ".join(points),
                "values": values,
                "total": sum(item["count"] for item in values),
            }
        )

    return {
        "pareto": pareto,
        "pareto_line_points": pareto_line_points,
        "pareto_cut_left": pareto_cut_left,
        "pareto_cut_left_css": pareto_cut_left_css,
        "pareto_accumulated_labels": pareto_accumulated_labels,
        "pareto_total_formatado": format_brl_input(total_value),
        "pareto_cut_index": pareto_cut_index or 0,
        "pareto_count": len(pareto),
        "months": [month_label(key) for key in month_keys],
        "month_count": len(month_keys),
        "period_label": period_label_from_month_keys(month_keys),
        "series": series,
        "max_count": max_count,
        "y_ticks": y_ticks,
    }


def recovery_tooltip_lines(item, group_label):
    return "\n".join(
        [
            f"{group_label}: {item['label']}",
            f"Valor recursado: {item['valor_recursado_formatado']}",
            f"Valor recuperado: {item['valor_recuperado_formatado']}",
            (
                "Taxa Eficiência Op. "
                f"(vl. recuperado / vl. recursado): "
                f"{item['taxa_sucesso_recurso']}%"
            ),
            f"Quantidade recursada: {item['qtd_recursos']}",
            f"Quantidade recuperada: {item['qtd_recuperados']}",
        ]
    )


def build_recovery_group(rows, key_name, label_normalizer=None):
    groups = {}
    for registro in rows:
        raw_label = registro.get(key_name)
        label = (
            label_normalizer(raw_label)
            if label_normalizer
            else (raw_label or "Não informado")
        )
        current = groups.setdefault(
            label,
            {
                "label": label,
                "qtd_glosas": 0,
                "valor_glosado_total": 0,
                "valor_recursado": 0,
                "valor_recuperado": 0,
                "qtd_recursos": 0,
                "qtd_recuperados": 0,
                "qtd_acatos": 0,
            },
        )
        current["qtd_glosas"] += 1
        current["valor_glosado_total"] += registro_valor_glosado(registro)
        if is_recebido_registro(registro):
            current["valor_recuperado"] += as_float_or_zero(registro.get("valor_recebido"))
        if is_recurso_registro(registro):
            current["qtd_recursos"] += 1
            current["valor_recursado"] += registro_valor_glosado(registro)
            if is_recebido_registro(registro):
                current["qtd_recuperados"] += 1
        elif is_acato_registro(registro):
            current["qtd_acatos"] += 1

    for item in groups.values():
        item["taxa_recuperacao"] = percent_value(
            item["valor_recuperado"],
            item["valor_glosado_total"],
        )
        item["taxa_sucesso_recurso"] = percent_value(
            item["valor_recuperado"],
            item["valor_recursado"],
        )
        item["valor_glosado_total_formatado"] = format_brl_input(
            item["valor_glosado_total"]
        )
        item["valor_recursado_formatado"] = format_brl_input(
            item["valor_recursado"]
        )
        item["valor_recuperado_formatado"] = format_brl_input(
            item["valor_recuperado"]
        )
    return list(groups.values())


def classify_recovery_quadrant(item, media_valor_glosado, media_taxa_recuperacao):
    high_value = item["valor_glosado_total"] > media_valor_glosado
    high_recovery = item["taxa_recuperacao"] > media_taxa_recuperacao
    if high_value and high_recovery:
        return {
            "key": "excelente",
            "label": "Excelente",
            "description": "alto valor glosado e alta recuperação",
        }
    if high_value and not high_recovery:
        return {
            "key": "prioridade",
            "label": "Prioridade Máxima",
            "description": "alto valor glosado e baixa recuperação",
        }
    if not high_value and high_recovery:
        return {
            "key": "baixa",
            "label": "Baixa Prioridade",
            "description": "boa recuperação com menor impacto financeiro",
        }
    return {
        "key": "pouco",
        "label": "Pouco Relevante",
        "description": "menor valor glosado e baixa recuperação",
    }


def recovery_plot_position(value, total, start=8, end=92):
    return start + ((percent_value(value, total) / 100) * (end - start))


def recovery_log_position(value, max_value, start=10, end=90):
    if value <= 0 or max_value <= 0:
        return start
    return start + ((log10(value + 1) / log10(max_value + 1)) * (end - start))


def format_css_number(value):
    return f"{value:.2f}"


def build_recuperacao_indicators(rows, period_start=None, period_end=None):
    recovery_rows = [
        registro
        for registro in rows
        if is_active_registro(registro)
        and is_recurso_registro(registro)
        and has_internal_treatment(registro)
        and registro_valor_glosado(registro) > 0
    ]

    motivo_groups = build_recovery_group(
        recovery_rows,
        "motivo_glosa",
        normalize_motivo_label,
    )
    media_valor_glosado = (
        sum(item["valor_glosado_total"] for item in motivo_groups) / len(motivo_groups)
        if motivo_groups
        else 0
    )
    media_taxa_recuperacao = (
        sum(item["taxa_recuperacao"] for item in motivo_groups) / len(motivo_groups)
        if motivo_groups
        else 0
    )
    max_valor_glosado = max(
        (item["valor_glosado_total"] for item in motivo_groups),
        default=0,
    )
    max_valor_recuperado = max(
        (item["valor_recuperado"] for item in motivo_groups),
        default=0,
    )
    max_taxa_recuperacao = 100

    scatter = sorted(
        motivo_groups,
        key=lambda item: (item["valor_glosado_total"], item["valor_recuperado"]),
        reverse=True,
    )
    jitter_steps = (-3, -1.5, 0, 1.5, 3)
    for index, item in enumerate(scatter):
        quadrant = classify_recovery_quadrant(
            item,
            media_valor_glosado,
            media_taxa_recuperacao,
        )
        item["quadrant_key"] = quadrant["key"]
        item["quadrant_label"] = quadrant["label"]
        item["quadrant_description"] = quadrant["description"]
        x_pct = recovery_log_position(
            item["valor_glosado_total"],
            max_valor_glosado,
            12,
            88,
        )
        y_pct = 100 - recovery_plot_position(
            min(item["taxa_recuperacao"], 100),
            max_taxa_recuperacao,
            16,
            84,
        )
        item["x_pct"] = min(max(x_pct + jitter_steps[index % len(jitter_steps)], 10), 90)
        item["y_pct"] = min(
            max(y_pct + jitter_steps[(index // len(jitter_steps)) % len(jitter_steps)], 14),
            86,
        )
        item["x_pct_css"] = format_css_number(item["x_pct"])
        item["y_pct_css"] = format_css_number(item["y_pct"])
        item["bubble_size"] = 18 + round(
            percent_value(item["valor_recuperado"], max_valor_recuperado) * 0.22
        )
        item["valor_glosado_compacto"] = format_brl_compact(
            item["valor_glosado_total"]
        )
        item["short_label"] = item["label"]
        item["label_side"] = "left" if item["x_pct"] >= 74 else "right"
        item["label_flow"] = "down" if item["y_pct"] <= 24 else "up"
        item["tooltip"] = recovery_tooltip_lines(item, "Motivo da glosa")

    convenio_groups = build_recovery_group(recovery_rows, "convenio")
    convenio_valor = sorted(
        convenio_groups,
        key=lambda item: (item["valor_recuperado"], item["valor_glosado_total"]),
        reverse=True,
    )
    convenio_recursado = sorted(
        convenio_groups,
        key=lambda item: (item["valor_recursado"], item["qtd_recursos"]),
        reverse=True,
    )
    convenio_sucesso = sorted(
        convenio_groups,
        key=lambda item: (
            item["taxa_sucesso_recurso"],
            item["qtd_recuperados"],
        ),
        reverse=True,
    )
    max_convenio_valor = max(
        (item["valor_recuperado"] for item in convenio_valor),
        default=0,
    )
    max_convenio_recursado = max(
        (item["valor_recursado"] for item in convenio_recursado),
        default=0,
    )
    for item in convenio_valor:
        item["value_bar_width"] = percent_int(
            item["valor_recuperado"],
            max_convenio_valor,
        )
        item["tooltip"] = recovery_tooltip_lines(item, "Convênio")
    for item in convenio_recursado:
        item["resource_bar_width"] = percent_int(
            item["valor_recursado"],
            max_convenio_recursado,
        )
        item["tooltip"] = recovery_tooltip_lines(item, "Convênio")
    for item in convenio_sucesso:
        item["success_bar_width"] = percent_int(
            item["taxa_sucesso_recurso"],
            100,
        )
        item["tooltip"] = recovery_tooltip_lines(item, "Convênio")

    recovery_month_keys = period_month_keys(period_start, period_end)
    recovery_monthly = {
        key: {
            "label": month_label(key),
            "valor_recursado": 0,
            "valor_recuperado": 0,
            "qtd_recursada": 0,
            "qtd_recuperada": 0,
        }
        for key in recovery_month_keys
    }
    for registro in recovery_rows:
        key = month_key(registro.get("data_glosa"))
        if key not in recovery_monthly:
            continue
        current = recovery_monthly[key]
        current["valor_recursado"] += registro_valor_glosado(registro)
        current["qtd_recursada"] += 1
        if is_recebido_registro(registro):
            current["valor_recuperado"] += as_float_or_zero(
                registro.get("valor_recebido")
            )
            current["qtd_recuperada"] += 1

    max_monthly_value = max(
        (
            max(item["valor_recursado"], item["valor_recuperado"])
            for item in recovery_monthly.values()
        ),
        default=0,
    )
    monthly_divisor = max(max_monthly_value, 1)
    monthly_point_count = max(len(recovery_month_keys) - 1, 1)
    recursado_points = []
    recuperado_points = []
    sucesso_points = []
    recovery_monthly_points = []
    for index, key in enumerate(recovery_month_keys):
        item = recovery_monthly[key]
        taxa_sucesso = percent_value(
            item["valor_recuperado"],
            item["valor_recursado"],
        )
        x = round(4 + ((index / monthly_point_count) * 92), 2)
        recursado_y = round(
            92
            - recovery_log_position(
                item["valor_recursado"],
                monthly_divisor,
                0,
                76,
            ),
            2,
        )
        recuperado_y = round(
            92
            - recovery_log_position(
                item["valor_recuperado"],
                monthly_divisor,
                0,
                76,
            ),
            2,
        )
        sucesso_y = round(92 - ((min(taxa_sucesso, 100) / 100) * 76), 2)
        recursado_points.append(f"{x},{recursado_y}")
        recuperado_points.append(f"{x},{recuperado_y}")
        sucesso_points.append(f"{x},{sucesso_y}")
        recovery_monthly_points.append(
            {
                **item,
                "taxa_sucesso": taxa_sucesso,
                "valor_recursado_formatado": format_brl_input(
                    item["valor_recursado"]
                ),
                "valor_recuperado_formatado": format_brl_input(
                    item["valor_recuperado"]
                ),
                "x": format_css_number(x),
                "recursado_y": format_css_number(recursado_y),
                "recuperado_y": format_css_number(recuperado_y),
                "sucesso_y": format_css_number(sucesso_y),
            }
        )

    total_monthly_recursado = sum(
        item["valor_recursado"] for item in recovery_monthly.values()
    )
    total_monthly_recuperado = sum(
        item["valor_recuperado"] for item in recovery_monthly.values()
    )
    recovery_extrema = []
    extrema_series = (
        (
            "recursado",
            "valor_recursado",
            "recursado_y",
            lambda value: format_brl_compact(value),
        ),
        (
            "recuperado",
            "valor_recuperado",
            "recuperado_y",
            lambda value: format_brl_compact(value),
        ),
        (
            "sucesso",
            "taxa_sucesso",
            "sucesso_y",
            lambda value: f"{value}%",
        ),
    )
    for series_key, value_key, y_key, label_formatter in extrema_series:
        if not recovery_monthly_points:
            continue
        valid_indexes = [
            index
            for index, point in enumerate(recovery_monthly_points)
            if point["qtd_recursada"] > 0
        ]
        if not valid_indexes:
            continue
        indexes = {
            min(
                valid_indexes,
                key=lambda index: recovery_monthly_points[index][value_key],
            ),
            max(
                valid_indexes,
                key=lambda index: recovery_monthly_points[index][value_key],
            ),
        }
        for index in sorted(indexes):
            point = recovery_monthly_points[index]
            recovery_extrema.append(
                {
                    "series": series_key,
                    "x": point["x"],
                    "y": point[y_key],
                    "label": label_formatter(point[value_key]),
                    "label_flow": "down"
                    if float(point[y_key]) <= 24
                    else "up",
                    "month": point["label"],
                    "valor_recursado_formatado": point[
                        "valor_recursado_formatado"
                    ],
                    "valor_recuperado_formatado": point[
                        "valor_recuperado_formatado"
                    ],
                    "taxa_sucesso": point["taxa_sucesso"],
                    "qtd_recursada": point["qtd_recursada"],
                    "qtd_recuperada": point["qtd_recuperada"],
                }
            )
    recovery_monthly_indicators = {
        "months": [month_label(key) for key in recovery_month_keys],
        "month_count": len(recovery_month_keys),
        "period_label": period_label_from_month_keys(recovery_month_keys),
        "points": recovery_monthly_points,
        "recursado_points": " ".join(recursado_points),
        "recuperado_points": " ".join(recuperado_points),
        "sucesso_points": " ".join(sucesso_points),
        "extrema": recovery_extrema,
        "value_ticks": [
            {
                "label": format_brl_compact(
                    ((max_monthly_value + 1) ** fraction) - 1
                ),
                "y": format_css_number(92 - (fraction * 76)),
            }
            for fraction in (1, 0.75, 0.5, 0.25, 0)
        ],
        "rate_ticks": [
            {
                "label": f"{rate}%",
                "y": format_css_number(92 - ((rate / 100) * 76)),
            }
            for rate in (100, 75, 50, 25, 0)
        ],
        "total_recursado_formatado": format_brl_input(total_monthly_recursado),
        "total_recuperado_formatado": format_brl_input(total_monthly_recuperado),
        "taxa_sucesso": percent_value(
            total_monthly_recuperado,
            total_monthly_recursado,
        ),
    }

    media_valor_glosado_pct = recovery_log_position(
        media_valor_glosado,
        max_valor_glosado,
        12,
        88,
    )
    media_taxa_recuperacao_y_pct = 100 - recovery_plot_position(
        min(media_taxa_recuperacao, 100),
        max_taxa_recuperacao,
        16,
        84,
    )

    return {
        "tem_dados": bool(recovery_rows),
        "scatter": scatter,
        "convenio_valor": convenio_valor,
        "convenio_recursado": convenio_recursado,
        "convenio_sucesso": convenio_sucesso,
        "mensal": recovery_monthly_indicators,
        "media_valor_glosado": media_valor_glosado,
        "media_valor_glosado_formatado": format_brl_input(media_valor_glosado),
        "media_taxa_recuperacao": round(media_taxa_recuperacao, 1),
        "media_valor_glosado_pct": format_css_number(media_valor_glosado_pct),
        "media_taxa_recuperacao_y_pct": format_css_number(
            media_taxa_recuperacao_y_pct
        ),
        "x_ticks": [
            {
                "label": format_brl_compact(
                    ((max_valor_glosado + 1) ** fraction) - 1
                ),
                "left": format_css_number(12 + (fraction * 76)),
            }
            for fraction in (0, 0.2, 0.4, 0.6, 0.8, 1)
        ],
        "y_ticks": [
            {"label": "100%", "top": 16},
            {"label": "75%", "top": 33},
            {"label": "50%", "top": 50},
            {"label": "25%", "top": 67},
            {"label": "0%", "top": 84},
        ],
        "total_motivos": len(scatter),
        "scatter_default_limit": min(len(scatter), 12),
        "scatter_max_limit": len(scatter),
        "total_convenios": len(convenio_groups),
    }


def geral_empty_month(key):
    return {
        "label": month_label(key),
        "fatura": 0,
        "glosa": 0,
        "recursado": 0,
        "acato": 0,
        "recuperado": 0,
        "qtd": 0,
        "qtd_acatos": 0,
        "convenios": {},
        "motivos": {},
    }


def geral_metric_points(month_items, value_key):
    point_count = max(len(month_items) - 1, 1)
    points = []
    values = []
    for index, item in enumerate(month_items):
        value = item[value_key]
        x = round(4 + ((index / point_count) * 92), 2)
        y = round(92 - ((min(value, 100) / 100) * 76), 2)
        points.append(f"{x},{y}")
        values.append(
            {
                "x": format_css_number(x),
                "y": format_css_number(y),
                "value": value,
                "label": item["label"],
                "motivos_tooltip": item.get("motivos_tooltip", ""),
            }
        )
    return " ".join(points), values


def funnel_rendered_height(stage_height):
    return max(74, min(stage_height + 34, 152))


def funnel_connector_path(from_height, to_height, width=112):
    connector_height = max(from_height, to_height)
    center = connector_height / 2
    from_top = center - (from_height / 2)
    from_bottom = center + (from_height / 2)
    to_top = center - (to_height / 2)
    to_bottom = center + (to_height / 2)
    c1 = round(width * 0.25, 1)
    c2 = round(width * 0.72, 1)
    values = {
        "width": width,
        "from_top": round(from_top, 1),
        "from_bottom": round(from_bottom, 1),
        "to_top": round(to_top, 1),
        "to_bottom": round(to_bottom, 1),
        "c1": c1,
        "c2": c2,
    }
    return {
        "height": round(connector_height, 1),
        "path": (
            "M 0 {from_top} "
            "C {c1} {from_top}, {c1} {to_top}, {c2} {to_top} "
            "C {width} {to_top}, {width} {to_top}, {width} {to_top} "
            "L {width} {to_bottom} "
            "C {width} {to_bottom}, {width} {to_bottom}, {c2} {to_bottom} "
            "C {c1} {to_bottom}, {c1} {from_bottom}, 0 {from_bottom} "
            "Z"
        ).format(**values),
    }


def build_geral_indicators(rows, period_start=None, period_end=None):
    month_keys = period_month_keys(period_start, period_end)
    monthly = {key: geral_empty_month(key) for key in month_keys}
    convenio_groups = {}

    for registro in rows:
        convenio = registro.get("convenio") or "Não informado"
        fatura = as_float_or_zero(registro.get("valor"))
        glosa = registro_valor_glosado(registro)
        recursado = (
            glosa
            if has_internal_treatment(registro)
            and is_recurso_registro(registro)
            else 0
        )
        acato = (
            glosa
            if has_internal_treatment(registro)
            and is_acato_registro(registro)
            else 0
        )
        recuperado = (
            as_float_or_zero(registro.get("valor_recebido"))
            if is_recebido_registro(registro)
            else 0
        )
        motivo = normalize_motivo_label(registro.get("motivo_glosa"))

        current = convenio_groups.setdefault(
            convenio,
            {
                "label": convenio,
                "fatura": 0,
                "glosa": 0,
                "recursado": 0,
                "sucesso": 0,
                "acato": 0,
                "recuperado": 0,
                "qtd": 0,
            },
        )
        current["fatura"] += fatura
        current["glosa"] += glosa
        current["recursado"] += recursado
        current["sucesso"] += recuperado if recursado and recuperado > 0 else 0
        current["acato"] += acato
        current["recuperado"] += recuperado
        current["qtd"] += 1

        key = month_key(registro.get("data_glosa"))
        if key not in monthly:
            continue
        month_item = monthly[key]
        month_item["fatura"] += fatura
        month_item["glosa"] += glosa
        month_item["recursado"] += recursado
        month_item["acato"] += acato
        month_item["recuperado"] += recuperado
        month_item["qtd"] += 1
        if acato:
            month_item["qtd_acatos"] += 1

        convenio_month = month_item["convenios"].setdefault(
            convenio,
            {
                "label": convenio,
                "fatura": 0,
                "glosa": 0,
                "recursado": 0,
                "acato": 0,
            },
        )
        convenio_month["fatura"] += fatura
        convenio_month["glosa"] += glosa
        convenio_month["recursado"] += recursado
        convenio_month["acato"] += acato

        if acato:
            motivo_month = month_item["motivos"].setdefault(
                motivo,
                {"label": motivo, "count": 0, "value": 0},
            )
            motivo_month["count"] += 1
            motivo_month["value"] += acato

    totals = {
        "fatura": sum(item["fatura"] for item in convenio_groups.values()),
        "glosa": sum(item["glosa"] for item in convenio_groups.values()),
        "recursado": sum(item["recursado"] for item in convenio_groups.values()),
        "sucesso": sum(item["sucesso"] for item in convenio_groups.values()),
        "acato": sum(item["acato"] for item in convenio_groups.values()),
        "recuperado": sum(item["recuperado"] for item in convenio_groups.values()),
    }
    colors = [
        "#1f6f86",
        "#2f8a5f",
        "#d58a22",
        "#8069a8",
        "#c56d86",
        "#4f7fc4",
        "#8a6f2f",
    ]
    convenio_items = sorted(
        convenio_groups.values(),
        key=lambda item: (item["glosa"], item["fatura"]),
        reverse=True,
    )
    for index, item in enumerate(convenio_items, start=1):
        item["rank"] = index
        item["color"] = colors[(index - 1) % len(colors)]
        item["fatura_formatado"] = format_brl_input(item["fatura"])
        item["glosa_formatado"] = format_brl_input(item["glosa"])
        item["recursado_formatado"] = format_brl_input(item["recursado"])
        item["sucesso_formatado"] = format_brl_input(item["sucesso"])
        item["acato_formatado"] = format_brl_input(item["acato"])

    funnel_stages = [
        ("fatura", "1. Fatura Total", "", totals["fatura"]),
        ("glosa", "2. Valor Glosado", "Contestados pelos convênios", totals["glosa"]),
        ("recursado", "3. Recursos", "Valores recorridos", totals["recursado"]),
        ("sucesso", "4. Sucesso do Recurso", "Valores recuperados", totals["sucesso"]),
        ("acato", "5. Acatos (Perdas)", "Valores não recuperados", totals["acato"]),
    ]
    max_funnel = max((stage[3] for stage in funnel_stages), default=0)
    funnel = []
    stage_flow_colors = ["#2a8198", "#4f9bad", "#72aebb", "#66a984", "#778392"]
    funnel_value_lookup = {key: value for key, _label, _subtitle, value in funnel_stages}
    previous_value = None
    for index, (key, label, subtitle, value) in enumerate(funnel_stages):
        reference_key = "recursado" if key in {"sucesso", "acato"} else None
        reference_value = (
            funnel_value_lookup.get(reference_key, 0)
            if reference_key
            else previous_value
        )
        conversion = 100 if reference_value is None else percent_value(value, reference_value)
        drop = 0 if reference_value is None else max(reference_value - value, 0)
        drop_pct = 0 if reference_value is None else percent_value(drop, reference_value)
        height = 30 + round(percent_value(value, max_funnel) * 0.86)
        segments = []
        for convenio in convenio_items:
            segment_value = convenio[key]
            if segment_value <= 0:
                continue
            segments.append(
                {
                    "label": convenio["label"],
                    "rank": convenio["rank"],
                    "value": segment_value,
                    "value_formatado": format_brl_input(segment_value),
                    "share": percent_value(segment_value, value),
                    "width": percent_value(segment_value, value),
                    "color": convenio["color"],
                }
            )
        funnel.append(
            {
                "key": key,
                "label": label,
                "subtitle": subtitle,
                "reference_key": reference_key or "",
                "reference_label": "Recursos" if reference_key == "recursado" else "etapa anterior",
                "value": value,
                "value_formatado": format_brl_input(value),
                "share": percent_value(value, totals["fatura"]),
                "conversion": conversion,
                "drop": drop,
                "drop_formatado": format_brl_input(drop),
                "drop_pct": drop_pct,
                "width": max(percent_value(value, max_funnel), 3 if value else 0),
                "height": height,
                "rendered_height": funnel_rendered_height(height),
                "flow_color": stage_flow_colors[index],
                "segments": segments,
                "tooltip": "\n".join(
                    [
                        f"{label}: {format_brl_input(value)}",
                        f"Participação sobre fatura total: {percent_value(value, totals['fatura'])}%",
                    ]
                ),
            }
        )
        previous_value = value

    for index, item in enumerate(funnel):
        next_item = funnel[index + 1] if index + 1 < len(funnel) else None
        item["next_height"] = next_item["height"] if next_item else item["height"]
        item["connector_drop_pct"] = next_item["drop_pct"] if next_item else 0
        if next_item:
            connector = funnel_connector_path(
                item["rendered_height"],
                next_item["rendered_height"],
            )
            item["connector_height"] = connector["height"]
            item["connector_path"] = connector["path"]

    convenio_table = []
    for convenio in convenio_items:
        stages = []
        for key, label, _subtitle, value in funnel_stages:
            stage_value = convenio[key]
            stages.append(
                {
                    "key": key,
                    "label": label,
                    "value": stage_value,
                    "value_raw": format_css_number(stage_value),
                    "value_formatado": format_brl_input(stage_value),
                    "share": percent_value(stage_value, value),
                }
            )
        convenio_table.append(
            {
                "label": convenio["label"],
                "rank": convenio["rank"],
                "color": convenio["color"],
                "stages": stages,
            }
        )
    total_table = [
        {
            "key": key,
            "label": label,
            "value": value,
            "value_formatado": format_brl_input(value),
            "share": 100 if value else 0,
        }
        for key, label, _subtitle, value in funnel_stages
    ]

    month_items = []
    max_month_fatura = max((item["fatura"] for item in monthly.values()), default=0)
    for key in month_keys:
        item = monthly[key]
        convenio_segments = []
        tooltip_lines = [f"Competência: {item['label']}"]
        for convenio in convenio_items:
            convenio_item = item["convenios"].get(convenio["label"])
            if not convenio_item or convenio_item["fatura"] <= 0:
                continue
            convenio_segments.append(
                {
                    "label": convenio["label"],
                    "width": percent_value(convenio_item["fatura"], item["fatura"]),
                    "color": convenio["color"],
                }
            )
            tooltip_lines.extend(
                [
                    f"{convenio['label']}",
                    f"Faturamento: {format_brl_input(convenio_item['fatura'])}",
                    f"Glosa: {format_brl_input(convenio_item['glosa'])}",
                    f"Recursado: {format_brl_input(convenio_item['recursado'])}",
                    f"Acato: {format_brl_input(convenio_item['acato'])}",
                ]
            )
        item["fatura_formatado"] = format_brl_input(item["fatura"])
        item["glosa_formatado"] = format_brl_input(item["glosa"])
        item["recursado_formatado"] = format_brl_input(item["recursado"])
        item["acato_formatado"] = format_brl_input(item["acato"])
        item["bar_height"] = max(percent_value(item["fatura"], max_month_fatura), 2 if item["fatura"] else 0)
        item["segments"] = convenio_segments
        item["taxa_glosa"] = percent_value(item["glosa"], item["fatura"])
        item["indice_recuperacao"] = percent_value(item["recuperado"], item["glosa"])
        item["taxa_sucesso"] = percent_value(item["recuperado"], item["recursado"])
        item["taxa_acato"] = percent_value(item["acato"], item["glosa"])
        item["tooltip"] = "\n".join(tooltip_lines)
        item["motivos_tooltip"] = "\n".join(
            [f"Competência: {item['label']}"]
            + [
                (
                    f"{motivo['label']}: {motivo['count']} "
                    f"acato{'s' if motivo['count'] != 1 else ''} - "
                    f"{format_brl_input(motivo['value'])}"
                )
                for motivo in sorted(
                    item["motivos"].values(),
                    key=lambda motivo: (motivo["count"], motivo["value"]),
                    reverse=True,
                )[:6]
            ]
            + [
                f"Taxa de acato: {item['taxa_acato']}%",
                f"Taxa de sucesso: {item['taxa_sucesso']}%",
            ]
        )
        month_items.append(item)

    taxa_glosa_points, taxa_glosa_values = geral_metric_points(month_items, "taxa_glosa")
    indice_recuperacao_points, indice_recuperacao_values = geral_metric_points(
        month_items,
        "indice_recuperacao",
    )
    taxa_sucesso_points, taxa_sucesso_values = geral_metric_points(
        month_items,
        "taxa_sucesso",
    )
    taxa_acato_points, taxa_acato_values = geral_metric_points(month_items, "taxa_acato")

    return {
        "period_label": period_label_from_month_keys(month_keys),
        "months": [month_label(key) for key in month_keys],
        "month_count": len(month_keys),
        "funnel": funnel,
        "convenios": convenio_items,
        "convenio_table": convenio_table,
        "funnel_total_table": total_table,
        "funnel_default_limit": min(len(convenio_items), 10),
        "funnel_max_limit": len(convenio_items),
        "mensal": month_items,
        "taxa_glosa_points": taxa_glosa_points,
        "indice_recuperacao_points": indice_recuperacao_points,
        "taxa_sucesso_points": taxa_sucesso_points,
        "taxa_acato_points": taxa_acato_points,
        "taxa_glosa_values": taxa_glosa_values,
        "indice_recuperacao_values": indice_recuperacao_values,
        "taxa_sucesso_values": taxa_sucesso_values,
        "taxa_acato_values": taxa_acato_values,
        "rate_ticks": [
            {
                "label": f"{rate}%",
                "y": format_css_number(92 - ((rate / 100) * 76)),
            }
            for rate in (100, 75, 50, 25, 0)
        ],
        "totals": {
            **totals,
            "fatura_formatado": format_brl_input(totals["fatura"]),
            "glosa_formatado": format_brl_input(totals["glosa"]),
            "recursado_formatado": format_brl_input(totals["recursado"]),
            "sucesso_formatado": format_brl_input(totals["sucesso"]),
            "acato_formatado": format_brl_input(totals["acato"]),
        },
    }


def normalize_lookup_text(value):
    return " ".join(str(value or "").strip().upper().split())


def build_prazos_convenio_lookup(convenios):
    lookup = {}
    for convenio in convenios or []:
        dias = as_positive_int(convenio.get("dias_para_recurso"), None)
        if dias is None:
            continue

        cd_convenio = convenio.get("cd_convenio")
        if cd_convenio not in (None, ""):
            lookup[f"cd:{cd_convenio}"] = dias

        nome = normalize_lookup_text(convenio.get("convenio"))
        if nome:
            lookup[f"nome:{nome}"] = dias
    return lookup


def disabled_convenio_ids(convenios):
    return {
        as_int_or_zero(item.get("cd_convenio"))
        for item in convenios or []
        if item.get("habilitado") is False
    }


def is_enabled_convenio_registro(registro, convenios_desabilitados):
    return as_int_or_zero(registro.get("cd_convenio")) not in convenios_desabilitados


def prazo_recurso_registro(registro, prazos_lookup, prazo_padrao):
    cd_convenio = registro.get("cd_convenio")
    if cd_convenio not in (None, ""):
        prazo = prazos_lookup.get(f"cd:{cd_convenio}")
        if prazo is not None:
            return prazo

    nome = normalize_lookup_text(registro.get("convenio"))
    if nome:
        prazo = prazos_lookup.get(f"nome:{nome}")
        if prazo is not None:
            return prazo

    return prazo_padrao


def registro_tem_prazo_parametrizado(registro, prazos_lookup):
    cd_convenio = registro.get("cd_convenio")
    if cd_convenio not in (None, "") and f"cd:{cd_convenio}" in prazos_lookup:
        return True

    nome = normalize_lookup_text(registro.get("convenio"))
    return bool(nome and f"nome:{nome}" in prazos_lookup)


def tipo_tratativa_registro(registro):
    if is_pending_conciliation_registro(registro):
        return "Pendente"
    if is_recurso_registro(registro):
        return "Recurso"
    if is_acato_registro(registro):
        return "Acato"
    return "Não classificado"


def prazo_convenio_registro(registro, prazos_lookup):
    cd_convenio = registro.get("cd_convenio")
    if cd_convenio not in (None, ""):
        prazo = prazos_lookup.get(f"cd:{cd_convenio}")
        if prazo is not None:
            return prazo

    nome = normalize_lookup_text(registro.get("convenio"))
    if nome:
        return prazos_lookup.get(f"nome:{nome}")
    return None


def month_keys_between(start_date, end_date):
    keys = []
    current = date(start_date.year, start_date.month, 1)
    end = date(end_date.year, end_date.month, 1)
    while current <= end:
        keys.append(current.strftime("%Y-%m"))
        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)
    return keys


def build_vw_indicadores_aging_glosas(rows, prazos_lookup):
    today = date.today()
    view_rows = []
    for registro in rows:
        data_glosa = parse_api_date(registro.get("data_glosa"))
        if data_glosa is None:
            continue

        dt_recurso = parse_api_date(registro.get("dt_recurso"))
        data_final = dt_recurso or today
        aging_dias = max((data_final - data_glosa).days, 0)
        dias_para_recurso = prazo_convenio_registro(registro, prazos_lookup)
        flag_dentro_prazo = (
            dias_para_recurso is not None and aging_dias <= dias_para_recurso
        )
        flag_fora_prazo = (
            dias_para_recurso is not None and aging_dias > dias_para_recurso
        )

        view_rows.append(
            {
                "id": registro.get("id"),
                "cd_remessa": registro.get("cd_remessa"),
                "cd_atendimento": registro.get("cd_atendimento"),
                "conta": registro.get("conta"),
                "cd_convenio": registro.get("cd_convenio"),
                "convenio": registro.get("convenio") or "Não informado",
                "data_glosa": data_glosa,
                "dt_pagamento": parse_api_date(registro.get("dt_pagamento")),
                "dt_recurso": dt_recurso,
                "processo_recurso": registro.get("processo_recurso"),
                "sn_glosado": registro.get("sn_glosado"),
                "tipo_tratativa": tipo_tratativa_registro(registro),
                "status_tratativa": (
                    "Tratado" if has_internal_treatment(registro) else "Em aberto"
                ),
                "valor": as_float_or_zero(registro.get("valor")),
                "valor_glosado": registro_valor_glosado(registro),
                "valor_recebido": as_float_or_zero(registro.get("valor_recebido")),
                "aging_dias": aging_dias,
                "bucket_aging": aging_bucket_key(aging_dias),
                "ano_mes_glosa": data_glosa.strftime("%Y-%m"),
                "ano_mes_tratativa": data_final.strftime("%Y-%m"),
                "dias_para_recurso": dias_para_recurso,
                "flag_dentro_prazo": flag_dentro_prazo,
                "flag_fora_prazo": flag_fora_prazo,
                "sem_prazo_configurado": dias_para_recurso is None,
            }
        )
    return view_rows


def build_aging_indicators(vw_rows, period_start=None, period_end=None):
    month_keys = period_month_keys(period_start, period_end)
    treated_rows = [
        row
        for row in vw_rows
        if row["dt_recurso"] is not None and row["processo_recurso"]
    ]

    heatmap_lookup = {}
    for key in month_keys:
        for bucket_key in AGING_BUCKETS:
            heatmap_lookup[(bucket_key, key)] = {
                "count": 0,
                "value": 0,
                "aging_total": 0,
                "dentro": 0,
                "fora": 0,
            }

    for row in treated_rows:
        key = row["ano_mes_glosa"]
        if key not in month_keys:
            continue
        cell = heatmap_lookup[(row["bucket_aging"], key)]
        cell["count"] += 1
        cell["value"] += row["valor_glosado"]
        cell["aging_total"] += row["aging_dias"]
        if row["flag_dentro_prazo"]:
            cell["dentro"] += 1
        if row["flag_fora_prazo"]:
            cell["fora"] += 1

    max_heatmap_count = max(
        (cell["count"] for cell in heatmap_lookup.values()),
        default=0,
    )
    heatmap_rows = []
    for bucket_key, bucket_label in AGING_BUCKETS.items():
        cells = []
        for key in month_keys:
            cell = heatmap_lookup[(bucket_key, key)]
            count = cell["count"]
            intensity = percent_value(count, max_heatmap_count)
            intensity_level = 0
            if count:
                intensity_level = max(1, min(5, ceil(intensity / 20)))
            cells.append(
                {
                    "label": month_label(key),
                    "count": count,
                    "intensity": intensity,
                    "intensity_level": intensity_level,
                    "value_formatado": format_brl_input(cell["value"]),
                    "aging_medio": round(cell["aging_total"] / count, 1)
                    if count
                    else 0,
                    "dentro": cell["dentro"],
                    "fora": cell["fora"],
                }
            )
        heatmap_rows.append({"label": bucket_label, "cells": cells})

    monthly_lookup = {
        key: {
            "label": month_label(key),
            "count": 0,
            "value": 0,
            "aging_total": 0,
            "dentro": 0,
            "fora": 0,
        }
        for key in month_keys
    }
    for row in treated_rows:
        key = row["ano_mes_glosa"]
        if key not in monthly_lookup:
            continue
        current = monthly_lookup[key]
        current["count"] += 1
        current["value"] += row["valor_glosado"]
        current["aging_total"] += row["aging_dias"]
        if row["flag_dentro_prazo"]:
            current["dentro"] += 1
        if row["flag_fora_prazo"]:
            current["fora"] += 1

    max_monthly_count = max(
        (item["count"] for item in monthly_lookup.values()),
        default=0,
    )
    volume_tratativas_12m = []
    for key in month_keys:
        item = monthly_lookup[key]
        item["bar_width"] = percent_int(item["count"], max_monthly_count)
        item["bar_height"] = 4 + round((item["bar_width"] / 100) * 108)
        item["value_formatado"] = format_brl_input(item["value"])
        item["aging_medio"] = (
            round(item["aging_total"] / item["count"], 1)
            if item["count"]
            else 0
        )
        item["dentro_pct"] = percent_value(item["dentro"], item["dentro"] + item["fora"])
        item["fora_pct"] = percent_value(item["fora"], item["dentro"] + item["fora"])
        volume_tratativas_12m.append(item)

    convenio_groups = {}
    for row in treated_rows:
        name = row["convenio"] or "Não informado"
        current = convenio_groups.setdefault(
            name,
            {
                "label": name,
                "count": 0,
                "value": 0,
                "aging_total": 0,
                "dias_para_recurso": None,
            },
        )
        current["count"] += 1
        current["value"] += row["valor_glosado"]
        current["aging_total"] += row["aging_dias"]
        if current["dias_para_recurso"] is None and row["dias_para_recurso"] is not None:
            current["dias_para_recurso"] = row["dias_para_recurso"]

    convenio_barras = sorted(
        convenio_groups.values(),
        key=lambda item: (item["count"], item["value"]),
        reverse=True,
    )[:8]
    max_convenio_count = max((item["count"] for item in convenio_barras), default=0)
    max_convenio_value = max((item["value"] for item in convenio_barras), default=0)
    for item in convenio_barras:
        item["value_formatado"] = format_brl_input(item["value"])
        item["aging_medio"] = (
            round(item["aging_total"] / item["count"], 1)
            if item["count"]
            else 0
        )

    max_convenio_days = max(
        max(item["aging_medio"], item["dias_para_recurso"] or 0)
        for item in convenio_barras
    ) if convenio_barras else 0
    for item in convenio_barras:
        item["count_width"] = percent_value(item["count"], max_convenio_count)
        item["value_width"] = percent_value(item["value"], max_convenio_value)
        item["aging_width"] = percent_int(item["aging_medio"], max_convenio_days)
        item["prazo_marker_width"] = percent_int(
            item["dias_para_recurso"] or 0,
            max_convenio_days,
        )

    dentro = sum(1 for row in vw_rows if row["flag_dentro_prazo"])
    fora = sum(1 for row in vw_rows if row["flag_fora_prazo"])
    sem_prazo = sum(1 for row in vw_rows if row["sem_prazo_configurado"])
    em_aberto = sum(1 for row in vw_rows if row["status_tratativa"] == "Em aberto")

    return {
        "tem_dados": bool(treated_rows),
        "total": len(vw_rows),
        "tratados": len(treated_rows),
        "em_aberto": em_aberto,
        "dentro": dentro,
        "fora": fora,
        "sem_prazo": sem_prazo,
        "heatmap_months": [month_label(key) for key in month_keys],
        "month_count": len(month_keys),
        "period_label": period_label_from_month_keys(month_keys),
        "heatmap": heatmap_rows,
        "volume_tratativas_12m": volume_tratativas_12m,
        "convenio_barras": convenio_barras,
    }


def build_dashboard_indicadores(
    registros,
    prazo_sla=10,
    prazos_convenio=None,
    period_start=None,
    period_end=None,
):
    prazos_convenio = prazos_convenio or []
    prazos_lookup = build_prazos_convenio_lookup(prazos_convenio)
    convenios_desabilitados = disabled_convenio_ids(prazos_convenio)
    rows = [
        registro
        for registro in registros
        if (
            is_active_registro(registro)
            and (
                has_internal_treatment(registro)
                or is_pending_conciliation_registro(registro)
            )
            and is_enabled_convenio_registro(registro, convenios_desabilitados)
        )
    ]
    aging_view = build_vw_indicadores_aging_glosas(rows, prazos_lookup)
    aging_indicators = build_aging_indicators(aging_view, period_start, period_end)
    recursos = [
        registro
        for registro in rows
        if has_internal_treatment(registro)
        and is_recurso_registro(registro)
    ]
    acatos = [
        registro
        for registro in rows
        if has_internal_treatment(registro)
        and is_acato_registro(registro)
    ]
    total_glosado = sum(registro_valor_glosado(registro) for registro in rows)
    total_recursos_valor = sum(
        registro_valor_glosado(registro) for registro in recursos
    )
    total_acatos_valor = sum(registro_valor_glosado(registro) for registro in acatos)
    total_recebido = sum(
        as_float_or_zero(registro.get("valor_recebido"))
        for registro in rows
        if is_recebido_registro(registro)
    )
    recuperados = [registro for registro in rows if is_recebido_registro(registro)]

    recursos_com_sucesso = [
        registro
        for registro in recursos
        if is_recebido_registro(registro)
    ]
    glosas_sem_processo = [
        registro
        for registro in rows
        if is_pending_conciliation_registro(registro)
    ]
    total_glosas_sem_processo_valor = sum(
        registro_valor_glosado(registro) for registro in glosas_sem_processo
    )
    sem_recuperacao = [
        registro
        for registro in recursos
        if not is_recebido_registro(registro)
    ]
    total_sem_recuperacao_valor = sum(
        registro_valor_glosado(registro) for registro in sem_recuperacao
    )

    aging = []
    for key, label in AGING_BUCKETS.items():
        bucket_rows = [
            registro for registro in aging_view if registro["bucket_aging"] == key
        ]
        value = sum(registro["valor_glosado"] for registro in bucket_rows)
        aging.append(
            {
                "key": key,
                "label": label,
                "count": len(bucket_rows),
                "value": value,
                "value_formatado": format_brl_input(value),
            }
        )
    max_aging = max((item["count"] for item in aging), default=0)
    for item in aging:
        item["bar_width"] = percent_value(item["count"], max_aging)

    sla_dentro = 0
    sla_fora = 0
    sla_sem_parametro = 0
    for registro in aging_view:
        if registro["dias_para_recurso"] is None:
            sla_sem_parametro += 1
            continue
        if registro["flag_dentro_prazo"]:
            sla_dentro += 1
        elif registro["flag_fora_prazo"]:
            sla_fora += 1

    mensal = {}
    for registro in rows:
        key = month_key(registro.get("data_glosa"))
        current = mensal.setdefault(
            key,
            {
                "label": month_label(key),
                "count": 0,
                "value": 0,
                "recursos": 0,
                "acatos": 0,
            },
        )
        current["count"] += 1
        current["value"] += registro_valor_glosado(registro)
        if has_internal_treatment(registro) and is_recurso_registro(registro):
            current["recursos"] += 1
        elif has_internal_treatment(registro) and is_acato_registro(registro):
            current["acatos"] += 1

    volume_mensal = [
        mensal[key]
        for key in sorted(
            mensal,
            key=lambda item: "0000-00" if item == "Sem data" else item,
        )
    ][-8:]
    max_volume = max((item["value"] for item in volume_mensal), default=0)
    for item in volume_mensal:
        item["value_formatado"] = format_brl_input(item["value"])
        item["bar_width"] = percent_value(item["value"], max_volume)

    motivos = build_motivos_indicators(
        rows,
        period_start=period_start,
        period_end=period_end,
    )
    recuperacao = build_recuperacao_indicators(rows, period_start, period_end)
    geral = build_geral_indicators(rows, period_start, period_end)

    return {
        "kpis": {
            "total_registros": len(rows),
            "total_recursos": len(recursos),
            "total_acatos": len(acatos),
            "total_glosado": total_glosado,
            "total_glosado_formatado": format_brl_input(total_glosado),
            "total_recursos_valor": total_recursos_valor,
            "total_recursos_valor_formatado": format_brl_input(
                total_recursos_valor
            ),
            "total_acatos_valor": total_acatos_valor,
            "total_acatos_valor_formatado": format_brl_input(total_acatos_valor),
            "total_recebido": total_recebido,
            "total_recebido_formatado": format_brl_input(total_recebido),
            "total_recuperado": len(recuperados),
            "glosas_sem_processo": len(glosas_sem_processo),
            "total_glosas_sem_processo": len(glosas_sem_processo),
            "total_glosas_sem_processo_valor": total_glosas_sem_processo_valor,
            "total_glosas_sem_processo_valor_formatado": format_brl_input(
                total_glosas_sem_processo_valor
            ),
            "sem_recuperacao": len(sem_recuperacao),
            "total_sem_recuperacao": len(sem_recuperacao),
            "total_sem_recuperacao_valor": total_sem_recuperacao_valor,
            "total_sem_recuperacao_valor_formatado": format_brl_input(
                total_sem_recuperacao_valor
            ),
            "taxa_recurso": percent_value(len(recursos), len(rows)),
            "taxa_sucesso_qtd": percent_value(len(recursos_com_sucesso), len(recursos)),
            "taxa_sucesso_financeira": percent_value(
                total_recebido,
                total_recursos_valor,
            ),
        },
        "prazo_sla": prazo_sla,
        "prazos": {
            "configurados": len(
                [
                    convenio
                    for convenio in (prazos_convenio or [])
                    if convenio.get("dias_para_recurso") not in (None, "")
                ]
            ),
            "fallback": prazo_sla,
        },
        "sla": {
            "dentro": sla_dentro,
            "fora": sla_fora,
            "total": sla_dentro + sla_fora,
            "dentro_pct": percent_value(sla_dentro, sla_dentro + sla_fora),
            "fora_pct": percent_value(sla_fora, sla_dentro + sla_fora),
            "sem_parametro": sla_sem_parametro,
        },
        "aging": aging,
        "aging_glosas": aging_indicators,
        "volume_mensal": volume_mensal,
        "geral": geral,
        "motivos": motivos,
        "recuperacao": recuperacao,
    }


def clean_dashboard_filter_value(value):
    return str(value or "").strip()


def clean_dashboard_filter_values(values):
    cleaned = []
    seen = set()
    for value in values or []:
        item = clean_dashboard_filter_value(value)
        key = normalize_lookup_text(item)
        if not item or key in seen:
            continue
        cleaned.append(item)
        seen.add(key)
    return cleaned


def subtract_months(value, months):
    month_index = (value.year * 12 + value.month - 1) - months
    year = month_index // 12
    month = (month_index % 12) + 1
    day = min(value.day, monthrange(year, month)[1])
    return date(year, month, day)


def get_dashboard_filters(request):
    tratativa = clean_dashboard_filter_value(request.GET.get("tratativa")).lower()
    if tratativa == "glosa":
        tratativa = "acato"
    if tratativa not in {"recurso", "acato"}:
        tratativa = ""

    periodo_fim = format_api_date_input(request.GET.get("periodo_fim"))
    periodo_fim_date = parse_api_date(periodo_fim) or date.today()
    periodo_inicio = format_api_date_input(request.GET.get("periodo_inicio"))
    if not periodo_inicio:
        periodo_inicio = format_api_date_input(
            subtract_months(
                periodo_fim_date,
                DEFAULT_DASHBOARD_PERIOD_MONTHS - 1,
            )
        )
    if not periodo_fim:
        periodo_fim = format_api_date_input(periodo_fim_date)

    return {
        "periodo_inicio": periodo_inicio,
        "periodo_fim": periodo_fim,
        "tratativa": tratativa,
        "convenio": clean_dashboard_filter_values(request.GET.getlist("convenio")),
        "prestador": clean_dashboard_filter_values(request.GET.getlist("prestador")),
        "tipo_atendimento": clean_dashboard_filter_values(
            request.GET.getlist("tipo_atendimento")
        ),
        "motivo_glosa": clean_dashboard_filter_values(request.GET.getlist("motivo_glosa")),
    }


def unique_filter_options(rows, key_name):
    values = {
        clean_dashboard_filter_value(row.get(key_name))
        for row in rows
        if clean_dashboard_filter_value(row.get(key_name))
    }
    return sorted(values, key=lambda item: normalize_lookup_text(item))


def build_dashboard_filter_options(registros):
    rows = [registro for registro in registros if is_active_registro(registro)]
    return {
        "prestadores": unique_filter_options(rows, "prestador"),
        "tipos_atendimento": unique_filter_options(rows, "tp_atendimento"),
        "motivos_glosa": unique_filter_options(rows, "motivo_glosa"),
    }


def apply_dashboard_filters(registros, filters):
    periodo_inicio = parse_api_date(filters.get("periodo_inicio"))
    periodo_fim = parse_api_date(filters.get("periodo_fim"))
    convenios = {
        normalize_lookup_text(value) for value in filters.get("convenio", []) if value
    }
    prestadores = {
        normalize_lookup_text(value) for value in filters.get("prestador", []) if value
    }
    tipos_atendimento = {
        normalize_lookup_text(value)
        for value in filters.get("tipo_atendimento", [])
        if value
    }
    motivos_glosa = {
        normalize_lookup_text(value) for value in filters.get("motivo_glosa", []) if value
    }
    tratativa = filters.get("tratativa")

    filtered = []
    for registro in registros:
        data_glosa = parse_api_date(registro.get("data_glosa"))
        if periodo_inicio and (not data_glosa or data_glosa < periodo_inicio):
            continue
        if periodo_fim and (not data_glosa or data_glosa > periodo_fim):
            continue
        if convenios and normalize_lookup_text(registro.get("convenio")) not in convenios:
            continue
        if prestadores and normalize_lookup_text(registro.get("prestador")) not in prestadores:
            continue
        if (
            tipos_atendimento
            and normalize_lookup_text(registro.get("tp_atendimento")) not in tipos_atendimento
        ):
            continue
        if (
            motivos_glosa
            and normalize_lookup_text(registro.get("motivo_glosa")) not in motivos_glosa
        ):
            continue
        if tratativa == "recurso" and not (
            has_internal_treatment(registro)
            and is_recurso_registro(registro)
        ):
            continue
        if tratativa == "acato" and not (
            has_internal_treatment(registro)
            and is_acato_registro(registro)
        ):
            continue
        filtered.append(registro)

    return filtered


def normalized_contains(value, needle):
    query = normalize_lookup_text(needle)
    if not query:
        return True
    return query in normalize_lookup_text(value)


def same_numeric_text(value, expected):
    expected_text = clean_dashboard_filter_value(expected)
    if not expected_text:
        return True
    return str(value or "").strip() == expected_text


def apply_acompanhamento_filters(registros, filters):
    filtered = []
    for registro in registros:
        if not same_numeric_text(registro.get("cd_remessa"), filters.get("cd_remessa")):
            continue
        if not same_numeric_text(
            registro.get("cd_atendimento"),
            filters.get("cd_atendimento"),
        ):
            continue
        if not same_numeric_text(registro.get("conta"), filters.get("cd_reg")):
            continue
        convenio_filter = normalize_lookup_text(filters.get("nm_convenio"))
        if (
            convenio_filter
            and normalize_lookup_text(registro.get("convenio")) != convenio_filter
        ):
            continue
        if not normalized_contains(
            registro.get("processo_controle_fatura_gab"),
            filters.get("processo_original"),
        ):
            continue
        if not normalized_contains(
            registro.get("processo_recurso"),
            filters.get("processo_recurso"),
        ):
            continue
        if not normalized_contains(registro.get("nm_paciente"), filters.get("nm_paciente")):
            continue
        if (
            filters.get("tp_atendimento")
            and normalize_lookup_text(registro.get("tp_atendimento"))
            != normalize_lookup_text(filters.get("tp_atendimento"))
        ):
            continue
        filtered.append(registro)
    return filtered


def get_cached_dashboard_payload(cache_key, path, params=None, force_refresh=False):
    if force_refresh:
        cache.delete(cache_key)

    payload = cache.get(cache_key)
    if payload is None:
        payload = api_get(path, params)
        cache.set(
            cache_key,
            payload,
            getattr(settings, "DASHBOARD_CACHE_SECONDS", 45),
        )
    return payload


def build_api_cache_key(namespace, path, params=None):
    query = urlencode(
        sorted((key, value) for key, value in (params or {}).items() if value),
        doseq=True,
    )
    digest = sha256(f"{path}?{query}".encode("utf-8")).hexdigest()
    return f"api:{namespace}:{digest}"


def get_cached_api_payload(namespace, path, params=None, force_refresh=False):
    cache_key = build_api_cache_key(namespace, path, params)
    if force_refresh:
        cache.delete(cache_key)

    payload = cache.get(cache_key)
    if payload is None:
        payload = api_get(path, params)
        cache.set(
            cache_key,
            payload,
            getattr(settings, "APP_FILTER_CACHE_SECONDS", 45),
        )
    return payload


def get_convenio_filter_options(force_refresh=False):
    payload = get_cached_api_payload(
        DASHBOARD_CONVENIOS_CACHE_KEY,
        CONVENIOS_PATH,
        force_refresh=force_refresh,
    )
    rows = payload.get("convenios", []) if isinstance(payload, dict) else []
    return sorted(
        {
            str(item.get("nm_convenio") or "").strip()
            for item in rows
            if str(item.get("nm_convenio") or "").strip()
        },
        key=normalize_lookup_text,
    )


def get_convenio_dropdown_options(selected_value=""):
    try:
        options = get_convenio_filter_options()
    except ApiError:
        options = []

    selected_value = str(selected_value or "").strip()
    options_by_normalized_name = {
        normalize_lookup_text(option): option
        for option in options
        if normalize_lookup_text(option)
    }
    if selected_value:
        options_by_normalized_name[
            normalize_lookup_text(selected_value)
        ] = selected_value
    return sorted(
        options_by_normalized_name.values(),
        key=normalize_lookup_text,
    )


def clear_dashboard_cache():
    cache.delete_many(
        [
            DASHBOARD_GLOSAS_CACHE_KEY,
            DASHBOARD_PRAZOS_CACHE_KEY,
            DASHBOARD_CONVENIOS_CACHE_KEY,
            DASHBOARD_TISS_CACHE_KEY,
        ]
    )


def clear_filter_caches():
    cache.clear()


def _conciliacao_remessas_cache_context(request):
    try:
        page = max(int(request.GET.get("page") or 1), 1)
    except ValueError:
        page = 1
    page_size = 25
    filtros = {
        "numero_nfse": (request.GET.get("numero_nfse") or "").strip(),
        "cd_remessa": (request.GET.get("cd_remessa") or "").strip(),
        "convenio": (request.GET.get("convenio") or "").strip(),
    }
    params = {
        **{key: value or None for key, value in filtros.items()},
        "limit": page_size,
        "offset": (page - 1) * page_size,
    }
    cache_key = build_api_cache_key(
        "financeiro:remessas-conciliacao",
        f"{CONCILIACAO_FATURAMENTO_PATH}/remessas",
        params,
    )
    return (
        page,
        page_size,
        filtros,
        params,
        cache_key,
        f"{cache_key}:snapshot",
    )


def _restore_conciliacao_remessas_cache(
    cache_key,
    snapshot_key,
    cached_payload,
    api_response,
):
    if not isinstance(cached_payload, dict) or not isinstance(
        api_response,
        dict,
    ):
        return
    remessa_atualizada = api_response.get("remessa")
    remessas_anteriores = cached_payload.get("remessas")
    if not isinstance(remessa_atualizada, dict) or not isinstance(
        remessas_anteriores,
        list,
    ):
        return

    cd_remessa = remessa_atualizada.get("cd_remessa")
    indice = next(
        (
            index
            for index, remessa in enumerate(remessas_anteriores)
            if remessa.get("cd_remessa") == cd_remessa
        ),
        None,
    )
    if indice is None:
        return

    try:
        saldo_anterior = Decimal(
            str(remessas_anteriores[indice].get("valor_nao_conciliado") or 0)
        )
        saldo_atual = Decimal(
            str(remessa_atualizada.get("valor_nao_conciliado") or 0)
        )
        saldo_total = Decimal(
            str(cached_payload.get("valor_total_nao_conciliado") or 0)
        )
        conciliado_anterior = Decimal(
            str(remessas_anteriores[indice].get("valor_conciliado") or 0)
        )
        conciliado_atual = Decimal(
            str(remessa_atualizada.get("valor_conciliado") or 0)
        )
        conciliado_total = Decimal(
            str(cached_payload.get("valor_total_conciliado") or 0)
        )
    except (InvalidOperation, TypeError, ValueError):
        return

    payload_atualizado = deepcopy(cached_payload)
    remessas_atualizadas = payload_atualizado["remessas"]
    if saldo_atual <= 0:
        remessas_atualizadas.pop(indice)
        payload_atualizado["total"] = max(
            int(payload_atualizado.get("total") or 1) - 1,
            0,
        )
    else:
        remessas_atualizadas[indice] = remessa_atualizada
    payload_atualizado["valor_total_nao_conciliado"] = format(
        max(saldo_total - saldo_anterior + saldo_atual, Decimal("0.00")),
        ".2f",
    )
    payload_atualizado["valor_total_conciliado"] = format(
        max(
            conciliado_total - conciliado_anterior + conciliado_atual,
            Decimal("0.00"),
        ),
        ".2f",
    )
    cache.set(
        cache_key,
        payload_atualizado,
        getattr(settings, "APP_FILTER_CACHE_SECONDS", 45),
    )
    cache.set(snapshot_key, payload_atualizado, 900)


def dashboard(request):
    prazo_sla = as_positive_int(request.GET.get("sla"), 10)
    filtros = get_dashboard_filters(request)
    force_refresh = request.GET.get("refresh") == "1"
    opcoes_filtro = {
        "convenios": [],
        "prestadores": [],
        "tipos_atendimento": [],
        "motivos_glosa": [],
    }
    prazos_convenio = []
    convenio_options = []
    dashboard_errors = []
    try:
        prazos_payload = get_cached_dashboard_payload(
            DASHBOARD_PRAZOS_CACHE_KEY,
            PRAZOS_RECURSO_CONVENIO_PATH,
            force_refresh=force_refresh,
        )
        prazos_convenio = prazos_payload.get("convenios", [])
    except ApiError as exc:
        dashboard_errors.append(("Configuração por convênio", exc))

    try:
        convenio_options = get_convenio_filter_options(force_refresh)
    except ApiError as exc:
        dashboard_errors.append(("Convênios", exc))

    tiss_motivos = []
    tiss_rows = []
    try:
        tiss_payload = get_cached_dashboard_payload(
            DASHBOARD_TISS_CACHE_KEY,
            settings.API_TISS_PATH,
            {"limit": 600},
            force_refresh=force_refresh,
        )
        tiss_rows = tiss_payload.get("itens", []) if isinstance(tiss_payload, dict) else []
        tiss_motivos = [
            f"{item.get('codigo_termo')} - {item.get('termo')}"
            for item in tiss_rows
            if item.get("codigo_termo") and item.get("termo")
        ]
    except ApiError as exc:
        dashboard_errors.append(("Motivos TISS", exc))

    try:
        payload = get_cached_dashboard_payload(
            DASHBOARD_GLOSAS_CACHE_KEY,
            settings.API_REGISTRO_GLOSA_PATH,
            {"limit": DASHBOARD_GLOSAS_LIMIT},
            force_refresh=force_refresh,
        )
        registros = payload.get("glosas", []) if isinstance(payload, dict) else []
        registros = enrich_dashboard_motivos_glosa(registros, tiss_rows)
        opcoes_filtro = build_dashboard_filter_options(registros)
        opcoes_filtro["convenios"] = convenio_options
        if tiss_motivos:
            opcoes_filtro["motivos_glosa"] = tiss_motivos
        registros_filtrados = apply_dashboard_filters(registros, filtros)
        indicadores = build_dashboard_indicadores(
            registros_filtrados,
            prazo_sla,
            prazos_convenio,
            filtros.get("periodo_inicio"),
            filtros.get("periodo_fim"),
        )
    except ApiError as exc:
        indicadores = build_dashboard_indicadores(
            [],
            prazo_sla,
            prazos_convenio,
            filtros.get("periodo_inicio"),
            filtros.get("periodo_fim"),
        )
        dashboard_errors.append(("Indicadores", exc))

    auth_errors = [exc for _, exc in dashboard_errors if exc.status_code == 401]
    if auth_errors and len(auth_errors) == len(dashboard_errors):
        messages.error(
            request,
            "Sua sessão não é mais válida. Saia e entre novamente no sistema.",
        )
    else:
        for endpoint_name, exc in dashboard_errors:
            messages.error(request, format_api_error(exc, endpoint_name))
    return render(
        request,
        "dashboard.html",
        {
            "indicadores": indicadores,
            "filtros": filtros,
            "opcoes_filtro": opcoes_filtro,
        },
    )


@require_http_methods(["GET", "POST"])
def prazos_recurso_convenio(request):
    if request.method == "POST":
        payload = []
        errors = []
        for cd_convenio in request.POST.getlist("cd_convenio"):
            convenio = request.POST.get(f"convenio_{cd_convenio}", "").strip()
            dias_raw = request.POST.get(f"dias_para_recurso_{cd_convenio}", "").strip()
            if not dias_raw:
                continue

            dias = as_positive_int(dias_raw, None)
            if dias is None:
                errors.append(convenio or cd_convenio)
                continue

            payload.append(
                {
                    "cd_convenio": int(cd_convenio),
                    "convenio": convenio,
                    "dias_para_recurso": dias,
                    "habilitado": (
                        request.POST.get(f"habilitado_{cd_convenio}") == "true"
                    ),
                }
            )

        if errors:
            messages.error(
                request,
                "Informe uma quantidade de dias válida para: "
                + ", ".join(errors),
            )
        elif not payload:
            messages.warning(request, "Nenhum prazo foi informado para atualização.")
        else:
            try:
                api_put(PRAZOS_RECURSO_CONVENIO_PATH, payload)
                clear_filter_caches()
                messages.success(request, "Configurações por convênio atualizadas.")
                return redirect("prazos_recurso_convenio")
            except ApiError as exc:
                messages.error(
                    request,
                    format_api_error(exc, "Configuração por convênio"),
                )

    try:
        payload = api_get(PRAZOS_RECURSO_CONVENIO_PATH)
        convenios = payload.get("convenios", [])
        for convenio in convenios:
            if convenio.get("habilitado") is None:
                convenio["habilitado"] = True
    except ApiError as exc:
        convenios = []
        messages.error(request, format_api_error(exc, "Configuração por convênio"))

    resumo = {
        "convenios": len(convenios),
        "configurados": sum(
            1 for convenio in convenios if convenio.get("dias_para_recurso") not in (None, "")
        ),
    }
    return render(
        request,
        "prazos_recurso_convenio.html",
        {
            "convenios": convenios,
            "resumo": resumo,
        },
    )


@require_http_methods(["GET", "POST"])
def empresas_emissoras(request):
    form_data = {
        "empresa_id": "",
        "cnpj": "",
        "razao_social": "",
    }
    if request.method == "POST":
        action = (request.POST.get("form_action") or "criar").strip()
        empresa_id = as_int_or_zero(request.POST.get("empresa_id"))
        form_data = {
            "empresa_id": str(empresa_id or ""),
            "cnpj": (request.POST.get("cnpj") or "").strip(),
            "razao_social": (
                request.POST.get("razao_social") or ""
            ).strip(),
        }
        try:
            if action in {"inativar", "reativar"}:
                if empresa_id <= 0:
                    raise ValueError("Empresa emissora inválida.")
                api_patch(
                    f"{EMPRESAS_EMISSORAS_PATH}/{empresa_id}/status",
                    {"ativo": action == "reativar"},
                )
                messages.success(
                    request,
                    "Empresa emissora "
                    f"{'reativada' if action == 'reativar' else 'inativada'} "
                    "com sucesso.",
                )
            else:
                if not form_data["cnpj"] or not form_data["razao_social"]:
                    raise ValueError("Informe o CNPJ e a razão social.")
                payload = {
                    "cnpj": form_data["cnpj"],
                    "razao_social": form_data["razao_social"],
                }
                if action == "editar":
                    if empresa_id <= 0:
                        raise ValueError("Empresa emissora inválida.")
                    api_put(
                        f"{EMPRESAS_EMISSORAS_PATH}/{empresa_id}",
                        payload,
                    )
                    messages.success(
                        request,
                        "Empresa emissora atualizada com sucesso.",
                    )
                else:
                    api_post(EMPRESAS_EMISSORAS_PATH, payload)
                    messages.success(
                        request,
                        "Empresa emissora cadastrada com sucesso.",
                    )
            return redirect("empresas_emissoras")
        except ValueError as exc:
            messages.error(request, str(exc))
        except ApiError as exc:
            messages.error(
                request,
                f"Empresa emissora: {extract_api_error_message(exc)}",
            )

    empresas = _carregar_empresas_emissoras(
        request,
        incluir_inativas=True,
    )
    return render(
        request,
        "empresas_emissoras.html",
        {
            "empresas": empresas,
            "form_data": form_data,
            "resumo": {
                "total": len(empresas),
                "ativas": sum(
                    1 for empresa in empresas if empresa.get("ativo")
                ),
            },
        },
    )


@require_http_methods(["GET", "POST"])
def follow_up_glosas(request):
    if request.method == "POST":
        registro_id = request.POST.get("registro_glosa_id")
        form_action = request.POST.get("form_action") or "salvar"
        try:
            if form_action == "desfazer":
                api_delete(f"{settings.API_REGISTRO_GLOSA_PATH}/{registro_id}")
                clear_filter_caches()
                return modal_action_response(
                    request,
                    "Tratamento desfeito no Follow-Up de Glosas.",
                    "warning",
                )

            payload = build_registro_glosa_payload(request.POST)
            is_acatar = payload.get("sn_glosado") == "not"
            if registro_id:
                api_payload = api_put(
                    f"{settings.API_REGISTRO_GLOSA_PATH}/{registro_id}",
                    payload,
                )
            else:
                api_payload = api_post(
                    settings.API_REGISTRO_GLOSA_PATH,
                    payload,
                )
            clear_filter_caches()
            return modal_action_response(
                request,
                (
                    "Valor acatado no Follow-Up de Glosas."
                    if is_acatar
                    else "Recurso registrado no Follow-Up de Glosas."
                ),
                "warning" if is_acatar else "success",
                api_payload=api_payload,
            )
        except ApiError as exc:
            payload = build_registro_glosa_payload(request.POST)
            is_acatar = payload.get("sn_glosado") == "not"
            action_name = "acato" if is_acatar else "recurso"
            api_error = contextualize_registro_glosa_error(
                extract_api_error_message(exc),
                is_acatar,
            )
            return modal_action_response(
                request,
                f"Falha ao salvar {action_name}: {api_error}",
                "error",
                status=400,
            )

    filtros = {
        "processo_original": (
            request.GET.get("processo_original") or ""
        ).strip(),
        "processo_recurso": (
            request.GET.get("processo_recurso") or ""
        ).strip(),
        "convenio": (request.GET.get("convenio") or "").strip(),
        "paciente": (request.GET.get("paciente") or "").strip(),
        "cd_remessa": (request.GET.get("cd_remessa") or "").strip(),
        "cd_atendimento": (
            request.GET.get("cd_atendimento") or ""
        ).strip(),
        "tipo_atendimento": (
            request.GET.get("tipo_atendimento") or ""
        ).strip(),
    }
    detalhar_vinculo = as_int_or_zero(
        request.GET.get("detalhar_vinculo")
    )
    page = as_positive_int(request.GET.get("page"), 1)
    limit = 1 if detalhar_vinculo else 10
    offset = (page - 1) * limit
    cards = []
    total = 0
    resumo = {
        "quantidade_glosas": 0,
        "valor_total_glosado": 0,
        "valor_total_pendente": 0,
        "valor_total_tratado": 0,
    }
    consulta_indisponivel = False
    try:
        api_params = {
            "limit": limit,
            "offset": offset,
            "incluir_detalhes": "true" if detalhar_vinculo else "false",
        }
        if detalhar_vinculo:
            api_params["conciliacao_remessa_id"] = detalhar_vinculo
        else:
            api_params["agrupar_por_processo"] = "true"
            api_params.update(
                {
                    key: value
                    for key, value in filtros.items()
                    if value
                }
            )
        response = api_get(FOLLOW_UP_GLOSAS_PATH, params=api_params)
        cards_api = response.get("cards") or []
        if not detalhar_vinculo:
            cards_api = [
                {
                    **card,
                    "pacientes": (
                        []
                        if card.get("conciliacao_remessa_id")
                        else card.get("pacientes") or []
                    ),
                }
                for card in cards_api
            ]
        cards = prepare_follow_up_glosas_cards(cards_api)
        for card in cards:
            card["detalhes_carregados"] = (
                bool(detalhar_vinculo)
                or not card.get("conciliacao_remessa_id")
            )
        total = as_int_or_zero(response.get("total"))
        limit = as_positive_int(response.get("limit"), limit)
        offset = as_int_or_zero(response.get("offset"))
        resumo = {
            "quantidade_glosas": as_int_or_zero(
                response.get("quantidade_glosas")
            ),
            "valor_total_glosado": as_float_or_zero(
                response.get("valor_total_glosado")
            ),
            "valor_total_pendente": as_float_or_zero(
                response.get("valor_total_pendente")
            ),
            "valor_total_tratado": as_float_or_zero(
                response.get("valor_total_tratado")
            ),
        }
    except ApiError as exc:
        if is_service_unavailable_error(exc):
            consulta_indisponivel = True
        else:
            messages.error(request, format_api_error(exc, "Follow-Up de Glosas"))

    processos = group_follow_up_glosas_by_process(cards)
    base_query = {key: value for key, value in filtros.items() if value}
    total_pages = max(ceil(total / limit), 1)
    if page > total_pages:
        return redirect(
            f"{request.path}?{urlencode({**base_query, 'page': total_pages})}"
        )
    pagination = {
        "page": page,
        "total_pages": total_pages,
        "page_options": [
            {"number": number, "selected": number == page}
            for number in range(1, total_pages + 1)
        ],
        "has_previous": page > 1,
        "has_next": page < total_pages,
        "previous_url": (
            f"?{urlencode({**base_query, 'page': page - 1})}"
            if page > 1
            else ""
        ),
        "next_url": (
            f"?{urlencode({**base_query, 'page': page + 1})}"
            if page < total_pages
            else ""
        ),
        "start": offset + 1 if cards and total else 0,
        "end": min(offset + len(processos), total),
        "total": total,
        "query": base_query,
    }
    return render(
        request,
        "follow_up_glosas.html",
        {
            "cards": cards,
            "processos": processos,
            "filtros": filtros,
            "convenios": get_convenio_dropdown_options(
                filtros["convenio"]
            ),
            "resumo": resumo,
            "pagination": pagination,
            "consulta_indisponivel": consulta_indisponivel,
            "tipos_atendimento": TIPOS_ATENDIMENTO,
        },
    )


@require_http_methods(["GET", "POST"])
def associacoes_remessas_ipm(request):
    if request.method == "POST":
        acao = (request.POST.get("acao") or "associar").strip()
        associacao_id = as_int_or_zero(request.POST.get("associacao_id"))
        try:
            if acao == "excluir":
                api_delete(
                    f"{ASSOCIACOES_REMESSAS_IPM_PATH}/{associacao_id}"
                )
                messages.success(request, "Associação manual excluída.")
            else:
                payload = {
                    "numero_processo": request.POST.get(
                        "numero_processo", ""
                    ),
                    "competencia_producao": request.POST.get(
                        "competencia_producao", ""
                    ),
                    "nr": request.POST.get("nr", ""),
                    "cd_remessa": as_int_or_zero(
                        request.POST.get("cd_remessa")
                    ),
                }
                if associacao_id:
                    api_put(
                        f"{ASSOCIACOES_REMESSAS_IPM_PATH}/{associacao_id}",
                        {"cd_remessa": payload["cd_remessa"]},
                    )
                    messages.success(request, "Associação manual atualizada.")
                else:
                    api_post(ASSOCIACOES_REMESSAS_IPM_PATH, payload)
                    messages.success(request, "Remessa associada ao NR.")
            clear_filter_caches()
            query = {}
            if request.POST.get("competencia"):
                query["competencia"] = request.POST["competencia"]
            if request.POST.get("numero_processo_filtro"):
                query["numero_processo"] = request.POST[
                    "numero_processo_filtro"
                ]
            if request.POST.get("page"):
                query["page"] = request.POST["page"]
            return redirect(
                f"{request.path}?{urlencode(query)}" if query else request.path
            )
        except ApiError as exc:
            messages.error(
                request,
                format_api_error(exc, "Associação manual de remessa"),
            )

    filtros = {
        "competencia": (request.GET.get("competencia") or "").strip(),
        "numero_processo": (
            request.GET.get("numero_processo") or ""
        ).strip(),
    }
    processos = []
    page = as_positive_int(request.GET.get("page"), 1)
    limit = 10
    offset = (page - 1) * limit
    total = 0
    resumo = {
        "processos_pendentes": 0,
        "nrs_pendentes": 0,
        "associacoes_realizadas": 0,
        "remessas_disponiveis": 0,
    }
    consulta_indisponivel = False
    try:
        payload = api_get(
            ASSOCIACOES_REMESSAS_IPM_PATH,
            params={
                **{key: value for key, value in filtros.items() if value},
                "limit": limit,
                "offset": offset,
            },
        )
        processos = payload.get("processos") or []
        total = as_int_or_zero(payload.get("total"))
        limit = as_positive_int(payload.get("limit"), limit)
        offset = as_int_or_zero(payload.get("offset"))
        resumo.update(payload.get("resumo") or {})
        for processo in processos:
            processo["data_abertura_formatada"] = format_api_date(
                processo.get("data_abertura")
            )
    except ApiError as exc:
        consulta_indisponivel = is_service_unavailable_error(exc)
        if not consulta_indisponivel:
            messages.error(
                request,
                format_api_error(exc, "Associações manuais IPM"),
            )
    base_query = {key: value for key, value in filtros.items() if value}
    total_pages = max(ceil(total / limit), 1)
    if page > total_pages:
        return redirect(
            f"{request.path}?{urlencode({**base_query, 'page': total_pages})}"
        )
    pagination = {
        "page": page,
        "total_pages": total_pages,
        "page_options": [
            {"number": number, "selected": number == page}
            for number in range(1, total_pages + 1)
        ],
        "has_previous": page > 1,
        "has_next": page < total_pages,
        "previous_url": (
            f"?{urlencode({**base_query, 'page': page - 1})}"
            if page > 1
            else ""
        ),
        "next_url": (
            f"?{urlencode({**base_query, 'page': page + 1})}"
            if page < total_pages
            else ""
        ),
        "start": offset + 1 if processos and total else 0,
        "end": min(offset + len(processos), total),
        "total": total,
        "query": base_query,
    }
    return render(
        request,
        "associacoes_remessas_ipm.html",
        {
            "processos": processos,
            "filtros": filtros,
            "resumo": resumo,
            "pagination": pagination,
            "consulta_indisponivel": consulta_indisponivel,
        },
    )


@require_http_methods(["GET", "POST"])
def conta_atendimento(request):
    if request.method == "POST":
        registro_id = request.POST.get("registro_glosa_id")
        form_action = request.POST.get("form_action") or "salvar"
        try:
            if form_action == "desfazer" and registro_id:
                api_delete(f"{settings.API_REGISTRO_GLOSA_PATH}/{registro_id}")
                clear_filter_caches()
                return modal_action_response(
                    request,
                    "Registro desfeito a partir da conta selecionada.",
                    "error",
                )

            payload = build_registro_glosa_payload(request.POST)
            is_acatar = payload.get("sn_glosado") == "not"
            if registro_id:
                api_payload = api_put(f"{settings.API_REGISTRO_GLOSA_PATH}/{registro_id}", payload)
                clear_filter_caches()
                success_message = (
                    "Acato atualizado a partir da conta selecionada."
                    if is_acatar
                    else "Glosa atualizada a partir da conta selecionada."
                )
                return modal_action_response(
                    request,
                    success_message,
                    "warning",
                    api_payload=api_payload,
                )
            else:
                api_payload = api_post(settings.API_REGISTRO_GLOSA_PATH, payload)
                clear_filter_caches()
                success_message = (
                    "Acato registrado a partir da conta selecionada."
                    if is_acatar
                    else "Glosa registrada a partir da conta selecionada."
                )
                return modal_action_response(
                    request,
                    success_message,
                    "success",
                    api_payload=api_payload,
                )
        except ApiError as exc:
            payload = build_registro_glosa_payload(request.POST)
            is_acatar = payload.get("sn_glosado") == "not"
            action_name = "acato" if is_acatar else "glosa"
            if form_action == "desfazer":
                error_message = f"Falha ao desfazer registro: {extract_api_error_message(exc)}"
            else:
                api_error = contextualize_registro_glosa_error(
                    extract_api_error_message(exc),
                    is_acatar,
                )
                error_message = f"Falha ao salvar {action_name}: {api_error}"
            return modal_action_response(
                request,
                error_message,
                "error",
                status=400,
            )

    if request.method == "GET" and request.GET and is_browser_reload(request):
        return redirect(request.path)

    filtros = request.GET.dict()
    filtros.pop("_modal_action", None)
    filtros.pop("limit", None)
    filtros.pop("offset", None)
    page = as_positive_int(filtros.pop("page", None), 1)
    search_fields = {
        "cd_remessa",
        "cd_atendimento",
        "cd_reg",
        "nm_paciente",
        "nm_convenio",
        "descricao",
        "tp_atendimento",
    }
    pesquisa_executada = any(
        str(filtros.get(key) or "").strip()
        for key in search_fields
    )
    if request.GET and not pesquisa_executada:
        messages.warning(
            request,
            "Informe pelo menos um critério para realizar a pesquisa.",
        )
    limit = PATIENTS_PER_PAGE
    offset = (page - 1) * limit
    api_filtros = {k: v for k, v in filtros.items() if v}
    api_filtros["limit"] = limit
    api_filtros["offset"] = offset
    consulta_indisponivel = False
    total_pacientes = 0
    tiss_motivos = []
    convenios = []
    try:
        convenios = get_convenio_filter_options()
    except ApiError as exc:
        messages.error(request, format_api_error(exc, "Consulta de convênios"))

    try:
        payload_tiss = get_cached_api_payload(
            CONTA_TISS_CACHE_KEY,
            settings.API_TISS_PATH,
            {"limit": 600},
        )
        if isinstance(payload_tiss, dict):
            tiss_motivos = payload_tiss.get("itens", [])
    except ApiError as exc:
        messages.error(request, format_api_error(exc, "Consulta TISS"))

    try:
        if pesquisa_executada:
            payload = get_cached_api_payload(
                "conta-atendimento:contas",
                settings.API_CONTA_ATENDIMENTO_PATH,
                api_filtros,
            )
            contas = as_list(payload)
            if isinstance(payload, dict):
                total_pacientes = as_int_or_zero(payload.get("total"))
                limit = as_positive_int(payload.get("limit"), PATIENTS_PER_PAGE)
                offset = as_int_or_zero(payload.get("offset"))
            else:
                total_pacientes = len(_group_contas(contas))
            try:
                attach_registros_glosa(contas, api_filtros)
            except ApiError as exc:
                messages.error(
                    request,
                    format_api_error(exc, "Consulta de glosas registradas"),
                )
        else:
            contas = []
    except ApiError as exc:
        contas = []
        if is_service_unavailable_error(exc):
            consulta_indisponivel = True
        else:
            messages.error(request, format_api_error(exc, "Consulta de conta/atendimento"))
    for conta in contas:
        if isinstance(conta, dict):
            conta["dt_atendimento_formatada"] = format_api_date(
                conta.get("dt_atendimento")
            )
            conta["dt_alta_formatada"] = format_api_date(
                conta.get("dt_alta")
            )
            conta["hr_lancamento_formatada"] = format_lancamento_datetime(
                conta.get("dt_lancamento"),
                conta.get("hr_lancamento"),
            )
    grupos = _group_contas(contas)
    if pesquisa_executada and not total_pacientes:
        total_pacientes = len(grupos)

    base_query = {k: v for k, v in filtros.items() if v}
    total_pages = max(ceil(total_pacientes / PATIENTS_PER_PAGE), 1)
    if pesquisa_executada and page > total_pages:
        return redirect(
            f"{request.path}?{urlencode({**base_query, 'page': total_pages})}"
        )

    page = min(page, total_pages)
    grupos_pagina = grupos
    page_options = [
        {"number": number, "selected": number == page}
        for number in range(1, total_pages + 1)
    ]
    pagination = {
        "page": page,
        "total_pages": total_pages,
        "page_options": page_options,
        "has_previous": page > 1,
        "has_next": page < total_pages,
        "previous_url": (
            f"?{urlencode({**base_query, 'page': page - 1})}"
            if page > 1
            else ""
        ),
        "next_url": (
            f"?{urlencode({**base_query, 'page': page + 1})}"
            if page < total_pages
            else ""
        ),
        "start": offset + 1 if grupos and total_pacientes else 0,
        "end": min(offset + len(grupos), total_pacientes),
        "total": total_pacientes,
        "query": base_query,
    }
    resumo = {
        "agrupamentos": len(grupos),
        "pacientes": len(grupos),
        "atendimentos": sum(g.get("num_atendimentos", 0) for g in grupos),
        "valor_total": sum(g.get("total", 0) for g in grupos),
    }
    return render(
        request,
        "conta_atendimento.html",
        {
            "grupos": grupos_pagina,
            "filtros": filtros,
            "resumo": resumo,
            "pagination": pagination,
            "consulta_indisponivel": consulta_indisponivel,
            "tipos_atendimento": TIPOS_ATENDIMENTO,
            "tiss_motivos": tiss_motivos,
            "convenios": convenios,
            "pesquisa_executada": pesquisa_executada,
        },
    )


@require_http_methods(["GET", "POST"])
def acompanhamento(request):
    if request.method == "POST":
        registro_ids = [
            item.strip()
            for item in (request.POST.get("registro_ids") or "").split(",")
            if item.strip()
        ]
        qtd_recebida = as_int_or_none(request.POST.get("qtd_recebida"))
        payload = {
            "dt_recebimento": request.POST.get("dt_recebimento") or None,
            "valor_recebido": as_float_or_zero(request.POST.get("valor_recebido")),
            "qtd_recebida": qtd_recebida,
            "observacao_recebimento": (
                request.POST.get("observacao_recebimento") or None
            ),
        }
        if not registro_ids:
            messages.error(request, "Nenhum registro selecionado para recebimento.")
            return redirect("acompanhamento")
        if qtd_recebida is None or qtd_recebida < 1:
            messages.error(
                request,
                "Informe uma quantidade recebida maior que zero.",
            )
            return redirect(request.POST.get("next") or "acompanhamento")

        try:
            for registro_id in registro_ids:
                api_patch(
                    f"{settings.API_REGISTRO_GLOSA_PATH}/{registro_id}/recebimento",
                    payload,
                )
            clear_filter_caches()
            messages.success(
                request,
                "Recebimento registrado para o processo selecionado.",
            )
        except ApiError as exc:
            messages.error(request, format_api_error(exc, "Recebimento de glosa"))

        redirect_url = request.get_full_path()
        if request.POST.get("next"):
            redirect_url = request.POST["next"]
        return redirect(redirect_url)

    filtros = request.GET.dict()
    modo = filtros.pop("modo", "kanban")
    faixa = filtros.pop("faixa", "")
    api_filtros = {
        key: value
        for key, value in filtros.items()
        if key
        in {
            "cd_remessa",
            "cd_atendimento",
            "cd_reg",
            "nm_convenio",
            "processo_original",
            "processo_recurso",
            "nm_paciente",
            "tp_atendimento",
        }
        and value
    }
    convenios = []
    prazos_convenio = []
    try:
        convenios = get_convenio_filter_options()
    except ApiError as exc:
        messages.error(request, format_api_error(exc, "Consulta de convênios"))
    try:
        prazos_payload = get_cached_dashboard_payload(
            DASHBOARD_PRAZOS_CACHE_KEY,
            PRAZOS_RECURSO_CONVENIO_PATH,
        )
        prazos_convenio = prazos_payload.get("convenios", [])
    except ApiError as exc:
        messages.error(request, format_api_error(exc, "Configuração por convênio"))

    try:
        payload = get_cached_dashboard_payload(
            ACOMPANHAMENTO_GLOSAS_CACHE_KEY,
            settings.API_REGISTRO_GLOSA_PATH,
            {"limit": DASHBOARD_GLOSAS_LIMIT},
        )
        registros = payload.get("glosas", []) if isinstance(payload, dict) else []
        registros = [
            registro
            for registro in registros
            if is_recurso_registro(registro)
            and has_internal_treatment(registro)
        ]
        convenios_desabilitados = disabled_convenio_ids(prazos_convenio)
        registros = [
            registro
            for registro in registros
            if is_active_registro(registro)
            and is_enabled_convenio_registro(registro, convenios_desabilitados)
        ]
        registros = apply_acompanhamento_filters(registros, api_filtros)
    except ApiError as exc:
        registros = []
        messages.error(request, format_api_error(exc, "Acompanhamento"))

    rows = build_acompanhamento_rows(registros)
    cards = build_acompanhamento_cards(rows)
    kanban_columns = build_kanban_columns(cards)
    if faixa:
        rows_filtradas = [
            row
            for row in rows
            if (
                "recebidas"
                if row.get("possui_recebimento")
                else row["idade_bucket"]
            )
            == faixa
        ]
    else:
        rows_filtradas = rows

    resumo = build_acompanhamento_resumo(rows_filtradas)

    return render(
        request,
        "acompanhamento.html",
        {
            "filtros": filtros,
            "modo": modo if modo in {"kanban", "tabela"} else "kanban",
            "faixa": faixa,
            "faixas": ACOMPANHAMENTO_BUCKETS,
            "kanban_columns": kanban_columns,
            "rows": rows_filtradas,
            "resumo": resumo,
            "tipos_atendimento": TIPOS_ATENDIMENTO,
            "convenios": convenios,
            "current_full_path": request.get_full_path(),
        },
    )


def glosas(request):
    try:
        registros = get_cached_api_payload("glosas", "/glosas", request.GET.dict())
    except ApiError as exc:
        registros = []
        messages.error(request, format_api_error(exc, "Glosas"))
    return render(request, "glosas.html", {"glosas": registros})


@require_http_methods(["GET", "POST"])
def remessas(request):
    if request.method == "POST":
        try:
            api_post("/remessas", request.POST.dict())
            clear_filter_caches()
            messages.success(request, "Remessa enviada para cadastro.")
            return redirect("remessas")
        except ApiError as exc:
            messages.error(request, format_api_error(exc, "Cadastro de remessa"))
    try:
        registros = get_cached_api_payload("remessas", "/remessas")
    except ApiError as exc:
        registros = []
        messages.error(request, format_api_error(exc, "Remessas"))
    return render(request, "remessas.html", {"remessas": registros})


@require_http_methods(["GET", "POST"])
def recursos(request):
    if request.method == "POST":
        try:
            api_post("/recursos", request.POST.dict())
            clear_filter_caches()
            messages.success(request, "Recurso enviado para cadastro.")
            return redirect("recursos")
        except ApiError as exc:
            messages.error(request, format_api_error(exc, "Cadastro de recurso"))
    try:
        registros = get_cached_api_payload("recursos", "/recursos")
    except ApiError as exc:
        registros = []
        messages.error(request, format_api_error(exc, "Recursos"))
    return render(request, "recursos.html", {"recursos": registros})


@require_http_methods(["GET", "POST"])
def recebimentos(request):
    if request.method == "POST":
        try:
            api_post("/recebimentos", request.POST.dict())
            clear_filter_caches()
            messages.success(request, "Recebimento enviado para cadastro.")
            return redirect("recebimentos")
        except ApiError as exc:
            messages.error(request, format_api_error(exc, "Cadastro de recebimento"))
    try:
        registros = get_cached_api_payload("recebimentos", "/recebimentos")
    except ApiError as exc:
        registros = []
        messages.error(request, format_api_error(exc, "Recebimentos"))
    return render(request, "recebimentos.html", {"recebimentos": registros})


def build_conciliacao_faturamento_payload(data):
    try:
        notas = json.loads(data.get("notas_json") or "[]")
    except (TypeError, ValueError) as exc:
        raise ValueError("A lista de NFS-e informada é inválida.") from exc

    if not isinstance(notas, list):
        raise ValueError("A lista de NFS-e informada é inválida.")
    for nota in notas:
        if not isinstance(nota, dict):
            raise ValueError("A lista de NFS-e informada é inválida.")
        valor_liquido = as_float_or_none(
            nota.pop("valor_liquido_nfse", None)
        )
        valor_bruto_legado = as_float_or_none(
            nota.pop("valor_bruto_remessa", None)
        )
        if valor_liquido is None and valor_bruto_legado is None:
            continue
        valor_glosado = as_float_or_zero(nota.get("valor_glosado"))
        valor_impostos = as_float_or_zero(nota.get("valor_impostos"))
        if valor_impostos < 0:
            raise ValueError("O total das retenções não pode ser negativo.")
        if valor_glosado < 0:
            raise ValueError("O valor glosado não pode ser negativo.")
        if valor_liquido is None:
            valor_liquido = (
                valor_bruto_legado - valor_glosado - valor_impostos
            )
        valor_liquido = round(valor_liquido, 2)
        if valor_liquido <= 0:
            raise ValueError(
                "O valor líquido conciliado da NFS-e deve ser maior que zero."
            )
        nota["valor_alocado"] = f"{valor_liquido:.2f}"
        nota["sn_glosado"] = valor_glosado > 0
    cd_remessa = as_int_or_none(data.get("cd_remessa"))
    if cd_remessa is None or cd_remessa <= 0:
        raise ValueError("Informe uma remessa válida para a conciliação.")

    return {
        "cd_remessa": cd_remessa,
        "processo_recebimento": (data.get("processo_recebimento") or "").strip(),
        "notas": notas,
    }


def build_recebimento_remessa_payload(data):
    conciliacao_id = as_int_or_none(data.get("conciliacao_id"))
    cd_remessa = as_int_or_none(data.get("cd_remessa"))
    conta_bancaria_id = as_int_or_none(data.get("conta_bancaria_id"))
    numero_nfse = (data.get("numero_nfse") or "").strip()
    data_recebimento = data.get("data_recebimento") or None
    valor_recebido = as_float_or_none(data.get("valor_recebido"))
    if cd_remessa is None or cd_remessa <= 0:
        raise ValueError("Informe uma remessa válida para o recebimento.")
    if not numero_nfse:
        raise ValueError("Informe a NFS-e conciliada ao recebimento.")
    if not data_recebimento:
        raise ValueError("Informe a data do recebimento.")
    if valor_recebido is None or valor_recebido <= 0:
        raise ValueError("Informe um valor recebido maior que zero.")
    if conta_bancaria_id is None or conta_bancaria_id <= 0:
        raise ValueError("Selecione a conta bancária do recebimento.")
    return {
        "conciliacao_id": conciliacao_id,
        "cd_remessa": cd_remessa,
        "numero_nfse": numero_nfse,
        "data_recebimento": data_recebimento,
        "valor_recebido": f"{valor_recebido:.2f}",
        "conta_bancaria_id": conta_bancaria_id,
        "conta_plano_contas": (
            data.get("conta_plano_contas") or ""
        ).strip()
        or None,
        "conta_centro_custo": (
            data.get("conta_centro_custo") or ""
        ).strip()
        or None,
        "lancamento_extrato_id": as_int_or_none(
            data.get("lancamento_extrato_id")
        ),
    }


def build_edicao_conciliacao_payload(data):
    processo_recebimento = (
        data.get("processo_recebimento") or ""
    ).strip()
    data_previsao = data.get("data_previsao_recebimento") or None
    if not processo_recebimento:
        raise ValueError("Informe o processo de recebimento.")
    if not data_previsao:
        raise ValueError("Informe a data de previsão de recebimento.")
    payload = {
        "processo_recebimento": processo_recebimento,
        "data_previsao_recebimento": data_previsao,
    }
    codigos = (
        data.getlist("cd_remessa")
        if hasattr(data, "getlist")
        else data.get("cd_remessa", [])
    )
    if isinstance(codigos, str):
        codigos = [codigos]
    remessas = []
    codigos_informados = set()
    for codigo_bruto in codigos:
        cd_remessa = as_int_or_none(codigo_bruto)
        if cd_remessa is None or cd_remessa <= 0:
            raise ValueError("Informe uma remessa válida para a edição.")
        if cd_remessa in codigos_informados:
            raise ValueError("Uma remessa não pode ser informada duas vezes.")
        codigos_informados.add(cd_remessa)
        valor_glosado = as_float_or_none(
            data.get(f"valor_glosado_{cd_remessa}")
        )
        valor_recebido = as_float_or_none(
            data.get(f"valor_recebido_{cd_remessa}")
        )
        valor_impostos = as_float_or_none(
            data.get(f"valor_impostos_{cd_remessa}")
        )
        if valor_impostos is None:
            valor_impostos = 0.0
        if valor_glosado is None or valor_glosado < 0:
            raise ValueError(
                f"Informe um valor de glosa válido para a remessa "
                f"{cd_remessa}."
            )
        if valor_recebido is None or valor_recebido <= 0:
            raise ValueError(
                f"Informe um valor recebido maior que zero para a remessa "
                f"{cd_remessa}."
            )
        if valor_impostos < 0:
            raise ValueError(
                f"Informe um total de retenções válido para a remessa "
                f"{cd_remessa}."
            )
        remessas.append(
            {
                "cd_remessa": cd_remessa,
                "valor_glosado": f"{valor_glosado:.2f}",
                "valor_recebido": f"{valor_recebido:.2f}",
                "valor_impostos": f"{valor_impostos:.2f}",
            }
        )
    if remessas:
        payload["remessas"] = remessas
    return payload


def build_alteracoes_auditoria_conciliacao(evento):
    anteriores = evento.get("dados_anteriores") or {}
    novos = evento.get("dados_novos") or {}
    if evento.get("acao") == "criacao" or not novos:
        return []

    def formatar(valor, tipo="texto"):
        if valor in (None, ""):
            return "-"
        if tipo == "moeda":
            return f"R$ {format_brl_input(valor)}"
        if tipo == "data":
            return format_api_date(valor)
        if tipo == "booleano":
            return "Ativa" if valor else "Inativa"
        return str(valor)

    alteracoes = []

    def adicionar(campo, anterior, novo, tipo="texto"):
        if anterior == novo:
            return
        alteracoes.append(
            {
                "campo": campo,
                "anterior": formatar(anterior, tipo),
                "novo": formatar(novo, tipo),
            }
        )

    adicionar(
        "Processo de recebimento",
        anteriores.get("processo_recebimento"),
        novos.get("processo_recebimento"),
    )
    adicionar(
        "Previsão de recebimento",
        anteriores.get("data_previsao_recebimento"),
        novos.get("data_previsao_recebimento"),
        "data",
    )
    if "ativo" in anteriores and "ativo" in novos:
        adicionar(
            "Situação",
            anteriores.get("ativo"),
            novos.get("ativo"),
            "booleano",
        )

    remessas_anteriores = {
        str(remessa.get("cd_remessa")): remessa
        for remessa in anteriores.get("remessas", [])
    }
    remessas_novas = {
        str(remessa.get("cd_remessa")): remessa
        for remessa in novos.get("remessas", [])
    }
    for cd_remessa in sorted(
        set(remessas_anteriores) | set(remessas_novas)
    ):
        anterior = remessas_anteriores.get(cd_remessa, {})
        novo = remessas_novas.get(cd_remessa, {})
        adicionar(
            f"Valor recebido · remessa {cd_remessa}",
            anterior.get("valor_alocado_nfse"),
            novo.get("valor_alocado_nfse"),
            "moeda",
        )
        adicionar(
            f"Valor glosado · remessa {cd_remessa}",
            anterior.get("valor_glosado"),
            novo.get("valor_glosado"),
            "moeda",
        )
        adicionar(
            f"Total das retenções · remessa {cd_remessa}",
            anterior.get("valor_impostos"),
            novo.get("valor_impostos"),
            "moeda",
        )

    recebimento_anterior = anteriores.get("recebimento") or {}
    recebimento_novo = novos.get("recebimento") or {}
    if recebimento_anterior or recebimento_novo:
        adicionar(
            "Data do recebimento bancário",
            recebimento_anterior.get("data_recebimento"),
            recebimento_novo.get("data_recebimento"),
            "data",
        )
        adicionar(
            "Valor do recebimento bancário",
            recebimento_anterior.get("valor_recebido"),
            recebimento_novo.get("valor_recebido"),
            "moeda",
        )
        adicionar(
            "Conta bancária do recebimento",
            recebimento_anterior.get("conta_bancaria_id"),
            recebimento_novo.get("conta_bancaria_id"),
        )
        adicionar(
            "Conta do plano de contas",
            recebimento_anterior.get("conta_plano_contas"),
            recebimento_novo.get("conta_plano_contas"),
        )
        adicionar(
            "Conta do centro de custo",
            recebimento_anterior.get("conta_centro_custo"),
            recebimento_novo.get("conta_centro_custo"),
        )
        adicionar(
            "Lançamento financeiro",
            recebimento_anterior.get("lancamento_extrato_id"),
            recebimento_novo.get("lancamento_extrato_id"),
        )
    return alteracoes


@require_http_methods(["GET", "POST"])
def conciliacao_faturamento(request):
    (
        page,
        page_size,
        filtros,
        remessas_params,
        remessas_cache_key,
        remessas_snapshot_key,
    ) = _conciliacao_remessas_cache_context(request)
    if request.method == "POST":
        try:
            payload = build_conciliacao_faturamento_payload(request.POST)
            cd_remessa = payload.pop("cd_remessa")
            remessas_cached_payload = cache.get(
                remessas_cache_key
            ) or cache.get(remessas_snapshot_key)
            api_response = api_post(
                f"{CONCILIACAO_FATURAMENTO_PATH}/remessas/"
                f"{cd_remessa}/conciliar",
                payload,
            )
            clear_filter_caches()
            _restore_conciliacao_remessas_cache(
                remessas_cache_key,
                remessas_snapshot_key,
                remessas_cached_payload,
                api_response,
            )
            messages.success(request, "Remessa conciliada com sucesso.")
            return redirect(request.get_full_path())
        except (ApiError, ValueError) as exc:
            if isinstance(exc, ApiError):
                error_message = extract_api_error_message(exc)
            else:
                error_message = str(exc)
            messages.error(request, error_message)

    offset = remessas_params["offset"]

    try:
        remessas_payload = get_cached_api_payload(
            "financeiro:remessas-conciliacao",
            f"{CONCILIACAO_FATURAMENTO_PATH}/remessas",
            params=remessas_params,
        )
        cache.set(remessas_snapshot_key, deepcopy(remessas_payload), 900)
        remessas = remessas_payload.get("remessas", [])
        total_remessas = int(
            remessas_payload.get("total", len(remessas))
        )
        valor_total_pendente = remessas_payload.get(
            "valor_total_nao_conciliado",
            0,
        )
        valor_total_conciliado = remessas_payload.get(
            "valor_total_conciliado",
            0,
        )
    except ApiError as exc:
        remessas = []
        total_remessas = 0
        valor_total_pendente = 0
        valor_total_conciliado = 0
        messages.error(
            request,
            format_api_error(exc, "Conciliação Manual"),
        )

    try:
        contas_payload = get_cached_api_payload(
            "financeiro:contas-bancarias",
            CONTAS_BANCARIAS_PATH,
        )
        contas_bancarias = contas_payload.get("contas", [])
    except ApiError as exc:
        contas_bancarias = []
        messages.error(request, format_api_error(exc, "Contas bancárias"))

    total_pages = max(ceil(total_remessas / page_size), 1)
    base_query = {
        key: value for key, value in filtros.items() if value
    }
    if page > total_pages:
        return redirect(
            f"{request.path}?{urlencode({**base_query, 'page': total_pages})}"
        )

    page_options = [
        {"number": number, "selected": number == page}
        for number in range(1, total_pages + 1)
    ]
    pagination = {
        "page": page,
        "total_pages": total_pages,
        "page_options": page_options,
        "has_previous": page > 1,
        "has_next": page < total_pages,
        "previous_url": (
            f"?{urlencode({**base_query, 'page': page - 1})}"
            if page > 1
            else ""
        ),
        "next_url": (
            f"?{urlencode({**base_query, 'page': page + 1})}"
            if page < total_pages
            else ""
        ),
        "start": offset + 1 if remessas and total_remessas else 0,
        "end": min(offset + len(remessas), total_remessas),
        "total": total_remessas,
        "query": base_query,
    }
    return render(
        request,
        "conciliacao_faturamento.html",
        {
            "remessas": remessas,
            "contas_bancarias": contas_bancarias,
            "filtros": filtros,
            "convenios": get_convenio_dropdown_options(
                filtros["convenio"]
            ),
            "total_remessas": total_remessas,
            "valor_total_conciliado": valor_total_conciliado,
            "valor_total_pendente": valor_total_pendente,
            "pagination": pagination,
        },
    )


@require_http_methods(["GET", "POST"])
def conciliacoes_sem_recebimento(request):
    if request.method == "POST":
        form_action = request.POST.get("form_action") or "recebimento"
        try:
            if form_action == "editar_recebimento":
                recebimento_id = as_int_or_none(
                    request.POST.get("recebimento_id")
                )
                if recebimento_id is None:
                    raise ValueError("Informe um recebimento válido.")
                api_patch(
                    f"{CONCILIACAO_FATURAMENTO_PATH}/"
                    f"recebimentos-remessas/{recebimento_id}",
                    build_recebimento_remessa_payload(request.POST),
                )
                clear_filter_caches()
                messages.success(
                    request,
                    "Recebimento financeiro atualizado com sucesso.",
                )
                return redirect(request.get_full_path())
            if form_action == "excluir_recebimento":
                recebimento_id = as_int_or_none(
                    request.POST.get("recebimento_id")
                )
                if recebimento_id is None:
                    raise ValueError("Informe um recebimento válido.")
                api_delete(
                    f"{CONCILIACAO_FATURAMENTO_PATH}/"
                    f"recebimentos-remessas/{recebimento_id}"
                )
                clear_filter_caches()
                messages.success(
                    request,
                    "Recebimento financeiro excluído com sucesso.",
                )
                return redirect(request.get_full_path())
            if form_action == "editar_conciliacao":
                conciliacao_id = as_int_or_none(
                    request.POST.get("conciliacao_id")
                )
                if conciliacao_id is None:
                    raise ValueError("Informe uma conciliação válida.")
                api_put(
                    f"{CONCILIACOES_GERENCIAMENTO_PATH}/{conciliacao_id}",
                    build_edicao_conciliacao_payload(request.POST),
                )
                clear_filter_caches()
                messages.success(
                    request,
                    "Conciliação atualizada com sucesso.",
                )
                return redirect(request.get_full_path())
            if form_action == "inativar_conciliacao":
                conciliacao_id = as_int_or_none(
                    request.POST.get("conciliacao_id")
                )
                if conciliacao_id is None:
                    raise ValueError("Informe uma conciliação válida.")
                api_delete(
                    f"{CONCILIACOES_GERENCIAMENTO_PATH}/{conciliacao_id}"
                )
                clear_filter_caches()
                messages.success(
                    request,
                    "Conciliação inativada com sucesso.",
                )
                return redirect(request.get_full_path())
            recebimento_payload = build_recebimento_remessa_payload(
                request.POST
            )
            api_post(
                f"{CONCILIACAO_FATURAMENTO_PATH}/recebimentos-remessas",
                recebimento_payload,
            )
            clear_filter_caches()
            messages.success(
                request,
                (
                    "Recebimento da NFS-e registrado com sucesso para a "
                    f"remessa {recebimento_payload['cd_remessa']}."
                ),
            )
            return redirect(request.get_full_path())
        except ApiError as exc:
            messages.error(request, extract_api_error_message(exc))
        except ValueError as exc:
            messages.error(request, str(exc))

    try:
        page = max(int(request.GET.get("page") or 1), 1)
    except ValueError:
        page = 1
    page_size = 25
    filtros = {
        "numero_nfse": (request.GET.get("numero_nfse") or "").strip(),
        "cd_remessa": (request.GET.get("cd_remessa") or "").strip(),
        "convenio": (request.GET.get("convenio") or "").strip(),
        "processo_recebimento": (
            request.GET.get("processo_recebimento") or ""
        ).strip(),
    }
    offset = (page - 1) * page_size

    try:
        payload = get_cached_api_payload(
            "financeiro:conciliacoes-sem-recebimento:v3",
            CONCILIACOES_SEM_RECEBIMENTO_PATH,
            params={
                **{key: value or None for key, value in filtros.items()},
                "limit": page_size,
                "offset": offset,
            },
        )
        conciliacoes = payload.get("conciliacoes", [])
        total_conciliacoes = int(payload.get("total", len(conciliacoes)))
        total_remessas_sem_recebimento = int(
            payload.get("total_remessas_sem_recebimento", 0)
        )
        valor_total_pendente = payload.get("valor_total_pendente", 0)
        valor_total_recebido = payload.get("valor_total_recebido", 0)
    except ApiError as exc:
        conciliacoes = []
        total_conciliacoes = 0
        total_remessas_sem_recebimento = 0
        valor_total_pendente = 0
        valor_total_recebido = 0
        messages.error(
            request,
            format_api_error(exc, "Conciliação Financeira"),
        )

    try:
        contas_payload = get_cached_api_payload(
            "financeiro:contas-bancarias",
            CONTAS_BANCARIAS_PATH,
        )
        contas_bancarias = contas_payload.get("contas", [])
    except ApiError as exc:
        contas_bancarias = []
        messages.error(request, format_api_error(exc, "Contas bancárias"))

    contas_por_id = {
        str(conta.get("id")): conta for conta in contas_bancarias
    }
    for remessa in conciliacoes:
        for nota in remessa.get("notas", []):
            for recebimento in nota.get("recebimentos", []):
                conta_bancaria_id = recebimento.get("conta_bancaria_id")
                recebimento["conta_bancaria_label"] = (
                    format_conta_bancaria_label(
                        contas_por_id.get(str(conta_bancaria_id)),
                        conta_bancaria_id,
                    )
                )
                (
                    recebimento["conta_bancaria_banco"],
                    separator,
                    recebimento["conta_bancaria_dados"],
                ) = recebimento["conta_bancaria_label"].partition(" · ")
                if not separator:
                    recebimento["conta_bancaria_dados"] = "-"

    total_pages = max(ceil(total_conciliacoes / page_size), 1)
    base_query = {
        key: value for key, value in filtros.items() if value
    }
    if page > total_pages:
        return redirect(
            f"{request.path}?{urlencode({**base_query, 'page': total_pages})}"
        )
    pagination = {
        "page": page,
        "total_pages": total_pages,
        "page_options": [
            {"number": number, "selected": number == page}
            for number in range(1, total_pages + 1)
        ],
        "has_previous": page > 1,
        "has_next": page < total_pages,
        "previous_url": (
            f"?{urlencode({**base_query, 'page': page - 1})}"
            if page > 1
            else ""
        ),
        "next_url": (
            f"?{urlencode({**base_query, 'page': page + 1})}"
            if page < total_pages
            else ""
        ),
        "total": total_conciliacoes,
        "query": base_query,
    }
    return render(
        request,
        "conciliacoes_sem_recebimento.html",
        {
            "conciliacoes": conciliacoes,
            "filtros": filtros,
            "convenios": get_convenio_dropdown_options(
                filtros["convenio"]
            ),
            "total_conciliacoes": total_conciliacoes,
            "total_remessas_sem_recebimento": (
                total_remessas_sem_recebimento
            ),
            "valor_total_pendente": valor_total_pendente,
            "valor_total_recebido": valor_total_recebido,
            "contas_bancarias": contas_bancarias,
            "pagination": pagination,
        },
    )


@require_http_methods(["GET"])
def conciliacoes_financeiras(request):
    try:
        page = max(int(request.GET.get("page") or 1), 1)
    except ValueError:
        page = 1
    page_size = 25
    filtros = {
        "numero_nfse": (
            request.GET.get("numero_nfse") or ""
        ).strip(),
        "cd_remessa": (request.GET.get("cd_remessa") or "").strip(),
        "convenio": (request.GET.get("convenio") or "").strip(),
        "processo_recebimento": (
            request.GET.get("processo_recebimento") or ""
        ).strip(),
        "situacao": (request.GET.get("situacao") or "").strip(),
        "incluir_inativas": (
            "true" if request.GET.get("incluir_inativas") else "false"
        ),
    }
    offset = (page - 1) * page_size
    try:
        payload = api_get(
            CONCILIACOES_GERENCIAMENTO_PATH,
            params={
                "numero_nfse": filtros["numero_nfse"] or None,
                "cd_remessa": filtros["cd_remessa"] or None,
                "convenio": filtros["convenio"] or None,
                "processo_recebimento": (
                    filtros["processo_recebimento"] or None
                ),
                "situacao": filtros["situacao"] or None,
                "incluir_inativas": filtros["incluir_inativas"],
                "limit": page_size,
                "offset": offset,
            },
        )
        conciliacoes = payload.get("conciliacoes", [])
        total = int(payload.get("total", len(conciliacoes)))
        resumo = {
            "ativas": int(payload.get("total_ativas", 0)),
            "inativas": int(payload.get("total_inativas", 0)),
            "recebidas": int(payload.get("total_recebidas", 0)),
            "sem_recebimento": int(
                payload.get("total_sem_recebimento", 0)
            ),
        }
    except ApiError as exc:
        conciliacoes = []
        total = 0
        resumo = {
            "ativas": 0,
            "inativas": 0,
            "recebidas": 0,
            "sem_recebimento": 0,
        }
        messages.error(
            request,
            format_api_error(exc, "Consulta de conciliações"),
        )
    contas_por_id = {}
    if any(
        nota.get("recebimentos")
        for remessa in conciliacoes
        for nota in remessa.get("notas", [])
    ):
        try:
            contas_payload = get_cached_api_payload(
                "financeiro:contas-bancarias",
                CONTAS_BANCARIAS_PATH,
            )
            contas_por_id = {
                str(conta.get("id")): conta
                for conta in contas_payload.get("contas", [])
            }
        except ApiError:
            pass
    for remessa in conciliacoes:
        conciliacoes_ids = {
            nota.get("id") for nota in remessa.get("notas", [])
        }
        for evento in remessa.get("auditoria", []):
            evento["alteracoes"] = build_alteracoes_auditoria_conciliacao(
                evento
            )
            evento["numero_nfse"] = (
                evento.get("numero_nfse")
                or (evento.get("dados_novos") or {}).get("numero_nfse")
                or (evento.get("dados_anteriores") or {}).get(
                    "numero_nfse"
                )
                or "-"
            )
            evento["tipo_acao_label"] = {
                "criacao": "INCLUSÃO",
                "criacao_migrada": "INCLUSÃO",
                "edicao": "MODIFICAÇÃO",
                "inativacao": "EXCLUSÃO",
                "recebimento": "RECEBIMENTO",
                "edicao_recebimento": "MODIFICAÇÃO",
                "exclusao_recebimento": "EXCLUSÃO",
            }.get(evento.get("acao"), "EVENTO")
            evento["vinculo_anterior"] = (
                evento.get("conciliacao_origem_id") is not None
                and evento.get("conciliacao_origem_id")
                not in conciliacoes_ids
            )
        for nota in remessa.get("notas", []):
            for recebimento in nota.get("recebimentos", []):
                conta_bancaria_id = recebimento.get("conta_bancaria_id")
                recebimento["conta_bancaria_label"] = (
                    format_conta_bancaria_label(
                        contas_por_id.get(str(conta_bancaria_id)),
                        conta_bancaria_id,
                    )
                )
    base_query = {
        key: value
        for key, value in filtros.items()
        if value and not (key == "incluir_inativas" and value == "false")
    }
    total_pages = max(ceil(total / page_size), 1)
    if page > total_pages:
        return redirect(
            f"{request.path}?{urlencode({**base_query, 'page': total_pages})}"
        )
    pagination = {
        "page": page,
        "total_pages": total_pages,
        "page_options": [
            {"number": number, "selected": number == page}
            for number in range(1, total_pages + 1)
        ],
        "has_previous": page > 1,
        "has_next": page < total_pages,
        "previous_url": (
            f"?{urlencode({**base_query, 'page': page - 1})}"
            if page > 1
            else ""
        ),
        "next_url": (
            f"?{urlencode({**base_query, 'page': page + 1})}"
            if page < total_pages
            else ""
        ),
        "total": total,
        "query": base_query,
    }
    return render(
        request,
        "conciliacoes_financeiras.html",
        {
            "conciliacoes": conciliacoes,
            "filtros": filtros,
            "convenios": get_convenio_dropdown_options(
                filtros["convenio"]
            ),
            "resumo": resumo,
            "pagination": pagination,
        },
    )


@require_http_methods(["GET"])
def conciliacao_faturamento_remessas(request, nfse_row_hash):
    try:
        payload = api_get(
            f"{CONCILIACAO_FATURAMENTO_PATH}/notas/{nfse_row_hash}/remessas",
            params={"q": request.GET.get("q")},
        )
        return JsonResponse(payload)
    except ApiError as exc:
        return JsonResponse(
            {"detail": extract_api_error_message(exc)},
            status=exc.status_code or 502,
        )


@require_http_methods(["GET"])
def conciliacao_faturamento_notas(request, cd_remessa):
    try:
        payload = api_get(
            f"{CONCILIACAO_FATURAMENTO_PATH}/remessas/"
            f"{cd_remessa}/notas",
            params={"q": request.GET.get("q")},
        )
        return JsonResponse(payload)
    except ApiError as exc:
        return JsonResponse(
            {"detail": extract_api_error_message(exc)},
            status=exc.status_code or 502,
        )


@require_http_methods(["GET"])
def conciliacao_faturamento_lancamentos(request):
    try:
        payload = api_get(
            LANCAMENTOS_EXTRATO_PATH,
            params={
                "conta_bancaria_id": request.GET.get("conta_bancaria_id"),
                "data_recebimento": request.GET.get("data_recebimento"),
                "incluir_lancamento_id": request.GET.get(
                    "incluir_lancamento_id"
                ),
            },
        )
        return JsonResponse(payload)
    except ApiError as exc:
        return JsonResponse(
            {"detail": extract_api_error_message(exc)},
            status=exc.status_code or 502,
        )


@require_http_methods(["GET", "POST"])
def conciliacao(request):
    if request.method == "POST":
        try:
            divergencias = api_post("/conciliacao/executar", {})
            clear_filter_caches()
            messages.success(request, "Conciliacao executada.")
            return render(request, "conciliacao.html", {"divergencias": divergencias})
        except ApiError as exc:
            messages.error(request, format_api_error(exc, "Execucao da conciliacao"))
    try:
        divergencias = get_cached_api_payload(
            "conciliacao",
            "/conciliacao/divergencias",
        )
    except ApiError as exc:
        divergencias = []
        messages.error(request, format_api_error(exc, "Conciliacao"))
    return render(request, "conciliacao.html", {"divergencias": divergencias})
