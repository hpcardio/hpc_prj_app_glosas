"""Contas a Receber · Fluxo do recebimento (remessa → protocolo → NFS → recebimento)."""

from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET

from .contas_receber_export import build_contas_receber_workbook
from .services import ApiError, api_get

FLUXO_PATH = "/app_glosas/financeiro/contas-a-receber/fluxo"
POR_PAGINA = 10
TIMEOUT = 60
EXPORT_TIMEOUT = 110
MESES = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")
ETAPAS_FUNIL = (
    ("protocolo", "Aguardando protocolo", "Remessas fechadas no MV que ainda não têm protocolo no convênio."),
    ("nfse", "Aguardando NFS", "Protocoladas no convênio, sem NFS vinculada."),
    ("recebimento", "Aguardando recebimento", "NFS emitida, sem lançamento de extrato vinculado."),
    ("concluido", "Recebidas", "Fluxo completo: o recebimento está no extrato."),
)
ORDENACOES = (
    ("parado", "Mais tempo parado"),
    ("competencia", "Competência mais recente"),
    ("valor", "Maior valor"),
)
ORIGENS = {
    "portal": "Portal do convênio (automático)",
    "manual": "Vinculado no Receita Certa",
    "planilha": "Histórico da planilha",
    "legado": "Conciliação Manual (legado)",
}


def _decimal(valor):
    try:
        return Decimal(str(valor if valor is not None else "0"))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _data(valor):
    if not valor:
        return None
    try:
        return date.fromisoformat(str(valor)[:10])
    except ValueError:
        return None


def competencia_label(valor):
    data = _data(valor)
    return f"{MESES[data.month - 1]}/{data.year}" if data else "—"


def faixa_dias(dias):
    if dias is None:
        return ""
    if dias <= 15:
        return "ok"
    if dias <= 45:
        return "atencao"
    return "critico"


def _enriquecer(item):
    item["competencia_label"] = competencia_label(item.get("competencia"))
    item["faixa_dias"] = faixa_dias(item.get("dias_na_etapa"))
    protocolo = item.get("protocolo") or None
    if protocolo:
        protocolo["origem_label"] = ORIGENS.get(protocolo.get("origem"), protocolo.get("origem") or "")
        cobrado, liberado = protocolo.get("valor_cobrado"), protocolo.get("valor_liberado")
        protocolo["glosa_portal"] = (
            _decimal(cobrado) - _decimal(liberado)
            if cobrado is not None and liberado is not None
            else None
        )
    nfse = item.get("nfse") or None
    if nfse:
        nfse["origem_label"] = ORIGENS.get(nfse.get("origem"), nfse.get("origem") or "")
    detalhes = {
        "remessa": f"{item.get('qt_contas', 0)} conta{'s' if item.get('qt_contas') != 1 else ''}",
        "protocolo": (
            f"Nº {protocolo['numero']}" if protocolo
            else f"{len(item['protocolo_candidatos'])} sugest{'ão' if len(item['protocolo_candidatos']) == 1 else 'ões'} a confirmar"
            if item.get("protocolo_candidatos") else "Sem protocolo"
        ),
        "nfse": f"NF {nfse['numero']}" if nfse else "Sem NFS",
        "recebimento": (
            "Recebido no extrato" if item.get("recebimentos") else "Sem recebimento"
        ),
    }
    for etapa in item.get("etapas") or []:
        etapa["detalhe"] = detalhes.get(etapa["chave"], "")
    return item


def _funil(resumo, filtros):
    etapas = {e.get("chave"): e for e in resumo.get("etapas") or []}
    total = sum(int(e.get("quantidade") or 0) for e in etapas.values()) or 0
    base = {k: v for k, v in filtros.items() if v and k not in {"etapa", "page"}}
    cards = []
    for chave, rotulo, ajuda in ETAPAS_FUNIL:
        dados = etapas.get(chave) or {}
        quantidade = int(dados.get("quantidade") or 0)
        ativo = filtros.get("etapa") == chave
        query = base if ativo else {**base, "etapa": chave}
        cards.append({
            "chave": chave,
            "rotulo": rotulo,
            "ajuda": ajuda,
            "quantidade": quantidade,
            "valor": dados.get("valor") or "0",
            "percentual": round(quantidade * 100 / total) if total else 0,
            "ativo": ativo,
            "url": f"?{urlencode(query)}" if query else "?",
        })
    return cards


