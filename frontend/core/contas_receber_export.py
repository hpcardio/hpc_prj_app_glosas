"""Planilha do Fluxo do recebimento, no layout da aba BASE da planilha DEMO
("DEMO CONVENIOS CONSOLIDADO a partir de 2025"), coluna a coluna.

Regras de preenchimento (iguais às fórmulas da DEMO):
- Dias = (data do recebimento ou hoje) - data de entrega;
- GLOSA = valor total da remessa - valor bruto da NF;
- IMPOSTOS = valor bruto - valor líquido da NF;
- Status: 1-REALIZADO (recebida), VENCIDOS (previsão de recebimento
  vencida) ou A RECEBER;
- STATUS2: PAGO (paga no portal ou recebida no extrato), NF EMITIDA (NF sem recebimento) ou A RECEBER.
"""
from datetime import date
from decimal import Decimal, InvalidOperation
from io import BytesIO

import xlsxwriter

from .payables_export import DATE, MONEY

MESES = ("JANEIRO", "FEVEREIRO", "MARÇO", "ABRIL", "MAIO", "JUNHO", "JULHO",
         "AGOSTO", "SETEMBRO", "OUTUBRO", "NOVEMBRO", "DEZEMBRO")
MESES_ABREV = ("JAN", "FEV", "MAR", "ABR", "MAI", "JUN", "JUL", "AGO", "SET", "OUT", "NOV", "DEZ")
HEADER_STYLE = {"bold": True, "font_color": "#FFFFFF", "bg_color": "#1F4E78", "border": 1,
                "align": "center", "valign": "vcenter", "text_wrap": True}
NUMBER = "0"


def _data(valor):
    if not valor:
        return None
    try:
        return date.fromisoformat(str(valor)[:10])
    except ValueError:
        return None


def _dinheiro(valor):
    if valor in (None, ""):
        return None
    try:
        return Decimal(str(valor))
    except (InvalidOperation, ValueError):
        return None


def _numero(texto):
    texto = str(texto or "").strip()
    return int(texto) if texto.isdigit() and len(texto) <= 15 else (texto or None)


def _recebimento(item):
    datas = [_data(r.get("data")) for r in item.get("recebimentos") or []]
    return max((d for d in datas if d), default=None)


def _contexto(item, hoje):
    nf = item.get("nfse") or {}
    protocolo = item.get("protocolo") or {}
    # NF de grupo: só a parte que cabe à remessa (rateio feito pela API).
    rateio = item.get("nf_rateio") or {}
    bruto = _dinheiro(rateio.get("valor") if rateio else nf.get("valor"))
    liquido = _dinheiro(rateio.get("liquido") if rateio else nf.get("valor_liquido"))
    total = _dinheiro(item.get("valor")) or Decimal("0")
    entrega = _data(item.get("dt_entrega")) or _data(item.get("dt_fechamento"))
    previsao = _data(item.get("dt_prevista_pgto"))
    recebido_em = _recebimento(item)
    concluido = item.get("situacao_fluxo") in {"recebido", "recebido_sem_conciliacao"}
    if concluido:
        status, status2 = "1-REALIZADO", "PAGO"
    else:
        status = "VENCIDOS" if previsao and previsao < hoje else "A RECEBER"
        status2 = "NF EMITIDA" if nf else "A RECEBER"
    referencia = recebido_em or previsao
    return {
        "nf": nf, "protocolo": protocolo, "bruto": bruto, "liquido": liquido, "total": total,
        "entrega": entrega, "previsao": previsao, "recebido_em": recebido_em,
        "status": status, "status2": status2,
        "dias": ((recebido_em or hoje) - entrega).days if entrega else None,
        "glosa": (total - bruto) if bruto is not None else None,
        "impostos": (bruto - liquido) if bruto is not None and liquido is not None else None,
        "ano_ref": f"{MESES[referencia.month - 1]}-{referencia.year}" if referencia else None,
    }


