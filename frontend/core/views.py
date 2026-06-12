from datetime import date, datetime
from math import ceil
from urllib.parse import urlencode

from django.contrib import messages
from django.conf import settings
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from .services import ApiError, api_get, api_post

PATIENTS_PER_PAGE = 10


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


def format_api_error(exc: ApiError, endpoint_name: str) -> str:
    if exc.status_code == 401:
        return f"{endpoint_name}: API exige autenticacao. Configure API_BEARER_TOKEN no ambiente do frontend."
    if exc.status_code == 404:
        return f"{endpoint_name}: endpoint ainda nao encontrado na API."
    return f"{endpoint_name}: {exc}"


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
                atendimentos.append({
                    "cd_atendimento": atd,
                    "itens": itens,
                    "total": atd_total,
                    "num_lancamentos": len(itens),
                    "convenios": sorted(atd_convenios),
                    "procedimentos": sorted(atd_procedimentos),
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
        payload = request.POST.dict()
        payload["item_glosado"] = payload.get("descricao", "")
        payload["valor_apresentado"] = payload.get("vl_total_conta") or "0"
        payload["valor_glosado"] = payload.get("valor_glosado") or "0"
        payload["motivo_glosa"] = payload.get("motivo_glosa") or "Nao informado"
        try:
            api_post("/glosas/from-conta-atendimento", payload)
            messages.success(request, "Glosa registrada a partir da conta selecionada.")
            return redirect("glosas")
        except ApiError as exc:
            messages.error(request, f"Falha ao registrar glosa: {exc}")

    filtros = request.GET.dict()
    filtros.pop("limit", None)
    filtros.pop("offset", None)
    page = as_positive_int(filtros.pop("page", None), 1)
    api_filtros = {k: v for k, v in filtros.items() if v}
    try:
        contas = as_list(api_get(settings.API_CONTA_ATENDIMENTO_PATH, api_filtros)) if request.GET else []
    except ApiError as exc:
        contas = []
        messages.error(request, format_api_error(exc, "Consulta de conta/atendimento"))
    for conta in contas:
        if isinstance(conta, dict):
            conta["dt_lancamento_formatada"] = format_api_date(conta.get("dt_lancamento"))
    grupos = _group_contas(contas)
    total_pages = max(ceil(len(grupos) / PATIENTS_PER_PAGE), 1)
    page = min(page, total_pages)
    page_start = (page - 1) * PATIENTS_PER_PAGE
    page_end = page_start + PATIENTS_PER_PAGE
    grupos_pagina = grupos[page_start:page_end]
    base_query = {k: v for k, v in filtros.items() if v}
    page_options = [{"number": number, "selected": number == page} for number in range(1, total_pages + 1)]
    pagination = {
        "page": page,
        "total_pages": total_pages,
        "page_options": page_options,
        "has_previous": page > 1,
        "has_next": page < total_pages,
        "previous_url": f"?{urlencode({**base_query, 'page': page - 1})}" if page > 1 else "",
        "next_url": f"?{urlencode({**base_query, 'page': page + 1})}" if page < total_pages else "",
        "start": page_start + 1 if grupos else 0,
        "end": min(page_end, len(grupos)),
        "total": len(grupos),
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
        {"grupos": grupos_pagina, "filtros": filtros, "resumo": resumo, "pagination": pagination},
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
