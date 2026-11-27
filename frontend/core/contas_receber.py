"""Contas a Receber · Fluxo do recebimento (remessa → protocolo → NFS → recebimento)."""

from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .contas_receber_export import build_contas_receber_workbook
from .services import ApiError, api_delete, api_get, api_put

CR_PATH = "/app_glosas/financeiro/contas-a-receber"
FLUXO_PATH = f"{CR_PATH}/fluxo"
POR_PAGINA = 10
TIMEOUT = 60
EXPORT_TIMEOUT = 110
MESES = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")
ETAPAS_FUNIL = (
    ("protocolo", "Aguardando protocolo", "Remessas fechadas no MV que ainda não têm protocolo no convênio."),
    ("nfs_autorizada", "NFS autorizadas",
     "O convênio pediu a NF (ENVIAR em Notas Fiscais – Pendentes no portal) e ela ainda não foi anexada."),
    ("nao_autorizada", "NFS não autorizadas", "Protocoladas, mas o convênio ainda não listou em Notas Fiscais."),
    ("aguardando_recebimento", "Aguardando recebimento",
     "NF entregue (Notas Fiscais – Pendentes sem ENVIAR) ou NFS vinculada, ainda sem pagamento."),
    ("recebido_sem_conciliacao", "Recebidas s/ conciliação bancária",
     "Pagas no portal do convênio, sem crédito do extrato vinculado."),
    ("recebido", "Recebidas", "Pagas no portal do convênio e com o crédito do extrato vinculado."),
)
SITUACOES_RECEBIDAS = {"recebido", "recebido_sem_conciliacao"}
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
    "nup_grupo": "NF do grupo (NUP do processo agrupador no portal)",
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


def valores_do_card(item):
    """Total -> glosas -> bruto -> impostos -> líquido (mesmas regras da planilha DEMO).

    Total: valor da remessa no MV ("Valor Total" da DEMO). Glosas: total -
    valor da NF; sem NF, a glosa do portal do convênio (cobrado - liberado).
    Bruto: total - glosas ("VLR BRUTO" da DEMO, a base da NF). Impostos:
    valor da NF - líquido do ISS (retenções). Líquido: bruto - impostos (o
    líquido da NF). Sem o dado, o campo fica vazio (None).
    """
    total = _decimal(item.get("valor"))
    bruto = total
    nfse = item.get("nfse") or {}
    protocolo = item.get("protocolo") or {}
    # Parte da NF que cabe à remessa (a API rateia as NFs de grupo, que
    # cobrem vários processos/remessas); sem rateio, a própria NF.
    rateio = item.get("nf_rateio") or {}
    fonte = rateio if rateio else nfse
    campo_liquido = "liquido" if rateio else "valor_liquido"
    valor_nf = _decimal(fonte["valor"]) if fonte.get("valor") is not None else None
    liquido = _decimal(fonte[campo_liquido]) if fonte.get(campo_liquido) is not None else None
    estimativa = item.get("estimativa") or None
    if valor_nf is not None:
        glosas, origem_glosa = bruto - valor_nf, "nf"
    elif estimativa and estimativa.get("origem_base") != "bruto":
        # Parte do liberado do processo que cabe à remessa.
        glosas, origem_glosa = bruto - _decimal(estimativa.get("base")), "portal"
    elif protocolo.get("glosa_portal") is not None:
        glosas, origem_glosa = protocolo["glosa_portal"], "portal"
    else:
        glosas, origem_glosa = None, ""
    retencoes = valor_nf - liquido if valor_nf is not None and liquido is not None else None
    estimado = retencoes is None and estimativa is not None
    if estimado:
        # Sem NF no ISS: a API estima pela alíquota retida nas NFs do convênio.
        retencoes = _decimal(estimativa.get("retencoes"))
        liquido = _decimal(estimativa.get("liquido"))
    if glosas is not None:
        valor_bruto = total - glosas
    elif estimado:
        # Estimativa sobre o valor total (sem glosa conhecida): bruto = total.
        valor_bruto = _decimal(estimativa.get("base")) if estimativa.get("base") is not None else total
    else:
        valor_bruto = None
    return {
        "total": total,
        "valor_bruto": valor_bruto,
        "bruto": bruto,
        "glosas": glosas,
        "origem_glosa": origem_glosa,
        "retencoes": retencoes,
        "liquido": liquido,
        "estimado": estimado,
        "explicacao_estimativa": (
            f"Estimado: {_pct(estimativa.get('aliquota'))} "
            f"{'da Configuração por Convênio' if estimativa.get('fonte_aliquota') == 'configuracao' else 'retido nas NFs do convênio'}, "
            f"sobre o valor {'bruto da remessa' if estimativa.get('origem_base') == 'bruto' else 'liberado no portal'}"
            f"{_detalhe_impostos(estimativa.get('retencoes_detalhe'))}. "
            "Muda para o valor real quando a NF for vinculada."
            if estimado else ""
        ),
        "explicacao_retencao": _explicacao_rateio(rateio),
    }