@require_GET
def fluxo(request):
    filtros = {
        "convenio": (request.GET.get("convenio") or "").strip(),
        "competencia_de": (request.GET.get("competencia_de") or "").strip()[:7],
        "competencia_ate": (request.GET.get("competencia_ate") or "").strip()[:7],
        "q": (request.GET.get("q") or "").strip()[:60],
        "etapa": request.GET.get("etapa") if request.GET.get("etapa") in {e[0] for e in ETAPAS_FUNIL} else "",
        "a_confirmar": "1" if request.GET.get("a_confirmar") == "1" else "",
        "ordenar_por": request.GET.get("ordenar_por") if request.GET.get("ordenar_por") in {o[0] for o in ORDENACOES} else "",
    }
    try:
        page = max(int(request.GET.get("page") or 1), 1)
    except ValueError:
        page = 1
    params = {k: v for k, v in filtros.items() if v and k != "a_confirmar"}
    if filtros["convenio"] and not filtros["convenio"].isdigit():
        params.pop("convenio")
    if filtros["a_confirmar"]:
        params["a_confirmar"] = "true"
    if request.GET.get("export") == "xlsx":
        # Planilha com todas as remessas do filtro da tela (layout da DEMO).
        try:
            dados = api_get(FLUXO_PATH, {**params, "exportar": "true"}, timeout=EXPORT_TIMEOUT)
        except ApiError as exc:
            messages.error(request, f"Não foi possível exportar o fluxo do Contas a Receber: {exc}")
            base = {k: v for k, v in filtros.items() if v}
            return redirect(f"{request.path}?{urlencode(base)}" if base else request.path)
        response = HttpResponse(
            build_contas_receber_workbook(dados.get("itens") or [], timezone.localdate()),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = f'attachment; filename="fluxo-contas-a-receber-{timezone.localdate():%Y%m%d}.xlsx"'
        response["Cache-Control"] = "private, no-store"
        return response
    payload = {}
    try:
        payload = api_get(FLUXO_PATH, {**params, "page": page, "page_size": POR_PAGINA}, timeout=TIMEOUT)
    except ApiError as exc:
        messages.error(request, f"Não foi possível carregar o fluxo do Contas a Receber: {exc}")

    total = int(payload.get("total") or 0)
    total_pages = max(int(payload.get("total_pages") or 1), 1)
    page = min(page, total_pages)
    base_query = {k: v for k, v in filtros.items() if v}
    itens = [_enriquecer(item) for item in payload.get("itens") or []]
    pagination = {
        "page": page,
        "total_pages": total_pages,
        "page_options": [{"number": n, "selected": n == page} for n in range(1, total_pages + 1)],
        "has_previous": page > 1,
        "has_next": page < total_pages,
        "previous_url": f"?{urlencode({**base_query, 'page': page - 1})}" if page > 1 else "",
        "next_url": f"?{urlencode({**base_query, 'page': page + 1})}" if page < total_pages else "",
        "start": (page - 1) * POR_PAGINA + 1 if itens else 0,
        "end": (page - 1) * POR_PAGINA + len(itens),
        "total": total,
        "query": base_query,
    }
    resumo = payload.get("resumo") or {}
    sem_confirmar = {k: v for k, v in base_query.items() if k not in {"a_confirmar", "page"}}
    response = render(request, "contas_receber_fluxo.html", {
        "filtros": filtros,
        "funil": _funil(resumo, filtros),
        "resumo": resumo,
        "itens": itens,
        "pagination": pagination,
        "convenios": payload.get("convenios") or [],
        "ordenacoes": ORDENACOES,
        "valor_total_filtrado": payload.get("valor_total_filtrado") or "0",
        "a_confirmar_url": f"?{urlencode({**sem_confirmar, 'a_confirmar': '1'})}",
        "remover_a_confirmar_url": f"?{urlencode(sem_confirmar)}" if sem_confirmar else "?",
        "atualizado_em": payload.get("atualizado_em"),
        "export_url": f"?{urlencode({**{k: v for k, v in filtros.items() if v}, 'export': 'xlsx'})}",
    })
    response["Cache-Control"] = "private, no-store"
    return response
