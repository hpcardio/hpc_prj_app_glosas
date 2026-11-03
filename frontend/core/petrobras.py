"""Same-origin bridge; API enforces permissions and all business rules."""

import json
import re
from decimal import Decimal, InvalidOperation
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_http_methods
from .services import ApiError, api_get, api_request

PATHS = {
    "GET": r"(status|glosas|protocolos|execucoes|glosas/[1-9][0-9]*/(anexos|historico)|glosas/[1-9][0-9]*/anexos/[1-9][0-9]*)",
    "POST": r"(protocolos|competencias/sincronizar|glosas/cruzar|protocolos/[1-9][0-9]*/sincronizar|glosas/[1-9][0-9]*/(cruzar|anexos|enviar|consultar-recurso))",
    "PUT": r"glosas/[1-9][0-9]*/(rascunho|acato)",
    "DELETE": r"glosas/[1-9][0-9]*/acato",
}


@require_http_methods(["GET", "POST", "PUT", "DELETE"])
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
    kwargs = {"params": {
        key: value for key, value in request.GET.items() if value.strip()
    }} if request.method == "GET" else {}
    if request.method in ("POST", "PUT"):
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


# Follow-Up integration: payer glosas rendered as standard cards. Petrobras is
# only the source of these records; the cards keep the common layout and the
# integration enables the payer appeal fields on each item.
FOLLOW_UP_PATH = "/app_glosas/petrobras/demonstrativos"
CONVENIO_PADRAO = "SAÚDE PETROBRAS"
NAO_INFORMADO = "Não informado"
FILTROS_API = {
    "numero_protocolo": "protocolo",
    "nr_guia": "guia",
    "numero_lote": "lote",
    "paciente": "paciente",
    "cd_atendimento": "cd_atendimento",
    "cd_remessa": "cd_remessa",
}
# The payer return has no internal process, appeal process or attendance type.
FILTROS_SEM_EQUIVALENTE = (
    "processo_original",
    "processo_recurso",
    "tipo_atendimento",
)


# Glosa informed only per guide in the payment demonstrativo (no items).
GLOSA_POR_GUIA = "guia_pagamento"


def follow_up_aplicavel(filtros):
    if not settings.PETROBRAS_ENABLED:
        return False
    convenio = (filtros.get("convenio") or "").upper()
    if convenio and "PETROBRAS" not in convenio:
        return False
    if any(filtros.get(key) for key in FILTROS_SEM_EQUIVALENTE):
        return False
    return all(
        str(filtros.get(key) or "").isdigit() or not filtros.get(key)
        for key in ("cd_atendimento", "cd_remessa")
    )


def follow_up_pagina(filtros, offset, limit):
    params = {
        api_key: filtros[key]
        for key, api_key in FILTROS_API.items()
        if filtros.get(key)
    }
    # api_get drops falsy values; zero must still reach the API.
    params.update({"offset": str(offset), "limit": str(limit)})
    return api_get(FOLLOW_UP_PATH, params=params, timeout=60)


def _decimal(value):
    try:
        return Decimal(str(value)) if value not in (None, "") else None
    except (InvalidOperation, ValueError):
        return None


def _soma(valores):
    valores = list(valores)
    if any(v is None for v in valores):
        return None
    return sum(valores, Decimal(0))


def _sem_zeros(guia):
    guia = str(guia or "").strip()
    return (guia.lstrip("0") or "0") if guia else ""


def _tratado(row):
    return row.get("recurso_estado") not in (None, "", "rascunho")


def _valor_tratado(row, glosa):
    """API value covers sent appeals and internal acato."""
    valor = _decimal(row.get("valor_tratado"))
    if valor is not None:
        return valor
    return glosa if _tratado(row) else Decimal(0)


def _item(row):
    fonte = row.get("fonte") or {}
    oracle = row.get("oracle") or {}
    glosa = _decimal(row.get("valor_glosa")) or Decimal(0)
    tratado = _valor_tratado(row, glosa)
    motivos = [m.get("codigo") for m in fonte.get("motivos") or []]
    return {
        "demonstrativo_id_registro": f"petrobras-{row['id']}",
        "cd_paciente": oracle.get("cd_paciente"),
        "nm_paciente": oracle.get("nm_paciente"),
        "cd_remessa": oracle.get("cd_remessa"),
        "cd_atendimento": oracle.get("cd_atendimento"),
        "cd_reg": oracle.get("cd_reg"),
        "cd_lancamento": oracle.get("cd_lancamento"),
        "nm_convenio": oracle.get("nm_convenio") or CONVENIO_PADRAO,
        "tp_atendimento": oracle.get("tp_atendimento"),
        "cd_pro_fat": oracle.get("cd_pro_fat") or fonte.get("tuss"),
        "cd_tuss": None,
        "cd_gru_fat": oracle.get("cd_gru_fat"),
        "ds_gru_fat": oracle.get("ds_gru_fat"),
        "descricao": oracle.get("descricao") or fonte.get("descricao"),
        "nr_guia": _sem_zeros(row.get("guia_prestador")),
        # The payer senha is the MV NR_GUIA used to link with Oracle; a
        # reissue without it uses the senha of the same guide (senha_guia).
        "senha": _sem_zeros(fonte.get("senha") or row.get("senha_guia")),
        "numero_protocolo": fonte.get("protocolo"),
        "numero_lote": fonte.get("lote"),
        "dt_atendimento": oracle.get("dt_atendimento"),
        "dt_alta": oracle.get("dt_alta"),
        "dt_lancamento": oracle.get("data_realizacao"),
        "qt_lancamento": _decimal(fonte.get("quantidade")),
        # Guide without items: the MV account value stands for the billed one.
        "vl_total_conta": _decimal(
            fonte.get("valor_informado")
            if fonte.get("escopo") != GLOSA_POR_GUIA
            else oracle.get("valor_mv")
        ),
        "valor_processado": _decimal(fonte.get("valor_processado")),
        "valor_glosa": glosa,
        "valor_liberado": _decimal(fonte.get("valor_liberado")),
        "valor_total_tratado": tratado,
        "valor_pendente": glosa - tratado,
        "motivo_glosa_codigo": ", ".join(filter(None, motivos)),
        "motivo_glosa_descricao": "",
        "tratativa_disponivel": False,
        "registro_glosa": None,
        # Guide without items: MV account items as reference only, since
        # the payer does not say which of them was glosed.
        "itens_mv": [
            {
                "codigo": i.get("cd_tuss") or i.get("cd_pro_fat"),
                "descricao": i.get("descricao"),
                "ds_gru_fat": i.get("ds_gru_fat"),
                "quantidade": _decimal(i.get("quantidade")),
                "valor": _decimal(i.get("valor")),
            }
            for i in oracle.get("itens") or []
        ]
        if fonte.get("escopo") == GLOSA_POR_GUIA
        else [],
        "petrobras": row,
    }


