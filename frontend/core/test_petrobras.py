import json
from unittest.mock import Mock, patch
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.template.loader import render_to_string
from core import petrobras
from core.access import ROUTE_PERMISSIONS
from core.services import ApiError
from core.access import SCREEN_KEYS


@override_settings(PETROBRAS_ENABLED=True)
class PetrobrasTests(SimpleTestCase):
    def request(self, method="get", body=None):
        req = getattr(RequestFactory(), method)(
            "/follow-up-glosas/petrobras/glosas",
            data=json.dumps(body or {}) if method != "get" else {},
            content_type="application/json",
        )
        req.session = {"api_user": {"perfil": "usuario"}}
        return req

    def test_routes_use_followup_permission(self):
        self.assertEqual(ROUTE_PERMISSIONS["petrobras_bridge"], "follow_up_glosas")

    @patch("core.petrobras.api_request")
    def test_proxy_preserves_auth_context_and_query(self, api):
        upstream = Mock(
            content=b'{"total":0,"items":[]}',
            status_code=200,
            headers={"Content-Type": "application/json"},
        )
        api.return_value = upstream
        req = self.request()
        req.GET = req.GET.copy()
        req.GET["guia"] = "000123"
        response = petrobras.bridge(req, "glosas")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(api.call_args.kwargs["params"], {"guia": "000123"})
        self.assertEqual(
            api.call_args.args[:2], ("GET", "/app_glosas/petrobras/glosas")
        )
        self.assertEqual(response["Cache-Control"], "no-store")
        upstream.close.assert_called_once()

    @patch("core.petrobras.api_request")
    def test_arbitrary_proxy_paths_blocked(self, api):
        for path in [
            "../usuarios",
            "glosas/1/../../usuarios",
            "https://example.com",
            "glosas?guia=x",
        ]:
            self.assertEqual(petrobras.bridge(self.request(), path).status_code, 404)
        api.assert_not_called()

    @patch("core.petrobras.api_request")
    def test_batch_oracle_check_is_proxied_for_writers(self, api):
        api.return_value = Mock(status_code=200, content=b"{}", headers={})
        self.assertEqual(petrobras.bridge(self.request("post"), "glosas/cruzar").status_code, 200)
        self.assertEqual(api.call_args.args[:2], ("POST", "/app_glosas/petrobras/glosas/cruzar"))
        req = self.request("post")
        req.session["api_user"]["perfil"] = "leitura"
        self.assertEqual(petrobras.bridge(req, "glosas/cruzar").status_code, 403)

    @patch("core.petrobras.api_request")
    def test_readonly_cannot_mutate(self, api):
        req = self.request("post")
        req.session["api_user"]["perfil"] = "leitura"
        self.assertEqual(petrobras.bridge(req, "protocolos").status_code, 403)
        api.assert_not_called()

    @override_settings(PETROBRAS_ENABLED=False)
    @patch("core.petrobras.api_request")
    def test_disabled_rollout_does_not_call_api(self, api):
        self.assertEqual(petrobras.bridge(self.request(), "status").status_code, 404)
        api.assert_not_called()

    @patch(
        "core.petrobras.api_request",
        side_effect=ApiError("<html>upstream traceback</html>", 502),
    )
    def test_upstream_html_is_not_exposed(self, api):
        response = petrobras.bridge(self.request(), "glosas")
        self.assertNotIn(b"traceback", response.content)
        self.assertEqual(response.status_code, 502)

    def test_template_escapes_convenio_and_uses_static_scripts(self):
        req = self.request()
        html = render_to_string(
            "partials/petrobras_followup.html",
            {"request": req, "filtros": {"convenio": "<script>alert(1)</script>"}},
        )
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("petrobras-followup.js", html)
        self.assertIn("petrobras-followup.css", html)


    @patch("core.petrobras.api_request")
    def test_empty_optional_filters_are_not_forwarded(self, api):
        api.return_value = Mock(
            content=b'{"total":242,"items":[]}', status_code=200,
            headers={"Content-Type": "application/json"},
        )
        req = self.request()
        req.GET = req.GET.copy()
        req.GET.update({"prazo_ate": "", "tuss": " ", "offset": "0"})
        self.assertEqual(petrobras.bridge(req, "glosas").status_code, 200)
        self.assertEqual(api.call_args.kwargs["params"], {"offset": "0"})

    @patch("core.petrobras.api_request")
    def test_valid_deadline_is_preserved(self, api):
        api.return_value = Mock(
            content=b'{"total":1,"items":[]}', status_code=200,
            headers={"Content-Type": "application/json"},
        )
        req = self.request()
        req.GET = req.GET.copy()
        req.GET["prazo_ate"] = "2026-10-30"
        petrobras.bridge(req, "glosas")
        self.assertEqual(
            api.call_args.kwargs["params"], {"prazo_ate": "2026-10-30"}
        )


