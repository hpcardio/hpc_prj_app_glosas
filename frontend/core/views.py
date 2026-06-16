from datetime import date, datetime
from math import ceil
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl

from django.contrib import messages
from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from .services import ApiError, api_delete, api_get, api_post, api_put

PATIENTS_PER_PAGE = 10
TIPOS_ATENDIMENTO = ('Ambulatório', 'Externo', 'Urgência', 'Internação')


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
        return f"{endpoint_name}: API exige autenticacao. Configure API_BEARER_TOKEN no ambiente do frontend."
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
    try:
        return float(str(value).replace(",", "."))
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
    payload = api_get(settings.API_REGISTRO_GLOSA_PATH, params)
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
        "qtd_glosada": as_int_or_none(data.get("qtd_glosada")),
        "valor_glosado": as_float_or_none(data.get("valor_glosado")),
        "dt_recurso": data.get("dt_recurso") or None,
        "dt_pagamento": data.get("dt_pagamento") or None,
    }


def dashboard(request):
    try:
        indicadores = api_get("/dashboard/indicadores")
        divergencias = api_get("/conciliacao/divergencias")
    except ApiError as exc:
        indicadores = {}
        divergencias = []
        messages.error(request, format_api_error(exc, "Dashboard"))
    return render(request, "dashboard.html", {"indicadores": indicadores, "divergencias": divergencias[:6]})


@require_http_methods(["GET", "POST"])
def conta_atendimento(request):
    if request.method == "POST":
        registro_id = request.POST.get("registro_glosa_id")
        form_action = request.POST.get("form_action") or "salvar"
        try:
            if form_action == "desfazer" and registro_id:
                api_delete(f"{settings.API_REGISTRO_GLOSA_PATH}/{registro_id}")
                return modal_action_response(
                    request,
                    "Registro desfeito a partir da conta selecionada.",
                    "error",
                )

            payload = build_registro_glosa_payload(request.POST)
            is_acatar = payload.get("sn_glosado") == "not"
            if registro_id:
                api_payload = api_put(f"{settings.API_REGISTRO_GLOSA_PATH}/{registro_id}", payload)
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
    limit = PATIENTS_PER_PAGE
    offset = (page - 1) * limit
    api_filtros = {k: v for k, v in filtros.items() if v}
    api_filtros["limit"] = limit
    api_filtros["offset"] = offset
    consulta_indisponivel = False
    total_pacientes = 0
    tiss_motivos = []
    try:
        payload_tiss = api_get(settings.API_TISS_PATH, {"limit": 600})
        if isinstance(payload_tiss, dict):
            tiss_motivos = payload_tiss.get("itens", [])
    except ApiError as exc:
        messages.error(request, format_api_error(exc, "Consulta TISS"))

    try:
        if request.GET:
            payload = api_get(settings.API_CONTA_ATENDIMENTO_PATH, api_filtros)
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
    if request.GET and not total_pacientes:
        total_pacientes = len(grupos)

    base_query = {k: v for k, v in filtros.items() if v}
    total_pages = max(ceil(total_pacientes / PATIENTS_PER_PAGE), 1)
    if request.GET and page > total_pages:
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
        },
    )


def glosas(request):
    try:
        registros = api_get("/glosas", request.GET.dict())
    except ApiError as exc:
        registros = []
        messages.error(request, format_api_error(exc, "Glosas"))
    return render(request, "glosas.html", {"glosas": registros})


@require_http_methods(["GET", "POST"])
def remessas(request):
    if request.method == "POST":
        try:
            api_post("/remessas", request.POST.dict())
            messages.success(request, "Remessa enviada para cadastro.")
            return redirect("remessas")
        except ApiError as exc:
            messages.error(request, format_api_error(exc, "Cadastro de remessa"))
    try:
        registros = api_get("/remessas")
    except ApiError as exc:
        registros = []
        messages.error(request, format_api_error(exc, "Remessas"))
    return render(request, "remessas.html", {"remessas": registros})


@require_http_methods(["GET", "POST"])
def recursos(request):
    if request.method == "POST":
        try:
            api_post("/recursos", request.POST.dict())
            messages.success(request, "Recurso enviado para cadastro.")
            return redirect("recursos")
        except ApiError as exc:
            messages.error(request, format_api_error(exc, "Cadastro de recurso"))
    try:
        registros = api_get("/recursos")
    except ApiError as exc:
        registros = []
        messages.error(request, format_api_error(exc, "Recursos"))
    return render(request, "recursos.html", {"recursos": registros})


@require_http_methods(["GET", "POST"])
def recebimentos(request):
    if request.method == "POST":
        try:
            api_post("/recebimentos", request.POST.dict())
            messages.success(request, "Recebimento enviado para cadastro.")
            return redirect("recebimentos")
        except ApiError as exc:
            messages.error(request, format_api_error(exc, "Cadastro de recebimento"))
    try:
        registros = api_get("/recebimentos")
    except ApiError as exc:
        registros = []
        messages.error(request, format_api_error(exc, "Recebimentos"))
    return render(request, "recebimentos.html", {"recebimentos": registros})


@require_http_methods(["GET", "POST"])
def conciliacao(request):
    if request.method == "POST":
        try:
            divergencias = api_post("/conciliacao/executar", {})
            messages.success(request, "Conciliacao executada.")
            return render(request, "conciliacao.html", {"divergencias": divergencias})
        except ApiError as exc:
            messages.error(request, format_api_error(exc, "Execucao da conciliacao"))
    try:
        divergencias = api_get("/conciliacao/divergencias")
    except ApiError as exc:
        divergencias = []
        messages.error(request, format_api_error(exc, "Conciliacao"))
    return render(request, "conciliacao.html", {"divergencias": divergencias})
