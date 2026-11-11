"""Supplier summary and title detail for the filtered operation export."""
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO
from textwrap import wrap

import xlsxwriter

from .payables_priority import AGES, decorate_suppliers


MONEY, DATE = '"R$" #,##0.00', "dd/mm/yyyy"


def _status(balance, days):
    return "Pago" if balance <= 0 else "Em atraso" if days > 0 else "A vencer"


def _age_label(days):
    limits = {5: 120, 4: 90, 3: 60, 2: 30, 1: 0}
    return next(
        (label for level, label in AGES if days > limits[level]), "Sem atraso"
    )


# (header, cell format, width, value from (supplier, title, row context)).
# The first twelve follow MV's "Contas a Pagar" spreadsheet, in its order and
# with its names; Receita Certa's own columns follow them.
COLUMNS = (
    ("Dt. Lançamento", DATE, 14, lambda s, t, c: _date(t.get("data_lancamento"))),
    ("Dt. Emissão", DATE, 14, lambda s, t, c: _date(t.get("data_emissao"))),
    ("Dt. Vencimento", DATE, 14, lambda s, t, c: _date(t.get("data_vencimento"))),
    ("Fornecedor", None, 36, lambda s, t, c: s.get("nome_fornecedor") or ""),
    ("Descrição da Conta", None, 60, lambda s, t, c: t.get("descricao_conta") or ""),
    ("Observação", None, 60, lambda s, t, c: t.get("observacao") or ""),
    ("Valor Título", MONEY, 18, lambda s, t, c: _money(t.get("valor_total"))),
    ("Desconto", MONEY, 14, lambda s, t, c: _optional_money(t.get("valor_desconto"))),
    ("Acréscimo", MONEY, 14, lambda s, t, c: _optional_money(t.get("valor_acrescimo"))),
    ("Nr. Documento", None, 18, lambda s, t, c: str(t.get("numero_documento") or "")),
    # Latest MV payment that was not reversed.
    ("Data do Pagamento", DATE, 14, lambda s, t, c: _date(t.get("data_pagamento_mv"))),
    # Paid in MV, as in its spreadsheet; manual payments only change
    # "Saldo a pagar".
    ("Valor Pago", MONEY, 18, lambda s, t, c: _money(t.get("valor_honrado_oracle"))),
    ("Prioridade", None, 12, lambda s, t, c: c["priority"]),
    ("Código do fornecedor", None, 18, lambda s, t, c: (
        "Sem cadastro no MV" if s.get("sem_cadastro")
        else str(s.get("codigo_fornecedor") or ""))),
    ("Criticidade", None, 18, lambda s, t, c: (
        "Sem classificação" if s.get("criticidade_pendente")
        else "Crítico" if s.get("critico") else "Não crítico")),
    ("Total em atraso do fornecedor", MONEY, 22,
     lambda s, t, c: _money(s.get("valor_total_vencido"))),
    ("Maior atraso do fornecedor (dias)", None, 22,
     lambda s, t, c: int(s.get("dias_atraso") or 0)),
    ("Pagar agora do fornecedor", MONEY, 22,
     lambda s, t, c: _money(s.get("pagamento_imediato"))),
    ("Saldo a negociar do fornecedor", MONEY, 22,
     lambda s, t, c: _money(s.get("saldo_negociar"))),
    ("Título", None, 14, lambda s, t, c: str(t.get("codigo_parcela") or "")),
    ("Parcela", None, 10, lambda s, t, c: str(t.get("numero_parcela") or "")),
    ("Dias em atraso", None, 14, lambda s, t, c: c["days"]),
    ("Saldo a pagar", MONEY, 18, lambda s, t, c: c["balance"]),
    ("Status", None, 14, lambda s, t, c: _status(c["balance"], c["days"])),
    ("Nível de criticidade", None, 14, lambda s, t, c: s.get("nivel_criticidade")),
    ("Classificação de criticidade", None, 22, lambda s, t, c: s["criticidade_label"]),
    ("Faixa de atraso do título", None, 22, lambda s, t, c: _age_label(c["days"])),
    ("Classificação da conta", None, 26,
     lambda s, t, c: t.get("classificacao") or "Sem classificação"),
)
HEADERS = tuple(column[0] for column in COLUMNS)


SUMMARY_HEADERS = (
    "Nome Fornecedor", "Valor Total Vencido", "Vencimento da última NF",
    "Valor da última NF", "Criticidade", "Caducidade", "Quadrante",
    "Classificação da conta",
)


def _money(value):
    return Decimal(str(value or 0))


def _date(value):
    return date.fromisoformat(str(value)[:10]) if value else None


def _optional_money(value):
    return None if value in (None, "") else _money(value)


def _latest_invoice(supplier):
    """Latest due date among open titles in the applied filters."""
    titles = [
        title for title in supplier.get("titulos", [])
        if title.get("data_vencimento") and _money(title.get("saldo_a_pagar")) > 0
    ]
    return max(titles, key=lambda title: (
        _date(title["data_vencimento"]),
        _date(title.get("data_emissao")) or date.min,
        int(title.get("codigo_contas_pagar") or 0),
        int(title.get("codigo_parcela") or 0),
    ), default=None)