IMPOSTOS_ROTULO = (("iss", "ISS"), ("ir", "IR"), ("pis", "PIS"), ("cofins", "COFINS"), ("csll", "CSLL"))


def _pct(valor):
    return f"{_decimal(valor):.2f}".replace(".", ",") + "%"


def _moeda(valor):
    texto = f"{_decimal(valor):,.2f}"
    return "R$ " + texto.replace(",", "X").replace(".", ",").replace("X", ".")


def _detalhe_impostos(detalhe):
    partes = [f"{rotulo} {_moeda(detalhe[chave])}" for chave, rotulo in IMPOSTOS_ROTULO if (detalhe or {}).get(chave)]
    return f" ({' · '.join(partes)})" if partes else ""


def _explicacao_rateio(rateio):
    if not rateio or not rateio.get("compartilhada"):
        return "Valor da NF menos o líquido no ISS"
    fonte = (
        "percentuais da Configuração por Convênio"
        if rateio.get("fonte_aliquota") == "configuracao"
        else "alíquota da própria NF"
    )
    return (
        f"NF de grupo ({rateio.get('remessas_na_nf')} remessas): parte desta remessa × "
        f"{_pct(rateio.get('aliquota'))} ({fonte}){_detalhe_impostos(rateio.get('retencoes_detalhe'))}"
    )


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
    item["situacao_fluxo"] = item.get("situacao_fluxo") or "nao_autorizada"
    portal = item.get("nota_portal") or None
    if portal:
        portal["paga"] = portal.get("situacao") == "paga"
    item["valores"] = valores_do_card(item)
    item["nf_compartilhada"] = bool((item.get("nf_rateio") or {}).get("compartilhada"))
    detalhes = {
        "remessa": f"{item.get('qt_contas', 0)} conta{'s' if item.get('qt_contas') != 1 else ''}",
        "protocolo": (
            f"Nº {protocolo['numero']}" if protocolo
            else f"{len(item['protocolo_candidatos'])} sugest{'ão' if len(item['protocolo_candidatos']) == 1 else 'ões'} a confirmar"
            if item.get("protocolo_candidatos")
            else f"Planilha: Nº {item['protocolo_planilha']['numero']} a conferir"
            if item.get("protocolo_planilha") else "Sem protocolo"
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
            # Cards de recebimento: valor líquido (pedido de 27/11/2026).
            "liquido": dados.get("base_valor") == "liquido",
            "percentual": round(quantidade * 100 / total) if total else 0,
            "ativo": ativo,
            "url": f"?{urlencode(query)}" if query else "?",
        })
    return cards


FILTROS_DA_TELA = ("convenio", "competencia_de", "competencia_ate", "q", "etapa", "a_confirmar", "ordenar_por", "page")


def _erro_api(exc):
    from .views import extract_api_error_message  # noqa: PLC0415 (views é pesado)

    return extract_api_error_message(exc)


def _voltar(request, cd_remessa, extra=None):
    """Volta para a tela com os filtros e o card da remessa aberto."""
    query = {k: v for k, v in request.POST.items() if k in FILTROS_DA_TELA and v}
    query.update({"aberta": cd_remessa, **(extra or {})})
    return redirect(f"{request.path}?{urlencode(query)}#remessa-{cd_remessa}")


