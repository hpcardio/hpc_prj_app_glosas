"""Dívida vencida em acordo (renegociada no MV com Prorrogar/Parcelar).

O acordo soma na dívida vencida, em faixa própria: o card mostra a divisão
em atraso / em acordo e cada título mostra o vencimento original e o novo.
"""
from io import BytesIO
from unittest.mock import patch

from django.test import TestCase
from openpyxl import load_workbook

from core.payables_export import ACORDO_HEADERS, HEADERS, build_payables_workbook
from core.payables_lazy_testing import lazy_get
from core.payables_priority import summary_card_filters
from core import tests as existing_tests

URL = "/financeiro/contas-a-pagar/operacao/"


def payload_com_acordo():
    data = existing_tests.ContasPagarTests.payload()
    fornecedor = data["fornecedores"][0]
    fornecedor.update({
        "valor_total_vencido": "509700.00",
        "valor_vencido": "509700.00",
        "valor_vencido_em_atraso": "500000.00",
        "valor_em_acordo": "9700.00",
        "titulos_em_acordo": 1,
    })
    fornecedor["titulos"].append({
        **fornecedor["titulos"][0],
        "codigo_parcela": 225814,
        "numero_parcela": 2,
        "data_vencimento": "2026-12-21",
        "data_vencimento_original": "2026-04-10",
        "data_acordo": "2026-11-12",
        "acordo_parcela_nova": True,
        "situacao_acordo": "a_vencer",
        "valor_total": "580.00",
        "saldo_a_pagar": "580.00",
        "dias_vencidos": 0,
        "pagamentos": [],
    })
    data["resumo"].update({
        "valor_vencido_atual": "5009700.00",
        "valor_vencido_em_atraso": "5000000.00",
        "valor_em_acordo": "9700.00",
    })
    return data


class AcordosTests(TestCase):
    setUp = existing_tests.ContasPagarTests.setUp

    @patch("core.views.api_get")
    def test_card_e_titulo_destacam_o_acordo(self, api_get):
        api_get.return_value = payload_com_acordo()

        response = lazy_get(self.client, URL, details=True)

        self.assertContains(response, "payables-debt-split")
        self.assertContains(response, "R$ 9.700,00")
        self.assertContains(response, "componente=em_acordo")
        self.assertContains(response, "em acordo</b>")
        self.assertContains(response, 'class="payables-title-card is-agreement"')
        self.assertContains(response, "orig. 10/04/2026")
        self.assertContains(response, ">Em acordo</strong>")
        self.assertContains(response, "VENCIMENTO ORIGINAL")
        self.assertContains(response, "NOVO VENCIMENTO (ACORDO)")
        self.assertContains(response, "21/12/2026")
        self.assertContains(response, "12/11/2026")
        self.assertContains(response, "Parcela criada no parcelamento")

    @patch("core.views.api_get")
    def test_sem_acordo_nao_mostra_a_divisao(self, api_get):
        api_get.return_value = existing_tests.ContasPagarTests.payload()
        response = lazy_get(self.client, URL, details=True)
        self.assertNotContains(response, "payables-debt-split")
        self.assertNotContains(response, "is-agreement")

    def test_card_em_acordo_filtra_pela_api(self):
        self.assertEqual(
            summary_card_filters({"componente": "em_acordo"}),
            {"componente": "em_acordo"},
        )

    def test_planilha_traz_vencimento_original_e_status(self):
        fornecedor = payload_com_acordo()["fornecedores"][0]
        sheet = load_workbook(BytesIO(build_payables_workbook([fornecedor])))["Contas vencidas"]
        linhas = {
            sheet.cell(row=r, column=HEADERS.index("Título") + 1).value: r
            for r in (2, 3)
        }
        valor = lambda titulo, header: sheet.cell(
            row=linhas[titulo], column=HEADERS.index(header) + 1
        ).value
        self.assertEqual(HEADERS[-3:], ACORDO_HEADERS)
        self.assertEqual(valor("225814", "Status"), "Em acordo")
        self.assertEqual(valor("225814", "Vencimento original (acordo)").date().isoformat(), "2026-04-10")
        self.assertEqual(valor("225814", "Data do acordo").date().isoformat(), "2026-11-12")
        self.assertEqual(valor("225814", "Em acordo do fornecedor"), 9700)
        self.assertEqual(valor("99", "Status"), "Em atraso")
        self.assertIsNone(valor("99", "Vencimento original (acordo)"))


class ExportacaoFiltroTests(TestCase):
    setUp = existing_tests.ContasPagarTests.setUp

    @patch("core.views.api_get")
    def test_botao_exportar_mostra_o_filtro_do_card(self, api_get):
        api_get.return_value = payload_com_acordo()
        response = lazy_get(self.client, URL + "?componente=em_acordo")
        self.assertContains(response, "Exportar Excel · Dívida vencida em acordo")
        self.assertIn("componente=em_acordo", response.context["export_url"])

    @patch("core.views.api_get")
    def test_botao_exportar_sem_filtro_de_card(self, api_get):
        api_get.return_value = payload_com_acordo()
        response = lazy_get(self.client, URL)
        self.assertContains(response, "Exportar Excel</a>")


class LoginComExportacaoTests(TestCase):
    @patch("core.views.api_get")
    @patch("core.views.api_authenticate")
    def test_login_volta_para_a_tela_e_nao_para_o_download(self, authenticate, api_get):
        authenticate.return_value = {"access_token": "token-seguro"}
        api_get.return_value = {
            "id": 10, "nome": "Financeiro", "email": "fin@teste.com",
            "perfil": "usuario", "telas_permitidas": ["contas_pagar_operacao"],
        }
        response = self.client.post("/login/", {
            "email": "fin@teste.com", "password": "senha",
            "next": URL + "?q=&componente=em_acordo&export=xlsx",
        })
        self.assertRedirects(
            response, URL + "?q=&componente=em_acordo", fetch_redirect_response=False,
        )