def _account_classes(supplier):
    """MV account classifications of the supplier's exported titles."""
    names = sorted({
        title.get("classificacao") or "Sem classificação"
        for title in supplier.get("titulos", [])
    })
    return ", ".join(names) or "Sem classificação"


def _supplier_summary(supplier):
    title = _latest_invoice(supplier)
    level = supplier.get("nivel_criticidade")
    classification = (
        f"{level} - {supplier['criticidade_label']}"
        if level else supplier["criticidade_label"]
    )
    age_label, quadrant = "Não informado", "Não informado"
    if title:
        days = int(title.get("dias_vencidos") or 0)
        age = next((n for threshold, n in (
            (120, 5), (90, 4), (60, 3), (30, 2), (0, 1)
        ) if days > threshold), 0)
        age_label = f"{age} - {dict(AGES)[age]}" if age else "Sem atraso"
        quadrant = (
            f"Q{(5 - level) * 5 + (6 - age)}" if level and age
            else "Fora da matriz" if not age else "Classificação pendente"
        )
    invoice_value = title.get("valor_nota_fiscal") if title else None
    return [
        supplier.get("nome_fornecedor") or "",
        _money(supplier.get("valor_total_vencido")),
        _date(title["data_vencimento"]) if title else None,
        _money(invoice_value) if invoice_value is not None else None,
        classification, age_label, quadrant, _account_classes(supplier),
    ]


HEADER_STYLE = {
    "bold": True, "font_color": "#FFFFFF", "bg_color": "#1B7088",
    "text_wrap": True, "valign": "vcenter",
}


# Written with XlsxWriter in constant-memory mode: the full Contas Vencidas
# export has ~75 thousand rows and openpyxl took ~50 s in production, past
# the 60 s proxy limit. Rows are streamed in order; strings are always
# written as text, so API values beginning with '=' stay literal.
class _Formats:
    def __init__(self, workbook):
        self.workbook, self.cache = workbook, {}

    def get(self, number_format=None, wrap=False):
        key = (number_format, wrap)
        if key not in self.cache:
            props = {"num_format": number_format} if number_format else {}
            if wrap:
                props.update(text_wrap=True, valign="vcenter")
            self.cache[key] = self.workbook.add_format(props) if props else None
        return self.cache[key]


def _write(sheet, row, column, value, cell_format=None):
    if value is None or value == "":
        # Blank, as openpyxl wrote empty text.
        if cell_format is not None:
            sheet.write_blank(row, column, None, cell_format)
    elif isinstance(value, str):
        sheet.write_string(row, column, value, cell_format)
    elif isinstance(value, (date, datetime)):
        sheet.write_datetime(row, column, value, cell_format)
    elif isinstance(value, bool):
        sheet.write_boolean(row, column, value, cell_format)
    else:
        sheet.write_number(row, column, float(value), cell_format)


def _write_header(sheet, header_format, headers, widths):
    sheet.set_row(0, 42)
    for column, (header, width) in enumerate(zip(headers, widths)):
        sheet.set_column(column, column, width)
        sheet.write_string(0, column, header, header_format)


def _add_supplier_summary(workbook, formats, header_format, suppliers):
    sheet = workbook.add_worksheet("Resumo por fornecedor")
    _write_header(sheet, header_format, SUMMARY_HEADERS,
                  (48, 24, 25, 24, 24, 24, 22, 30))
    sheet.freeze_panes(1, 1)
    sheet.autofilter(0, 0, len(suppliers), len(SUMMARY_HEADERS) - 1)
    cell_formats = [
        formats.get({1: MONEY, 2: DATE, 3: MONEY}.get(column), wrap=True)
        for column in range(len(SUMMARY_HEADERS))
    ]
    for row, supplier in enumerate(suppliers, 1):
        values = _supplier_summary(supplier)
        sheet.set_row(row, 16 * max(2, len(wrap(str(values[0] or ""), width=44))))
        for column, value in enumerate(values):
            _write(sheet, row, column, value, cell_formats[column])
    sheet.activate()


def build_payables_workbook(fornecedores):
    fornecedores = decorate_suppliers(fornecedores)
    output = BytesIO()
    workbook = xlsxwriter.Workbook(output, {"constant_memory": True})
    formats = _Formats(workbook)
    header_format = workbook.add_format(HEADER_STYLE)
    _add_supplier_summary(workbook, formats, header_format, fornecedores)
    sheet = workbook.add_worksheet("Contas vencidas")
    _write_header(sheet, header_format, HEADERS, [width for _, _, width, _ in COLUMNS])
    total_rows = sum(len(item.get("titulos", [])) for item in fornecedores)
    sheet.freeze_panes(1, 4)
    sheet.autofilter(0, 0, total_rows, len(COLUMNS) - 1)
    columns = [(formats.get(number_format), value) for _, number_format, _, value in COLUMNS]
    row = 0
    for priority, supplier in enumerate(fornecedores, 1):
        for title in supplier.get("titulos", []):
            row += 1
            context = {
                "priority": priority,
                "balance": _money(title.get("saldo_a_pagar")),
                "days": int(title.get("dias_vencidos") or 0),
            }
            for column, (cell_format, value) in enumerate(columns):
                _write(sheet, row, column, value(supplier, title, context), cell_format)
    workbook.close()
    return output.getvalue()