def _executar_vinculo(request, acao, cd_remessa):
    """Ações da entrega 2 (vínculos manuais). A API grava a auditoria."""
    base = f"{CR_PATH}/remessas/{cd_remessa}"
    protocolo_numero = " ".join((request.POST.get("protocolo_numero") or "").split()) or None
    if acao == "protocolo_salvar":
        numero = " ".join((request.POST.get("numero") or "").split())
        if not numero:
            messages.error(request, "Informe o número do protocolo.")
            return
        api_put(f"{base}/protocolo", {
            "numero": numero,
            "data_protocolo": request.POST.get("data_protocolo") or None,
            "confirmado_do_portal": request.POST.get("confirmado_do_portal") == "1",
        })
        messages.success(request, f"Protocolo {numero} vinculado à remessa {cd_remessa}.")
    elif acao == "protocolo_remover":
        api_delete(f"{base}/protocolo")
        messages.success(request, f"A remessa {cd_remessa} saiu do protocolo.")
    elif acao == "solicitacao_salvar":
        if not request.POST.get("data"):
            messages.error(request, "Informe a data da solicitação de nota.")
            return
        api_put(f"{base}/solicitacao-nota", {
            "data": request.POST["data"],
            "observacao": (request.POST.get("observacao") or "").strip()[:400] or None,
            "protocolo_numero": protocolo_numero,
        })
        messages.success(request, f"Solicitação de nota registrada na remessa {cd_remessa}.")
    elif acao == "solicitacao_remover":
        api_delete(f"{base}/solicitacao-nota")
        messages.success(request, "Solicitação de nota removida.")
    elif acao == "nfse_vincular":
        numero = (request.POST.get("numero_nfse") or "").strip()
        api_put(f"{base}/nfse", {
            "prestador_cnpj": "".join(ch for ch in request.POST.get("prestador_cnpj") or "" if ch.isdigit()),
            "numero_nfse": numero,
            "protocolo_numero": protocolo_numero,
        })
        messages.success(request, f"NFS {numero} vinculada à remessa {cd_remessa}.")
    elif acao == "nfse_remover":
        api_delete(f"{base}/nfse")
        messages.success(request, "NFS desvinculada da remessa.")
    else:
        messages.error(request, "Ação inválida.")


ACOES_VINCULO = {"protocolo_salvar", "protocolo_remover", "solicitacao_salvar",
                 "solicitacao_remover", "nfse_vincular", "nfse_remover"}


@require_http_methods(["GET", "POST"])
def fluxo(request):
    if request.method == "POST":
        acao = request.POST.get("acao")
        cd_remessa = request.POST.get("cd_remessa") or ""
        if acao not in ACOES_VINCULO or not cd_remessa.isdigit():
            messages.error(request, "Ação inválida.")
            return redirect(request.path)
        try:
            _executar_vinculo(request, acao, int(cd_remessa))
        except ApiError as exc:
            messages.error(request, f"Não foi possível salvar: {_erro_api(exc)}")
        return _voltar(request, cd_remessa)
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
            # Mesmos dados do card da tela (valores, situação, dias parados).
            build_contas_receber_workbook(
                [_enriquecer(item) for item in dados.get("itens") or []], timezone.localdate()
            ),
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
    aberta = request.GET.get("aberta") or ""
    # Pesquisa de NFS para vincular, dentro do card da remessa aberta.
    busca_nf = {"remessa": "", "q": "", "notas": None}
    nf_q = (request.GET.get("nf_q") or "").strip()[:60]
    if aberta.isdigit() and len(nf_q) >= 2:
        busca_nf = {"remessa": aberta, "q": nf_q, "notas": []}
        try:
            busca_nf["notas"] = api_get(f"{CR_PATH}/nfse", {"q": nf_q}, timeout=TIMEOUT).get("notas") or []
        except ApiError as exc:
            messages.error(request, f"Não foi possível pesquisar a NFS: {_erro_api(exc)}")
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
        "guias_carregando": bool(payload.get("guias_carregando")),
        "tem_estimativa": any((i.get("valores") or {}).get("estimado") for i in itens),
        "export_url": f"?{urlencode({**{k: v for k, v in filtros.items() if v}, 'export': 'xlsx'})}",
        "aberta": aberta,
        "busca_nf": busca_nf,
        "filtros_post": {k: v for k, v in {**filtros, "page": page}.items() if v},
    })
    response["Cache-Control"] = "private, no-store"
    return response