def follow_up_cards(data):
    """Demonstrativo > protocolo > beneficiário/guia > itens, as API cards."""
    por_demonstrativo = {}
    for row in data.get("items") or []:
        fonte = row.get("fonte") or {}
        por_demonstrativo.setdefault(row.get("demonstrativo"), {}).setdefault(
            fonte.get("protocolo"), []
        ).append(row)
    cards = []
    for demonstrativo in data.get("demonstrativos") or []:
        for protocolo, rows in (por_demonstrativo.get(demonstrativo) or {}).items():
            fontes = [r.get("fonte") or {} for r in rows]
            itens = [_item(r) for r in rows]
            remessas = {
                i["cd_remessa"] for i in itens if i["cd_remessa"] is not None
            }
            pacientes = {}
            for row, item in zip(rows, itens):
                fonte = row.get("fonte") or {}
                chave = (
                    fonte.get("carteira") or "",
                    item["senha"] or item["nr_guia"],
                )
                paciente = pacientes.setdefault(chave, {
                    "codigo_paciente": item["cd_paciente"] or 0,
                    "nm_paciente": item["nm_paciente"] or NAO_INFORMADO,
                    "itens": [],
                })
                paciente["itens"].append(item)
            glosado = sum((i["valor_glosa"] for i in itens), Decimal(0))
            tratado = sum((i["valor_total_tratado"] for i in itens), Decimal(0))
            convenios = {i["nm_convenio"] for i in itens}
            cards.append({
                "origem": "petrobras",
                "conciliacao_remessa_id": None,
                "cd_remessa": remessas.pop() if len(remessas) == 1 else None,
                "numero_protocolo": protocolo,
                "convenio": convenios.pop() if len(convenios) == 1 else CONVENIO_PADRAO,
                "data_competencia": None,
                "data_entrega": None,
                "numero_nfse": "",
                "valor_remessa": None,
                "valor_itens": _soma(i["vl_total_conta"] for i in itens),
                "valor_glosado": glosado,
                "valor_glosa_pendente": glosado - tratado,
                "valor_total_tratado": tratado,
                "possui_recurso": False,
                "processo": {
                    "numero_processo": demonstrativo,
                    "rotulo": "DEMONSTRATIVO",
                    "origem": "petrobras",
                    "data_abertura": max(
                        (f.get("data_emissao") or "" for f in fontes),
                        default="",
                    ) or None,
                    "status_processo": (
                        "Retorno parcial"
                        if any(
                            f.get("cobertura") == "parcial_nao_confirmada"
                            for f in fontes
                        )
                        else "Glosa por guia"
                        if any(f.get("escopo") == GLOSA_POR_GUIA for f in fontes)
                        else None
                    ),
                },
                "fiscal": {},
                "pacientes": list(pacientes.values()),
            })
    return cards


def follow_up_campos_extras(cards):
    """Payer identifiers shown on the beneficiary header of payer cards."""
    for card in cards:
        if card.get("origem") != "petrobras":
            continue
        card["detalhes_carregados"] = True
        for atendimento in card.get("atendimentos_paciente") or []:
            grupos = atendimento.get("grupos_procedimento") or [{}]
            item = (grupos[0].get("itens") or [{}])[0]
            fonte = (item.get("petrobras") or {}).get("fonte") or {}
            oracle = (item.get("petrobras") or {}).get("oracle") or {}
            # Guide = senha, or numeroGuiaPrestador when the payer sent none.
            # The payment demonstrativo has no wallet: MV account fills it.
            atendimento["carteira"] = (
                fonte.get("carteira") or oracle.get("nr_carteira") or NAO_INFORMADO
            )
            atendimento["campos_extras"] = [
                ("GUIA", item.get("senha") or item.get("nr_guia") or NAO_INFORMADO),
            ]
            for chave in ("cd_atendimento", "tp_atendimento"):
                atendimento[chave] = atendimento.get(chave) or NAO_INFORMADO
            for chave in ("dt_atendimento_formatada", "dt_alta_formatada"):
                if atendimento.get(chave) in (None, "", "-"):
                    atendimento[chave] = NAO_INFORMADO
            for grupo in grupos:
                for item in grupo.get("itens") or []:
                    item["petrobras_json"] = json.dumps(
                        item["petrobras"], default=str, ensure_ascii=False
                    )
    return cards
