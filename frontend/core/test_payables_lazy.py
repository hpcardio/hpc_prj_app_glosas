from copy import deepcopy
from unittest.mock import patch

from django.test import TestCase

from . import tests as existing_tests
from .services import ApiError


class PayablesLazyLoadingTests(TestCase):
    """Regression tests for the lazy Contas a Pagar page (summary and details on demand)."""

    setUp = existing_tests.ContasPagarTests.setUp
    url = "/financeiro/contas-a-pagar/operacao/"

    @patch("core.views.api_get")
    def test_entry_page_does_not_wait_for_the_api(self, api_get):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-payables-summary")
        self.assertIn("no-store", response["Cache-Control"])
        api_get.assert_not_called()

    @patch("core.views.api_get")
    def test_summary_fragment_asks_without_titles(self, api_get):
        api_get.return_value = existing_tests.ContasPagarTests.payload()
        response = self.client.get(self.url, {"fragmento": "resumo"})
        self.assertEqual(response["X-ReceitaCerta-Fragment"], "payables-summary")
        self.assertContains(response, 'data-payables-detail="10"')
        self.assertEqual(api_get.call_args.kwargs["params"]["incluir_titulos"], "false")
        # Operation lists suppliers whose titles are all paid, too.
        self.assertEqual(api_get.call_args.kwargs["params"]["incluir_pagos"], "true")
        self.client.get("/financeiro/contas-a-pagar/gestao/", {"fragmento": "resumo"})
        self.assertNotIn("incluir_pagos", api_get.call_args.kwargs["params"])

    @patch("core.views.api_get")
    def test_summary_failure_returns_503_for_retry(self, api_get):
        api_get.side_effect = ApiError("down")
        response = self.client.get(self.url, {"fragmento": "resumo"})
        self.assertEqual(response.status_code, 503)

    @patch("core.views.api_get")
    def test_detail_fragment_respects_the_selected_suppliers(self, api_get):
        api_get.return_value = existing_tests.ContasPagarTests.payload()
        allowed = self.client.get(self.url, {"detalhes_fornecedor": "10"})
        self.assertEqual(allowed["X-ReceitaCerta-Fragment"], "payables-detail")
        self.assertEqual(api_get.call_args.kwargs["params"]["fornecedores_ids"], "10")
        refused = self.client.get(self.url, {"detalhes_fornecedor": "10", "fornecedores_ids": "20"})
        self.assertEqual(refused.status_code, 404)

    @patch("core.views.api_get")
    def test_detail_fragment_lists_paid_titles_in_operation(self, api_get):
        payload = existing_tests.ContasPagarTests.payload()
        pago = deepcopy(payload["fornecedores"][0]["titulos"][0])
        pago.update(codigo_parcela=777, tipo_quitacao="quitado", saldo_a_pagar="0.00",
                    dias_vencidos=0, valor_honrado_oracle="1234.56",
                    data_pagamento_mv="2026-10-05")
        payload["fornecedores"][0]["titulos"].append(pago)
        api_get.return_value = payload
        response = self.client.get(self.url, {"detalhes_fornecedor": "10"})
        self.assertEqual(api_get.call_args.kwargs["params"]["incluir_pagos"], "true")
        self.assertContains(response, "#777")
        self.assertContains(response, "VALOR PAGO (MV)")
        self.assertContains(response, "1.234,56")
        self.assertContains(response, "05/10/2026")
        self.assertContains(response, 'class="is-paid">Pago<')
        self.client.get("/financeiro/contas-a-pagar/gestao/", {"detalhes_fornecedor": "10"})
        self.assertNotIn("incluir_pagos", api_get.call_args.kwargs["params"])

    @patch("core.views.api_get")
    def test_matrix_fragment_only_in_management(self, api_get):
        response = self.client.get(self.url, {"fragmento": "matriz"})
        self.assertEqual(response.status_code, 404)
        api_get.assert_not_called()


class PayablesSummaryCardFilterTests(TestCase):
    """Operação: summary cards and the day-before toggle filter the list."""

    setUp = existing_tests.ContasPagarTests.setUp
    url = "/financeiro/contas-a-pagar/operacao/"

    @patch("core.views.api_get")
    def test_card_and_toggle_reach_the_api_and_stay_in_links(self, api_get):
        payload = existing_tests.ContasPagarTests.payload()
        payload["resumo"]["vencidos_dia_anterior"] = "321.00"
        api_get.return_value = payload
        response = self.client.get(self.url, {
            "fragmento": "resumo", "componente": "vencida",
            "vencidos_dia_anterior": "true", "page": "1",
        })
        params = api_get.call_args.kwargs["params"]
        self.assertEqual(params["componente"], "vencida")
        self.assertEqual(params["vencidos_dia_anterior"], "true")
        self.assertContains(response, "is-alert is-filterable is-selected")
        self.assertContains(response, 'aria-checked="true"')
        self.assertContains(response, "R$ 321,00")
        self.assertContains(response, "Exibindo os fornecedores que compõem <strong>Dívida vencida</strong>")
        # Clicking the active card again removes it; other cards switch to theirs.
        self.assertContains(response, 'href="?vencidos_dia_anterior=true" title="Remover filtro"')
        self.assertContains(response, 'href="?vencidos_dia_anterior=true&amp;componente=a_vencer"')
        self.assertContains(response, 'href="?componente=vencida" role="switch"')
        self.assertContains(response, 'name="componente" value="vencida"')
        self.assertIn("componente=vencida", response.context["export_url"])

    @patch("core.views.api_get")
    def test_invalid_card_is_ignored_and_other_views_have_no_links(self, api_get):
        api_get.return_value = existing_tests.ContasPagarTests.payload()
        response = self.client.get(self.url, {"fragmento": "resumo", "componente": "x"})
        self.assertNotIn("componente", api_get.call_args.kwargs["params"])
        self.assertContains(response, 'aria-checked="false"')
        self.assertNotContains(response, "is-selected")
        gestao = self.client.get("/financeiro/contas-a-pagar/gestao/", {
            "fragmento": "resumo", "componente": "vencida", "vencidos_dia_anterior": "true",
        })
        self.assertNotIn("componente", api_get.call_args.kwargs["params"])
        self.assertNotIn("vencidos_dia_anterior", api_get.call_args.kwargs["params"])
        self.assertNotContains(gestao, "payables-kpi-link")

    @patch("core.views.api_get")
    def test_detail_fragment_forwards_the_card(self, api_get):
        api_get.return_value = existing_tests.ContasPagarTests.payload()
        self.client.get(self.url, {"detalhes_fornecedor": "10", "componente": "novos_7d"})
        self.assertEqual(api_get.call_args.kwargs["params"]["componente"], "novos_7d")