def petrobras_row(row_id=1, oracle=None, **fonte):
    return {
        "id": row_id,
        "demonstrativo": fonte.pop("demonstrativo", "D1"),
        "guia_prestador": "00000000000002407254",
        "guia_operadora": "122705001",
        "tuss": "40901360",
        "fonte": {
            "protocolo": "13822887",
            "lote": "35783619",
            "carteira": "000000000001",
            "senha": "B386348",
            "descricao": "DOPPLER",
            "data_emissao": "2026-10-01",
            "quantidade": "1",
            "valor_informado": "298.43",
            "valor_processado": "298.43",
            "valor_liberado": "298.42",
            "motivos": [{"codigo": "1705", "valor": "0.01"}],
            "cobertura": "parcial_nao_confirmada",
            **fonte,
        },
        "valor_glosa": "0.01",
        "cruzamento": "conciliado" if oracle else "nao_localizado",
        "oracle": oracle,
        "divergencias": [],
        "candidatos": 1 if oracle else 0,
        "recurso_estado": "rascunho",
        "justificativa": "",
        "versao": 1,
        "presente_na_fonte": True,
    }


ORACLE = {
    "cd_paciente": 51, "nm_paciente": "Paciente Sintético", "cd_atendimento": 789,
    "cd_remessa": 987, "cd_reg": 456, "cd_lancamento": 3, "tp_atendimento": "Externo",
    "cd_pro_fat": "40901360", "cd_gru_fat": 1, "ds_gru_fat": "EXAMES",
    "nm_convenio": "SAUDE PETROBRAS", "dt_atendimento": "2026-07-08",
    "descricao": "DOPPLER COLORIDO",
}


class PetrobrasFollowUpCardsTests(SimpleTestCase):
    @override_settings(PETROBRAS_ENABLED=True)
    def test_applicable_only_for_compatible_filters(self):
        self.assertTrue(petrobras.follow_up_aplicavel({}))
        self.assertTrue(petrobras.follow_up_aplicavel({"convenio": "SAUDE PETROBRAS"}))
        self.assertFalse(petrobras.follow_up_aplicavel({"convenio": "IPM"}))
        self.assertFalse(petrobras.follow_up_aplicavel({"processo_original": "P1"}))
        self.assertFalse(petrobras.follow_up_aplicavel({"tipo_atendimento": "Externo"}))
        self.assertFalse(petrobras.follow_up_aplicavel({"cd_remessa": "abc"}))
        with self.settings(PETROBRAS_ENABLED=False):
            self.assertFalse(petrobras.follow_up_aplicavel({}))

    @patch("core.petrobras.api_get")
    def test_page_forwards_filters_and_zero_limit(self, api_get):
        petrobras.follow_up_pagina(
            {"nr_guia": "B386348", "numero_protocolo": "13822887", "convenio": "x"}, 0, 0
        )
        api_get.assert_called_once_with(
            "/app_glosas/petrobras/demonstrativos",
            params={"guia": "B386348", "protocolo": "13822887", "offset": "0", "limit": "0"},
            timeout=60,
        )

    def test_cards_use_oracle_when_matched_and_payer_otherwise(self):
        cards = petrobras.follow_up_cards({
            "demonstrativos": ["D1"],
            "items": [petrobras_row(1, ORACLE), petrobras_row(2, None, carteira="000000000002")],
        })
        self.assertEqual(len(cards), 1)
        card = cards[0]
        self.assertEqual(card["processo"]["numero_processo"], "D1")
        self.assertEqual(card["processo"]["rotulo"], "DEMONSTRATIVO")
        self.assertEqual(card["numero_protocolo"], "13822887")
        self.assertEqual(card["cd_remessa"], 987)
        nomes = [p["nm_paciente"] for p in card["pacientes"]]
        self.assertEqual(nomes, ["Paciente Sintético", "Não informado"])
        oracle_item = card["pacientes"][0]["itens"][0]
        payer_item = card["pacientes"][1]["itens"][0]
        self.assertEqual(oracle_item["descricao"], "DOPPLER COLORIDO")
        self.assertEqual(payer_item["descricao"], "DOPPLER")
        self.assertEqual(payer_item["nr_guia"], "2407254")
        self.assertEqual(payer_item["senha"], "B386348")
        self.assertIsNone(payer_item["cd_atendimento"])
        self.assertFalse(payer_item["tratativa_disponivel"])
        self.assertEqual(str(card["valor_glosado"]), "0.02")
        self.assertEqual(str(card["valor_total_tratado"]), "0")

    def test_beneficiary_groups_by_wallet_and_senha(self):
        other_guide = petrobras_row(2, None)
        other_guide["guia_prestador"] = "00000000000009999999"
        other_senha = petrobras_row(3, None, senha="B000001")
        card = petrobras.follow_up_cards({
            "demonstrativos": ["D1"],
            "items": [petrobras_row(1, None), other_guide, other_senha],
        })[0]
        self.assertEqual(
            [len(p["itens"]) for p in card["pacientes"]], [2, 1]
        )

    def test_missing_payer_values_stay_unknown(self):
        row = petrobras_row(1, None, valor_processado=None, valor_informado=None)
        card = petrobras.follow_up_cards({"demonstrativos": ["D1"], "items": [row]})[0]
        item = card["pacientes"][0]["itens"][0]
        self.assertIsNone(item["valor_processado"])
        self.assertIsNone(card["valor_itens"])

    def test_sent_appeal_counts_as_treated(self):
        row = petrobras_row(1, ORACLE)
        row["recurso_estado"] = "protocolado"
        card = petrobras.follow_up_cards({"demonstrativos": ["D1"], "items": [row]})[0]
        self.assertEqual(str(card["valor_total_tratado"]), "0.01")
        self.assertEqual(str(card["valor_glosa_pendente"]), "0.00")