def _competencia(valor):
    data = _data(valor)
    return f"{MESES_ABREV[data.month - 1]}.{str(data.year)[2:]}" if data else None


# (cabeçalho, formato, largura, valor) — mesma ordem da aba BASE da DEMO.
COLUMNS = (
    ("Convênio", None, 22, lambda i, c: i.get("convenio")),
    ("Remessa", NUMBER, 10, lambda i, c: i.get("cd_remessa")),
    ("Competência", None, 12, lambda i, c: _competencia(i.get("competencia"))),
    ("Data Entrega", DATE, 13, lambda i, c: c["entrega"]),
    ("Valor Total", MONEY, 16, lambda i, c: c["total"]),
    ("Dias", NUMBER, 8, lambda i, c: c["dias"]),
    ("Status", None, 13, lambda i, c: c["status"]),
    ("GLOSA", MONEY, 14, lambda i, c: c["glosa"]),
    ("VLR BRUTO", MONEY, 16, lambda i, c: c["bruto"]),
    ("IMPOSTOS", MONEY, 14, lambda i, c: c["impostos"]),
    ("VLR LIQ NF", MONEY, 16, lambda i, c: c["liquido"]),
    ("NF", NUMBER, 10, lambda i, c: _numero(c["nf"].get("numero"))),
    ("EMISSÃO", DATE, 13, lambda i, c: _data(c["nf"].get("data_emissao"))),
    ("RBTO/ENVIO", DATE, 13, lambda i, c: c["recebido_em"]),
    ("PREV. RECBTO.", DATE, 13, lambda i, c: c["previsao"]),
    ("ANO", None, 16, lambda i, c: c["ano_ref"]),
    ("PROCESSO", NUMBER, 16, lambda i, c: _numero(c["protocolo"].get("numero"))),
    ("PROCESSO TRUE", None, 10, lambda i, c: "SIM" if c["protocolo"] else "NÃO"),
    ("STATUS2", None, 12, lambda i, c: c["status2"]),
    ("Ano2", NUMBER, 8, lambda i, c: c["recebido_em"].year if c["recebido_em"] else None),
    ("mês", None, 12, lambda i, c: MESES[c["recebido_em"].month - 1] if c["recebido_em"] else None),
    ("ano prev.recb", NUMBER, 10, lambda i, c: c["previsao"].year if c["previsao"] else None),
    ("mês prev.rcbto", None, 12, lambda i, c: MESES[c["previsao"].month - 1] if c["previsao"] else None),
)
HEADERS = tuple(column[0] for column in COLUMNS)


def build_contas_receber_workbook(itens, hoje):
    output = BytesIO()
    workbook = xlsxwriter.Workbook(output, {"constant_memory": True})
    header = workbook.add_format(HEADER_STYLE)
    formats = {fmt: workbook.add_format({"num_format": fmt}) for fmt in {c[1] for c in COLUMNS} if fmt}
    sheet = workbook.add_worksheet("BASE")
    for column, (titulo, _fmt, largura, _valor) in enumerate(COLUMNS):
        sheet.set_column(column, column, largura)
        sheet.write(0, column, titulo, header)
    sheet.freeze_panes(1, 2)
    sheet.autofilter(0, 0, max(len(itens), 1), len(COLUMNS) - 1)
    for row, item in enumerate(itens, start=1):
        contexto = _contexto(item, hoje)
        for column, (_titulo, fmt, _largura, valor) in enumerate(COLUMNS):
            dado = valor(item, contexto)
            if dado is None or dado == "":
                continue
            if isinstance(dado, date):
                sheet.write_datetime(row, column, dado, formats[DATE])
            elif isinstance(dado, Decimal):
                sheet.write_number(row, column, float(dado), formats.get(fmt))
            elif isinstance(dado, int):
                sheet.write_number(row, column, dado, formats.get(fmt))
            else:
                sheet.write_string(row, column, str(dado))
    workbook.close()
    return output.getvalue()
