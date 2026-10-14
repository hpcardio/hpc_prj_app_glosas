"""Excel export of the same supplier/title fields displayed in operation."""
from datetime import date
from decimal import Decimal
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


HEADERS = (
    "Prioridade", "Fornecedor", "Código do fornecedor", "Criticidade",
    "Total em atraso do fornecedor", "Maior atraso do fornecedor (dias)",
    "Pagar agora do fornecedor", "Saldo a negociar do fornecedor",
    "Título", "Parcela", "Documento", "Emissão", "Lançamento", "Vencimento",
    "Dias em atraso", "Valor", "Saldo a pagar", "Status", "Descrição",
)


def _money(value):
    return Decimal(str(value or 0))


def _date(value):
    return date.fromisoformat(str(value)[:10]) if value else None


def build_payables_workbook(fornecedores):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Contas vencidas"
    sheet.append(HEADERS)
    row_number = 1
    for priority, supplier in enumerate(fornecedores, 1):
        for title in supplier.get("titulos", []):
            balance = _money(title.get("saldo_a_pagar"))
            days = int(title.get("dias_vencidos") or 0)
            sheet.append([
                priority, supplier.get("nome_fornecedor") or "",
                str(supplier.get("codigo_fornecedor") or ""),
                "Crítico" if supplier.get("critico") else "Não crítico",
                _money(supplier.get("valor_total_vencido")),
                int(supplier.get("dias_atraso") or 0),
                _money(supplier.get("pagamento_imediato")),
                _money(supplier.get("saldo_negociar")),
                str(title.get("codigo_parcela") or ""),
                str(title.get("numero_parcela") or ""),
                str(title.get("numero_documento") or ""),
                _date(title.get("data_emissao")),
                _date(title.get("data_lancamento")),
                _date(title.get("data_vencimento")), days,
                _money(title.get("valor_total")), balance,
                "Pago" if balance <= 0 else "Em atraso" if days > 0 else "A vencer",
                title.get("descricao_conta") or "",
            ])
            row_number += 1
            for cell in sheet[row_number]:
                # API text must stay literal, including values beginning with '='.
                if isinstance(cell.value, str):
                    cell.data_type = "s"
                if cell.column in (5, 7, 8, 16, 17):
                    cell.number_format = '"R$" #,##0.00'
                elif cell.column in (12, 13, 14):
                    cell.number_format = "dd/mm/yyyy"
    sheet.freeze_panes = "D2"
    sheet.auto_filter.ref = sheet.dimensions
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1B7088")
        cell.alignment = Alignment(wrap_text=True, vertical="center")
    sheet.row_dimensions[1].height = 42
    for column in range(1, len(HEADERS) + 1):
        sheet.column_dimensions[get_column_letter(column)].width = (
            60 if column == 19 else 36 if column == 2 else 22
        )
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()
