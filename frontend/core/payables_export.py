"""Supplier summary and title detail for the filtered operation export."""
from datetime import date
from decimal import Decimal
from io import BytesIO
from textwrap import wrap

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

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


def _add_supplier_summary(workbook, suppliers):
    sheet = workbook.create_sheet("Resumo por fornecedor", 0)
    sheet.append(SUMMARY_HEADERS)
    for supplier in suppliers:
        sheet.append(_supplier_summary(supplier))
    for row in sheet.iter_rows(min_row=2, max_row=len(suppliers) + 1):
        for cell in row:
            if isinstance(cell.value, str):
                cell.data_type = "s"
            cell.alignment = Alignment(vertical="center", wrap_text=True)
        row[1].number_format = row[3].number_format = '"R$" #,##0.00'
        row[2].number_format = "dd/mm/yyyy"
        sheet.row_dimensions[row[0].row].height = 16 * max(
            2, len(wrap(str(row[0].value or ""), width=44))
        )
    sheet.freeze_panes = "B2"
    sheet.auto_filter.ref = sheet.dimensions
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1B7088")
        cell.alignment = Alignment(wrap_text=True, vertical="center")
    sheet.row_dimensions[1].height = 42
    for column, width in enumerate((48, 24, 25, 24, 24, 24, 22, 30), 1):
        sheet.column_dimensions[get_column_letter(column)].width = width
    workbook.active = 0


def build_payables_workbook(fornecedores):
    fornecedores = decorate_suppliers(fornecedores)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Contas vencidas"
    sheet.append(HEADERS)
    row_number = 1
    for priority, supplier in enumerate(fornecedores, 1):
        for title in supplier.get("titulos", []):
            context = {
                "priority": priority,
                "balance": _money(title.get("saldo_a_pagar")),
                "days": int(title.get("dias_vencidos") or 0),
            }
            sheet.append([value(supplier, title, context) for *_, value in COLUMNS])
            row_number += 1
            # Counted, not sheet.max_row/sheet[n]: both scan every cell.
            for column, (_, number_format, _, _) in enumerate(COLUMNS, 1):
                cell = sheet.cell(row=row_number, column=column)
                # API text must stay literal, including values beginning with '='.
                if isinstance(cell.value, str):
                    cell.data_type = "s"
                if number_format:
                    cell.number_format = number_format
    sheet.freeze_panes = "E2"
    sheet.auto_filter.ref = sheet.dimensions
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1B7088")
        cell.alignment = Alignment(wrap_text=True, vertical="center")
    sheet.row_dimensions[1].height = 42
    for index, (_, _, width, _) in enumerate(COLUMNS, 1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    _add_supplier_summary(workbook, fornecedores)
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()
