from calendar import monthrange
from datetime import date, datetime
from math import ceil, log10
from hashlib import sha256
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl

from django.contrib import messages
from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST

from .services import (
    ApiError,
    api_authenticate,
    api_delete,
    api_get,
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
ACOMPANHAMENTO_GLOSAS_CACHE_KEY = "acompanhamento:registros-glosa"
CONTA_TISS_CACHE_KEY = "conta-atendimento:tiss"
DEFAULT_DASHBOARD_PERIOD_MONTHS = 12


def _safe_login_redirect(request):
    next_url = request.POST.get("next") or request.GET.get("next") or "/"
    if url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return "/"


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
                return redirect(next_url)
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
                api_post(
                    "/usuarios/",
                    {
                        "nome": (request.POST.get("nome") or "").strip(),
                        "email": (request.POST.get("email") or "").strip().lower(),
                        "senha": request.POST.get("senha") or "",
                        "perfil": request.POST.get("perfil") or "usuario",
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
    return render(request, "user_access_management.html", {"users": users})


@require_POST
def logout_view(request):
    request.session.flush()
    return redirect("login")


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


def format_api_error(exc: ApiError, endpoint_name: str) -> str:
    if exc.status_code == 401:
        return f"{endpoint_name}: sua sessão não é mais válida. Entre novamente."
    if exc.status_code == 404:
        return f"{endpoint_name}: endpoint ainda nao encontrado na API."
    return f"{endpoint_name}: {exc}"


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
        registro.get("valor_glosado")
        if registro.get("valor_glosado") not in (None, "")
        else registro.get("valor")
    )


def qtd_registro_recurso(registro):
    return as_float_or_zero(
        registro.get("qtd_glosada")
        if registro.get("qtd_glosada") not in (None, "")
        else 1
    )


def processo_card_key(registro):
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
        if registro.get("sn_glosado") != "true":
            continue
        if not registro.get("processo_recurso"):
            continue

        row = dict(registro)
        row["paciente_label"] = (
            row.get("nm_paciente")
            or f"Paciente {row.get('codigo_paciente') or '-'}"
        )
        row["idade_bucket"] = age_bucket(row)
        row["idade_bucket_label"] = ACOMPANHAMENTO_BUCKETS[row["idade_bucket"]]
        row["qtd_recurso"] = qtd_registro_recurso(row)
        row["valor_recurso"] = valor_registro_recurso(row)
        row["valor_recurso_formatado"] = format_brl_input(row["valor_recurso"])
        row["valor_recebido_formatado"] = format_brl_input(
            row.get("valor_recebido")
        )
        row["dt_recebimento_input"] = format_api_date_input(
            row.get("dt_recebimento")
        )
        row["dt_recebimento_formatada"] = format_api_date(
            row.get("dt_recebimento")
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
        all_received = all(item.get("dt_recebimento") for item in itens)
        oldest_item = min(itens, key=bucket_reference_date)
        bucket_key = "recebidas" if all_received else age_bucket(oldest_item)
        total_recurso = sum(item["valor_recurso"] for item in itens)
        valor_recebimento_maximo = min(
            (item["valor_recurso"] for item in itens),
            default=0,
        )
        total_recebido = sum(
            as_float_or_zero(item.get("valor_recebido")) for item in itens
        )
        total = total_recebido if bucket_key == "recebidas" else total_recurso
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
                "valor_recebimento_maximo": valor_recebimento_maximo,
                "valor_recebido_total": total_recebido,
                "valor_total_formatado": format_brl_input(total),
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


def _glosa_match_key(item):
    return (
        str(as_int_or_zero(item.get("cd_remessa"))),
        str(as_int_or_zero(item.get("cd_atendimento"))),
        str(as_int_or_zero(item.get("cd_reg") or item.get("conta"))),
        str(item.get("cd_pro_fat") or item.get("procedimento") or ""),
        str(item.get("nr_guia") or item.get("cd_guia") or item.get("guia") or ""),
    )


def _prepare_registro_glosa(registro):
    prepared = dict(registro)
    qtd_glosada = registro.get("qtd_glosada")
    prepared["data_glosa_input"] = format_api_date_input(registro.get("data_glosa"))
    prepared["dt_recurso_input"] = format_api_date_input(registro.get("dt_recurso"))
    prepared["dt_pagamento_input"] = format_api_date_input(registro.get("dt_pagamento"))
    prepared["valor_glosado_input"] = format_brl_input(registro.get("valor_glosado"))
    try:
        prepared["qtd_glosada_input"] = int(float(str(qtd_glosada).replace(",", ".")))
    except (TypeError, ValueError):
        prepared["qtd_glosada_input"] = ""
    return prepared


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
    for registro in registros:
        if not isinstance(registro, dict):
            continue
        key = _glosa_match_key(registro)
        if key not in registros_por_linha:
            registros_por_linha[key] = _prepare_registro_glosa(registro)

    for conta in contas:
        if not isinstance(conta, dict):
            continue
        registro = registros_por_linha.get(_glosa_match_key(conta))
        if registro:
            conta["registro_glosa"] = registro
            conta["registro_glosa_id"] = registro.get("id")
            conta["registro_glosa_status"] = registro.get("sn_glosado")


def build_registro_glosa_payload(data):
    return {
        "codigo_paciente": as_int_or_zero(data.get("cd_paciente")),
        "nm_paciente": data.get("nm_paciente") or None,
        "cd_remessa": as_int_or_zero(data.get("cd_remessa")),
        "cd_atendimento": as_int_or_zero(data.get("cd_atendimento")),
        "conta": as_int_or_zero(data.get("cd_reg")),
        "cd_prestador": as_int_or_zero(data.get("cd_prestador")),
        "cd_convenio": as_int_or_zero(data.get("cd_convenio")),
        "tp_atendimento": data.get("tp_atendimento") or "",
        "procedimento": str(data.get("cd_pro_fat") or ""),
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
        "motivo_glosa": data.get("motivo_glosa") or "",
        "descricao_glosa": data.get("descricao_glosa") or "",
        "qtd_registro": as_float_or_none(data.get("qt_lancamento")),
        "qtd_glosada": as_int_or_none(data.get("qtd_glosada")),
        "valor_glosado": as_float_or_none(data.get("valor_glosado")),
        "dt_recurso": data.get("dt_recurso") or None,
        "dt_pagamento": data.get("dt_pagamento") or None,
    }


def normalize_flag(value):
    return str(value or "").strip().lower()


def is_active_registro(registro):
    return normalize_flag(registro.get("sn_ativo")) in {"true", "sim", "s", "1"}


def is_recurso_registro(registro):
    return normalize_flag(registro.get("sn_glosado")) in {"true", "sim", "s", "1"}


def has_internal_treatment(registro):
    return bool(registro.get("processo_recurso") and registro.get("dt_recurso"))


def is_acato_registro(registro):
    return normalize_flag(registro.get("sn_glosado")) in {
        "not",
        "false",
        "não",
        "nao",
        "n",
        "0",
    }


def registro_valor_glosado(registro):
    return as_float_or_zero(
        registro.get("valor_glosado")
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
        current["valor_recuperado"] += as_float_or_zero(registro.get("valor_recebido"))
        if is_recurso_registro(registro):
            current["qtd_recursos"] += 1
            current["valor_recursado"] += registro_valor_glosado(registro)
            if as_float_or_zero(registro.get("valor_recebido")) > 0:
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
        current["valor_recuperado"] += as_float_or_zero(
            registro.get("valor_recebido")
        )
        current["qtd_recursada"] += 1
        if as_float_or_zero(registro.get("valor_recebido")) > 0:
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
    convenios_desabilitados = {
        as_int_or_zero(item.get("cd_convenio"))
        for item in prazos_convenio
        if item.get("habilitado") is False
    }
    rows = [
        registro
        for registro in registros
        if (
            is_active_registro(registro)
            and has_internal_treatment(registro)
            and as_int_or_zero(registro.get("cd_convenio"))
            not in convenios_desabilitados
        )
    ]
    aging_view = build_vw_indicadores_aging_glosas(rows, prazos_lookup)
    aging_indicators = build_aging_indicators(aging_view, period_start, period_end)
    recursos = [registro for registro in rows if is_recurso_registro(registro)]
    acatos = [registro for registro in rows if is_acato_registro(registro)]
    total_glosado = sum(registro_valor_glosado(registro) for registro in rows)
    total_recursos_valor = sum(
        registro_valor_glosado(registro) for registro in recursos
    )
    total_acatos_valor = sum(registro_valor_glosado(registro) for registro in acatos)
    total_recebido = sum(
        as_float_or_zero(registro.get("valor_recebido"))
        for registro in rows
        if registro.get("dt_recebimento")
    )
    recuperados = [registro for registro in rows if registro.get("dt_recebimento")]

    recursos_com_sucesso = [
        registro
        for registro in recursos
        if as_float_or_zero(registro.get("valor_recebido")) > 0
    ]
    glosas_sem_processo = [
        registro
        for registro in recursos
        if not registro.get("processo_recurso") or not registro.get("dt_recurso")
    ]
    total_glosas_sem_processo_valor = sum(
        registro_valor_glosado(registro) for registro in glosas_sem_processo
    )
    sem_recuperacao = [
        registro
        for registro in recursos
        if as_float_or_zero(registro.get("valor_recebido")) <= 0
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
        if is_recurso_registro(registro):
            current["recursos"] += 1
        elif is_acato_registro(registro):
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
        if tratativa == "recurso" and not is_recurso_registro(registro):
            continue
        if tratativa == "acato" and not is_acato_registro(registro):
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
            {"limit": 5000},
            force_refresh=force_refresh,
        )
        registros = payload.get("glosas", []) if isinstance(payload, dict) else []
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
                error_message = f"Falha ao desfazer registro: {exc}"
            else:
                error_message = f"Falha ao salvar {action_name}: {exc}"
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
        payload = {
            "dt_recebimento": request.POST.get("dt_recebimento") or None,
            "valor_recebido": as_float_or_zero(request.POST.get("valor_recebido")),
            "observacao_recebimento": (
                request.POST.get("observacao_recebimento") or None
            ),
        }
        if not registro_ids:
            messages.error(request, "Nenhum registro selecionado para recebimento.")
            return redirect("acompanhamento")

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
    try:
        convenios = get_convenio_filter_options()
    except ApiError as exc:
        messages.error(request, format_api_error(exc, "Consulta de convênios"))

    try:
        payload = get_cached_dashboard_payload(
            ACOMPANHAMENTO_GLOSAS_CACHE_KEY,
            settings.API_REGISTRO_GLOSA_PATH,
            {"limit": 5000},
        )
        registros = payload.get("glosas", []) if isinstance(payload, dict) else []
        registros = [
            registro
            for registro in registros
            if is_recurso_registro(registro) and has_internal_treatment(registro)
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
            if ("recebidas" if row.get("dt_recebimento") else row["idade_bucket"])
            == faixa
        ]
    else:
        rows_filtradas = rows

    cards_filtrados = build_acompanhamento_cards(rows_filtradas)
    resumo = {
        "processos": len(cards_filtrados),
        "registros": len(rows_filtradas),
        "valor_total": sum(row["valor_recurso"] for row in rows_filtradas),
        "recebidos": sum(
            1 for row in rows_filtradas if row.get("dt_recebimento")
        ),
        "sem_recuperacao": sum(
            1
            for row in rows_filtradas
            if as_float_or_zero(row.get("valor_recebido")) <= 0
        ),
    }

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
