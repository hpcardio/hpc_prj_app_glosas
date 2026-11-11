from core.payables_lazy_testing import lazy_get
from copy import deepcopy
from datetime import datetime
from io import BytesIO
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from openpyxl import load_workbook

from openpyxl.utils import get_column_letter

from .payables_export import HEADERS, SUMMARY_HEADERS, build_payables_workbook


def cell(header, row):
    return f"{get_column_letter(HEADERS.index(header) + 1)}{row}"
from .services import ApiError
from . import tests as existing_tests


class PayablesExportTests(TestCase):
    setUp = existing_tests.ContasPagarTests.setUp
    url = "/financeiro/contas-a-pagar/operacao/"

    @patch("core.views.api_get")
    def test_exports_all_suppliers_in_one_request_with_applied_filters(self, api_get):
        first = existing_tests.ContasPagarTests.payload()
        first["total_pages"] = 1
        second = deepcopy(first)
        second["fornecedores"][0]["codigo_fornecedor"] = 20
        second["fornecedores"][0]["titulos"][0]["codigo_parcela"] = 100
        first["fornecedores"].extend(second["fornecedores"])
        api_get.return_value = first
        filters = dict(q="Essencial", criticidade="criticos", status="NEGOCIACAO",
                       data_inicio="2026-01-01", data_fim="2026-12-31")
        response = self.client.get(self.url, {**filters, "page": 9, "export": "xlsx"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertEqual(response["Cache-Control"], "private, no-store")
        workbook = load_workbook(BytesIO(response.content))
        summary = workbook.active
        self.assertEqual(tuple(c.value for c in summary[1]), SUMMARY_HEADERS)
        self.assertEqual(summary.max_row, 3)
        self.assertEqual(summary["B2"].value, 500000)
        sheet = workbook["Contas vencidas"]
        self.assertEqual(sheet.max_row, 3)
        self.assertEqual(tuple(c.value for c in sheet[1]), HEADERS)
        self.assertEqual(sheet[cell("Prioridade", 3)].value, 2)
        self.assertEqual(sheet[cell("Código do fornecedor", 3)].value, "20")
        self.assertEqual(sheet[cell("Título", 3)].value, "100")
        self.assertEqual(sheet[cell("Dt. Emissão", 2)].value, datetime(2026, 5, 18))
        self.assertEqual(sheet[cell("Valor Título", 2)].value, 650000)
        self.assertEqual(sheet[cell("Status", 2)].value, "Em atraso")
        self.assertEqual(sheet[cell("Descrição da Conta", 2)].value, "Medicamentos")
        self.assertEqual(sheet[cell("Valor Título", 2)].data_type, "n")
        api_get.assert_called_once()
        self.assertEqual(api_get.call_args.kwargs["params"],
                         {**filters, "exportar": "true", "incluir_pagos": "true"})

    def test_literal_text_statuses_and_missing_dates(self):
        supplier = existing_tests.ContasPagarTests.payload()["fornecedores"][0]
        supplier["nome_fornecedor"] = "=1+1"
        title = supplier["titulos"][0]
        title.update(numero_documento="000123", descricao_conta="=HYPERLINK(x)",
                     data_emissao=None, saldo_a_pagar=0)
        future = deepcopy(title)
        future.update(saldo_a_pagar=100, dias_vencidos=0)
        supplier["titulos"].append(future)
        workbook = load_workbook(BytesIO(build_payables_workbook([supplier])))
        self.assertEqual(workbook.active["A2"].data_type, "s")
        sheet = workbook["Contas vencidas"]
        self.assertEqual(sheet[cell("Fornecedor", 2)].data_type, "s")
        self.assertEqual(sheet[cell("Descrição da Conta", 2)].data_type, "s")
        self.assertEqual(sheet[cell("Nr. Documento", 2)].value, "000123")
        self.assertIsNone(sheet[cell("Dt. Emissão", 2)].value)
        self.assertEqual(sheet[cell("Status", 2)].value, "Pago")
        self.assertEqual(sheet[cell("Status", 3)].value, "A vencer")

    def test_titles_follow_the_mv_spreadsheet_order_and_fields(self):
        self.assertEqual(HEADERS[:12], (
            "Dt. Lançamento", "Dt. Emissão", "Dt. Vencimento", "Fornecedor",
            "Descrição da Conta", "Observação", "Valor Título", "Desconto",
            "Acréscimo", "Nr. Documento", "Data do Pagamento", "Valor Pago",
        ))
        self.assertEqual(len(HEADERS), len(set(HEADERS)))
        supplier = existing_tests.ContasPagarTests.payload()["fornecedores"][0]
        title = supplier["titulos"][0]
        title.update(observacao="=Renegociado", valor_desconto="10.50",
                     valor_acrescimo=None, valor_honrado_oracle="40.00",
                     data_pagamento_mv="2026-06-03")
        bare = deepcopy(title)
        for key in ("observacao", "valor_desconto", "valor_acrescimo",
                    "valor_honrado_oracle", "data_pagamento_mv"):
            bare.pop(key)
        supplier["titulos"].append(bare)
        sheet = load_workbook(BytesIO(build_payables_workbook([supplier])))["Contas vencidas"]
        self.assertEqual(tuple(c.value for c in sheet[1]), HEADERS)
        self.assertEqual(sheet.freeze_panes, "E2")
        self.assertEqual(sheet[cell("Observação", 2)].value, "=Renegociado")
        self.assertEqual(sheet[cell("Observação", 2)].data_type, "s")
        self.assertEqual(sheet[cell("Desconto", 2)].value, 10.5)
        self.assertEqual(sheet[cell("Desconto", 2)].number_format, '"R$" #,##0.00')
        self.assertIsNone(sheet[cell("Acréscimo", 2)].value)
        self.assertEqual(sheet[cell("Valor Pago", 2)].value, 40)
        self.assertEqual(sheet[cell("Data do Pagamento", 2)].value, datetime(2026, 6, 3))
        self.assertEqual(sheet[cell("Data do Pagamento", 2)].number_format, "dd/mm/yyyy")
        self.assertEqual(sheet[cell("Dt. Lançamento", 2)].number_format, "dd/mm/yyyy")
        self.assertIsNone(sheet[cell("Observação", 3)].value)
        self.assertIsNone(sheet[cell("Desconto", 3)].value)
        self.assertEqual(sheet[cell("Valor Pago", 3)].value, 0)
        self.assertIsNone(sheet[cell("Data do Pagamento", 3)].value)

    @patch("core.views.api_get")
    def test_empty_export_has_headers(self, api_get):
        api_get.return_value = {"fornecedores": [], "total_pages": 1}
        response = self.client.get(self.url, {"export": "xlsx"})
        sheet = load_workbook(BytesIO(response.content)).active
        self.assertEqual(sheet.max_row, 1)

    @patch("core.views.api_get")
    def test_api_failure_never_downloads_partial_workbook(self, api_get):
        payload = existing_tests.ContasPagarTests.payload()
        payload["total_pages"] = 2
        api_get.return_value = payload
        response = self.client.get(self.url, {"q": "Essencial", "export": "xlsx"})
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("export=", response.url)
        self.assertIn("q=Essencial", response.url)

    @patch("core.views.api_get")
    def test_link_preserves_applied_query_without_current_page(self, api_get):
        payload = existing_tests.ContasPagarTests.payload()
        payload["total_pages"] = 3
        api_get.return_value = payload
        response = lazy_get(self.client, self.url, {"q": "A & B", "page": 2})
        self.assertContains(response, "Exportar Excel")
        self.assertIn("q=A+%26+B", response.context["export_url"])
        self.assertNotIn("page=", response.context["export_url"])

    @patch("core.views.api_get")
    def test_export_requires_screen_permission(self, api_get):
        session = self.client.session
        session["api_user"] = {"perfil": "usuario", "telas_permitidas": []}
        session.save()
        response = self.client.get(self.url, {"export": "xlsx"})
        self.assertEqual(response.status_code, 302)
        api_get.assert_not_called()

    @patch("core.views.api_get")
    def test_export_requires_login(self, api_get):
        self.client.session.flush()
        response = self.client.get(self.url, {"export": "xlsx"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith("/login/"))
        api_get.assert_not_called()

    @patch("core.views.api_get")
    def test_api_error_preserves_filters_without_download(self, api_get):
        api_get.side_effect = ApiError("Falha na API")
        response = self.client.get(self.url, {"q": "Essencial", "export": "xlsx"})
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("export=", response.url)
        self.assertIn("q=Essencial", response.url)


class SupplierSummaryExportTests(TestCase):
    def supplier(self):
        supplier = existing_tests.ContasPagarTests.payload()["fornecedores"][0]
        supplier["nivel_criticidade"] = 4
        supplier["titulos"][0]["valor_nota_fiscal"] = "1300000.00"
        return supplier

    def test_latest_due_date_drives_invoice_value_age_and_quadrant(self):
        supplier = self.supplier()
        older = supplier["titulos"][0]
        older["data_emissao"] = "2026-09-15"
        recent = {**older, "codigo_parcela": 100, "data_emissao": "2026-01-01",
                  "data_vencimento": "2026-09-17", "dias_vencidos": 30,
                  "valor_total": "100.00", "valor_nota_fiscal": "300.00"}
        paid = {**recent, "codigo_parcela": 101, "data_vencimento": "2026-12-01",
                "saldo_a_pagar": "0.00"}
        supplier["titulos"] += [recent, paid]
        workbook = load_workbook(BytesIO(build_payables_workbook([supplier])))
        summary = workbook.active
        self.assertEqual(summary.max_column, 8)
        self.assertEqual(summary.max_row, 2)
        self.assertEqual([c.value for c in summary[2]], [
            "Fornecedor Essencial", 500000, datetime(2026, 9, 17), 300,
            "4 - Alta", "1 - 1–30 dias", "Q10", "Sem classificação",
        ])
        self.assertEqual(summary["D2"].data_type, "n")
        self.assertEqual(summary["C2"].number_format, "dd/mm/yyyy")
        self.assertEqual(summary.auto_filter.ref, "A1:H2")
        self.assertEqual(workbook["Contas vencidas"].max_row, 4)

    def test_age_boundaries_and_pending_classification(self):
        cases = [(0, "Fora da matriz"), (1, "Q15"), (30, "Q15"),
                 (31, "Q14"), (60, "Q14"), (61, "Q13"), (90, "Q13"),
                 (91, "Q12"), (120, "Q12"), (121, "Q11")]
        for days, quadrant in cases:
            with self.subTest(days=days):
                supplier = self.supplier()
                supplier["nivel_criticidade"] = 3
                supplier["titulos"][0]["dias_vencidos"] = days
                sheet = load_workbook(BytesIO(build_payables_workbook([supplier]))).active
                self.assertEqual(sheet["G2"].value, quadrant)
        supplier["nivel_criticidade"] = None
        sheet = load_workbook(BytesIO(build_payables_workbook([supplier]))).active
        self.assertEqual(sheet["G2"].value, "Classificação pendente")

    def test_missing_invoice_data_is_not_invented(self):
        supplier = self.supplier()
        supplier["titulos"][0].pop("valor_nota_fiscal")
        sheet = load_workbook(BytesIO(build_payables_workbook([supplier]))).active
        self.assertIsNone(sheet["D2"].value)
        supplier["titulos"][0]["data_vencimento"] = None
        sheet = load_workbook(BytesIO(build_payables_workbook([supplier]))).active
        self.assertIsNone(sheet["C2"].value)
        self.assertEqual(sheet["G2"].value, "Não informado")
        supplier["titulos"] = []
        sheet = load_workbook(BytesIO(build_payables_workbook([supplier]))).active
        self.assertEqual(sheet.max_row, 2)
        self.assertEqual(sheet["B2"].value, 500000)

    def test_installments_do_not_multiply_original_invoice_value(self):
        supplier = self.supplier()
        title = supplier["titulos"][0]
        supplier["titulos"].append({**title, "codigo_parcela": 100,
                                   "data_vencimento": "2026-07-09"})
        sheet = load_workbook(BytesIO(build_payables_workbook([supplier]))).active
        self.assertEqual(sheet["D2"].value, 1300000)
        self.assertEqual(sheet["C2"].value, datetime(2026, 7, 9))


class PayablesTitlePaymentDateTests(SimpleTestCase):
    def render(self, **titulo):
        from django.template.loader import render_to_string
        base = {"codigo_parcela": 1, "numero_parcela": 1, "valor_total": 100,
                "saldo_a_pagar": 100, "dias_vencidos": 0, "pagamentos": []}
        fornecedor = {"codigo_fornecedor": 10, "titulos": [{**base, **titulo}]}
        return render_to_string("partials/payables_supplier_details.html",
                                {"fornecedor": fornecedor, "modo": "operacao"})

    def test_title_shows_the_mv_payment_date(self):
        html = self.render(data_pagamento_mv="2026-06-03")
        self.assertIn("DATA DO PAGAMENTO (MV)", html)
        self.assertIn("03/06/2026", html)

    def test_title_without_payment_date_shows_dash(self):
        html = self.render(data_pagamento_mv=None)
        self.assertIn("<small>DATA DO PAGAMENTO (MV)</small><strong>-</strong>", html)
