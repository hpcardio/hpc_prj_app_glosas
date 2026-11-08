from io import BytesIO
from unittest.mock import patch

from django.test import TestCase
from openpyxl import load_workbook

from .payables_export import HEADERS, build_payables_workbook
from . import tests as existing_tests


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
        response = self.client.get(self.url, {"classificacao": "ACORDO"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(api_get.call_args.kwargs["params"]["classificacao"], "ACORDO")
        self.assertContains(response, '<option value="ACORDO" selected>ACORDO</option>', html=True)
        self.assertContains(response, "Classificação da conta aplicada")
        self.assertContains(response, "Classificação: ACORDO")
        self.assertContains(response, "CLASSIFICAÇÃO DA CONTA")

    @patch("core.views.api_get")
    def test_no_filter_sends_no_classification(self, api_get):
        api_get.return_value = self.payload()
        response = self.client.get(self.url)
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
