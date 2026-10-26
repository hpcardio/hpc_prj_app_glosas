from unittest.mock import patch

from django.test import TestCase

from core.services import ApiError
from core.payables_priority import decorate_suppliers, month_start, trend_history


class PriorityTests(TestCase):
    def setUp(self):
        session = self.client.session
        session["api_access_token"] = "test-only"
        session["api_user"] = {
            "id": 7,
            "nome": "Financeiro",
            "perfil": "usuario",
            "telas_permitidas": [
                "contas_pagar_gestao",
                "contas_pagar_operacao",
                "contas_pagar_acompanhamento",
                "fornecedores_criticos",
            ],
        }
        session.save()

    @patch("core.views.api_patch")
    def test_save_five_level_classification(self, api_patch):
        response = self.client.post(
            "/administrativo/fornecedores-criticos/",
            {
                "codigo_fornecedor": "10",
                "nivel_criticidade": "4",
                "justificativa": "OPME essencial",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            api_patch.call_args.args[1],
            {"nivel_criticidade": 4, "justificativa": "OPME essencial"},
        )

    @patch("core.views.api_patch")
    def test_invalid_level_does_not_write(self, api_patch):
        self.client.post(
            "/administrativo/fornecedores-criticos/",
            {"codigo_fornecedor": "10", "nivel_criticidade": "9"},
        )
        api_patch.assert_not_called()

    @patch("core.views.api_put")
    def test_save_budget_only_calls_budget_endpoint(self, api_put):
        response = self.client.post(
            "/financeiro/contas-a-pagar/gestao/",
            {
                "form_action": "orcamento_salvar",
                "competencia": "2026-10",
                "valor_orcamento": "350000.01",
            },
        )
        self.assertEqual(response.status_code, 302)
        api_put.assert_called_once_with(
            "/app_glosas/financeiro/contas-a-pagar/orcamento/2026-10-01",
            {"valor": "350000.01"},
        )
        self.assertIn("competencia=2026-10", response.url)

    @patch("core.views.api_put")
    def test_invalid_month_does_not_write(self, api_put):
        self.client.post(
            "/financeiro/contas-a-pagar/gestao/",
            {
                "form_action": "orcamento_salvar",
                "competencia": "invalid",
                "valor_orcamento": "100",
            },
        )
        api_put.assert_not_called()

    @patch("core.views.api_get")
    def test_title_filters_are_preserved_in_export_and_pagination(self, api_get):
        api_get.return_value = {"fornecedores": [], "total": 40, "total_pages": 2}
        response = self.client.get(
            "/financeiro/contas-a-pagar/operacao/",
            {"nivel_criticidade": "4", "faixa_atraso": "1", "somente_vencidos": "true"},
        )
        self.assertEqual(api_get.call_args.kwargs["params"]["faixa_atraso"], "1")
        self.assertContains(response, "faixa_atraso=1")
        self.assertContains(response, "nivel_criticidade=4")
        self.assertContains(response, "export=xlsx")

    @patch("core.views.api_get")
    def test_matrix_failure_keeps_existing_queue(self, api_get):
        api_get.side_effect = [
            {"fornecedores": [], "total": 0, "total_pages": 1},
            ApiError("unavailable"),
        ]
        response = self.client.get("/financeiro/contas-a-pagar/gestao/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Matriz indisponível")
        self.assertContains(response, "Comitê Diário de Caixa")
        self.assertNotContains(response, 'name="valor_orcamento"')

    @patch("core.views.api_get")
    def test_matrix_cells_link_to_title_filters(self, api_get):
        matrix = {
            "linhas": [
                {
                    "nivel": 5,
                    "label": "Extrema",
                    "celulas": [
                        {
                            "nivel": 5,
                            "faixa": 1,
                            "quadrante": 5,
                            "cor": "red",
                            "quantidade": 2,
                            "valor": "100.00",
                            "orcamento": "0.00",
                        }
                    ],
                }
            ],
            "colunas": [],
            "pendentes": {"quantidade": 0},
            "orcamento": "0.00",
        }
        api_get.side_effect = [{"fornecedores": [], "total": 0}, matrix]
        response = self.client.get(
            "/financeiro/contas-a-pagar/gestao/?competencia=2026-10&q=ABC"
        )
        self.assertContains(response, "Criticidade × tempo de atraso")
        self.assertContains(response, "nivel_criticidade=5")
        self.assertContains(response, "faixa_atraso=1")
        self.assertContains(response, "q=ABC")
        self.assertContains(response, 'value="2026-10"')

    @patch("core.views.api_put")
    def test_budget_write_requires_screen_access(self, api_put):
        session = self.client.session
        session["api_user"] = {
            "id": 9,
            "perfil": "usuario",
            "telas_permitidas": ["contas_pagar_operacao"],
        }
        session.save()
        response = self.client.post(
            "/financeiro/contas-a-pagar/gestao/", {"form_action": "orcamento_salvar"}
        )
        self.assertNotEqual(response.status_code, 200)
        api_put.assert_not_called()

    def test_old_payload_remains_readable_and_history_is_not_summed(self):
        row = decorate_suppliers([{"critico": True, "dias_atraso": 31}])[0]
        self.assertTrue(row["criticidade_pendente"])
        self.assertEqual(row["atraso_label"], "31–60 dias")
        self.assertEqual(month_start("2026-10"), "2026-10-01")
        trend = trend_history([{"valor_vencido": "200"}, {"valor_vencido": "100"}])
        self.assertEqual([r["largura"] for r in trend], [100, 50])

    @patch("core.views.api_put")
    def test_missing_budget_month_does_not_write(self, api_put):
        self.client.post(
            "/financeiro/contas-a-pagar/gestao/",
            {"form_action": "orcamento_salvar", "valor_orcamento": "100"},
        )
        api_put.assert_not_called()

    def test_excel_keeps_existing_columns_and_appends_priority(self):
        from io import BytesIO
        from openpyxl import load_workbook
        from core.payables_export import build_payables_workbook
        from core.tests import ContasPagarTests

        supplier = ContasPagarTests.payload()["fornecedores"][0]
        supplier["nivel_criticidade"] = 4
        sheet = load_workbook(BytesIO(build_payables_workbook([supplier]))).active
        self.assertEqual(sheet["D2"].value, "Crítico")
        self.assertEqual(sheet["S2"].value, "Medicamentos")
        self.assertEqual(sheet["T2"].value, 4)
        self.assertEqual(sheet["U2"].value, "Alta")
        self.assertEqual(sheet["V2"].value, "91–120 dias")
