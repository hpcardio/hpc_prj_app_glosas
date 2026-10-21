from copy import deepcopy
from datetime import datetime
from io import BytesIO
from unittest.mock import patch

from django.test import TestCase
from openpyxl import load_workbook

from .payables_export import HEADERS, build_payables_workbook
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
        sheet = load_workbook(BytesIO(response.content)).active
        self.assertEqual(sheet.max_row, 3)
        self.assertEqual(tuple(c.value for c in sheet[1]), HEADERS)
        self.assertEqual(sheet["A3"].value, 2)
        self.assertEqual(sheet["C3"].value, "20")
        self.assertEqual(sheet["I3"].value, "100")
        self.assertEqual(sheet["L2"].value, datetime(2026, 5, 18))
        self.assertEqual(sheet["P2"].value, 650000)
        self.assertEqual(sheet["R2"].value, "Em atraso")
        self.assertEqual(sheet["S2"].value, "Medicamentos")
        self.assertEqual(sheet["P2"].data_type, "n")
        api_get.assert_called_once()
        self.assertEqual(api_get.call_args.kwargs["params"], {**filters, "exportar": "true"})

    def test_literal_text_statuses_and_missing_dates(self):
        supplier = existing_tests.ContasPagarTests.payload()["fornecedores"][0]
        supplier["nome_fornecedor"] = "=1+1"
        title = supplier["titulos"][0]
        title.update(numero_documento="000123", descricao_conta="=HYPERLINK(x)",
                     data_emissao=None, saldo_a_pagar=0)
        future = deepcopy(title)
        future.update(saldo_a_pagar=100, dias_vencidos=0)
        supplier["titulos"].append(future)
        sheet = load_workbook(BytesIO(build_payables_workbook([supplier]))).active
        self.assertEqual(sheet["B2"].data_type, "s")
        self.assertEqual(sheet["S2"].data_type, "s")
        self.assertEqual(sheet["K2"].value, "000123")
        self.assertIsNone(sheet["L2"].value)
        self.assertEqual(sheet["R2"].value, "Pago")
        self.assertEqual(sheet["R3"].value, "A vencer")

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
        response = self.client.get(self.url, {"q": "A & B", "page": 2})
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
