from io import BytesIO
from unittest.mock import patch

from django.test import TestCase
from openpyxl import load_workbook

from .payables_export import HEADERS, build_payables_workbook
from . import tests as existing_tests
from .payables_lazy_testing import lazy_get


class PayablesAccountClassificationTests(TestCase):
    setUp = existing_tests.ContasPagarTests.setUp
    url = "/financeiro/contas-a-pagar/operacao/"

    def payload(self):
        data = existing_tests.ContasPagarTests.payload()
        data["opcoes_classificacao"] = [
            {"valor": "SEM_CLASSIFICACAO", "nome": "Sem classificação"},
            {"valor": "ACORDO", "nome": "ACORDO"},
        ]
        supplier = data["fornecedores"][0]
        supplier["classificacoes"] = ["ACORDO"]
        supplier["titulos"][0]["classificacao"] = "ACORDO"
        return data

    @patch("core.views.api_get")
    def test_filter_reaches_the_api_and_the_screen_shows_it(self, api_get):
        api_get.return_value = self.payload()
        response = lazy_get(self.client, self.url, {"classificacao": "ACORDO"}, details=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(all(c.kwargs["params"]["classificacao"] == "ACORDO" for c in api_get.call_args_list))
        self.assertContains(response, '<option value="ACORDO" selected>ACORDO</option>', html=True)
        self.assertContains(response, "Classificação da conta aplicada")
        self.assertContains(response, "Classificação: ACORDO")
        self.assertContains(response, "CLASSIFICAÇÃO DA CONTA")

    @patch("core.views.api_get")
    def test_no_filter_sends_no_classification(self, api_get):
        api_get.return_value = self.payload()
        response = lazy_get(self.client, self.url)
        self.assertNotIn("classificacao", api_get.call_args.kwargs["params"])
        self.assertNotContains(response, "Classificação da conta aplicada")
        self.assertContains(response, '<option value="SEM_CLASSIFICACAO">Sem classificação</option>', html=True)

    def test_export_has_the_account_classification_column(self):
        data = self.payload()
        data["fornecedores"][0]["titulos"].append(
            {**data["fornecedores"][0]["titulos"][0], "classificacao": None}
        )
        sheet = load_workbook(BytesIO(build_payables_workbook(data["fornecedores"])))["Contas vencidas"]
        column = HEADERS.index("Classificação da conta") + 1
        self.assertEqual(column, len(HEADERS))
        self.assertEqual(sheet.cell(row=2, column=column).value, "ACORDO")
        self.assertEqual(sheet.cell(row=3, column=column).value, "Sem classificação")


class PayablesManagementClassificationTests(TestCase):
    setUp = existing_tests.ContasPagarTests.setUp
    url = "/financeiro/contas-a-pagar/gestao/"

    @patch("core.views.api_get")
    def test_management_has_the_filter_and_sends_it(self, api_get):
        data = PayablesAccountClassificationTests.payload(self)
        api_get.return_value = data
        response = lazy_get(self.client, self.url, {"classificacao": "ACORDO"})
        self.assertContains(response, '<option value="ACORDO" selected>ACORDO</option>', html=True)
        self.assertContains(response, "Exportar Excel")
        self.assertIn("classificacao=ACORDO", response.context["export_url"])
        self.assertEqual(api_get.call_args_list[0].kwargs["params"]["classificacao"], "ACORDO")

    @patch("core.views.api_get")
    def test_management_exports_with_the_account_classification(self, api_get):
        data = PayablesAccountClassificationTests.payload(self)
        data["total_pages"] = 1
        api_get.return_value = data
        response = self.client.get(self.url, {"classificacao": "ACORDO", "export": "xlsx"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("contas-a-pagar-gestao.xlsx", response["Content-Disposition"])
        self.assertEqual(api_get.call_args.kwargs["params"]["exportar"], "true")
        self.assertEqual(api_get.call_args.kwargs["params"]["classificacao"], "ACORDO")
        book = load_workbook(BytesIO(response.content))
        titles = book["Contas vencidas"]
        self.assertEqual(titles.cell(row=2, column=HEADERS.index("Classificação da conta") + 1).value, "ACORDO")
        summary = book["Resumo por fornecedor"]
        self.assertEqual(summary.cell(row=1, column=8).value, "Classificação da conta")
        self.assertEqual(summary.cell(row=2, column=8).value, "ACORDO")
        self.assertIn("Classificação de criticidade", HEADERS)