@override_settings(PETROBRAS_ENABLED=True)
class PetrobrasFollowUpViewTests(TestCase):
    STANDARD = "/app_glosas/financeiro/conciliacao-faturamento/glosas-pendentes"

    def setUp(self):
        session = self.client.session
        session["api_access_token"] = "token-seguro"
        session["api_user"] = {
            "id": 1, "nome": "Teste", "email": "t@teste.com",
            "perfil": "usuario", "telas_permitidas": list(SCREEN_KEYS),
        }
        session.save()

    def payer(self, total=1, items=None):
        return {
            "total": total, "quantidade_glosas": 2, "valor_total_glosado": "10.00",
            "valor_total_tratado": "4.00", "demonstrativos": ["D1"] if items else [],
            "items": items or [], "offset": 0, "limit": 10,
        }

    @patch("core.petrobras.api_get")
    @patch("core.views.api_get")
    def test_payer_cards_share_list_pagination_and_summary(self, views_api, payer_api):
        views_api.return_value = {"cards": [], "total": 0, "quantidade_glosas": 0}
        payer_api.return_value = self.payer(1, [petrobras_row(1, None)])
        response = self.client.get("/follow-up-glosas/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payer_api.call_args.kwargs["params"]["offset"], "0")
        self.assertEqual(payer_api.call_args.kwargs["params"]["limit"], "10")
        self.assertEqual(response.context["pagination"]["total"], 1)
        self.assertEqual(response.context["resumo"]["quantidade_glosas"], 2)
        self.assertEqual(response.context["resumo"]["valor_total_pendente"], 6.0)
        self.assertContains(response, "DEMONSTRATIVO")
        self.assertContains(response, "GUIA DO PRESTADOR")
        self.assertContains(response, "SENHA")
        self.assertContains(response, "B386348")
        self.assertContains(response, "data-petrobras-item=")
        self.assertContains(response, 'class="petrobras-item-action"')
        self.assertContains(response, "Oracle: Não localizado")
        self.assertContains(response, "Não informado")
        self.assertNotContains(response, 'id="petrobras-results"')
        self.assertNotContains(response, "hx-get=\"/follow-up-glosas/?detalhar_processo=D1")

    @patch("core.petrobras.api_get")
    @patch("core.views.api_get")
    def test_second_page_skips_standard_groups_already_shown(self, views_api, payer_api):
        views_api.return_value = {"cards": [], "total": 13, "limit": 10, "offset": 10}
        payer_api.return_value = self.payer(5)
        self.client.get("/follow-up-glosas/", {"page": 2})
        self.assertEqual(payer_api.call_args.kwargs["params"]["offset"], "0")
        views_api.return_value = {"cards": [], "total": 3, "limit": 10, "offset": 10}
        self.client.get("/follow-up-glosas/", {"page": 2})
        self.assertEqual(payer_api.call_args.kwargs["params"]["offset"], "7")

    @patch("core.petrobras.api_get")
    @patch("core.views.api_get")
    def test_other_convenio_does_not_query_payer(self, views_api, payer_api):
        views_api.return_value = {"cards": [], "total": 0}
        self.client.get("/follow-up-glosas/", {"convenio": "IPM"})
        payer_api.assert_not_called()

    @patch("core.petrobras.api_get", side_effect=ApiError("indisponível", 503))
    @patch("core.views.api_get")
    def test_payer_outage_keeps_standard_cards(self, views_api, payer_api):
        views_api.return_value = {"cards": [], "total": 0}
        response = self.client.get("/follow-up-glosas/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Demonstrativos Petrobras temporariamente indisponíveis")
