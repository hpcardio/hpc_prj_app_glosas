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
    def test_portal_detail_is_proxied_for_writers_only(self, api):
        api.return_value = Mock(status_code=200, content=b"{}", headers={})
        self.assertEqual(petrobras.bridge(self.request("post"), "portal/detalhar").status_code, 200)
        self.assertEqual(api.call_args.args[:2], ("POST", "/app_glosas/petrobras/portal/detalhar"))
        req = self.request("post")
        req.session["api_user"]["perfil"] = "leitura"
        self.assertEqual(petrobras.bridge(req, "portal/detalhar").status_code, 403)
        self.assertEqual(petrobras.bridge(self.request(), "portal/detalhar").status_code, 404)
        from pathlib import Path
        from django.conf import settings
        base = Path(settings.BASE_DIR)
        html = (base / "templates/partials/petrobras_followup.html").read_text()
        js = (base / "static/js/petrobras-followup.js").read_text()
        self.assertIn('data-action="portal-detail"', html)
        self.assertIn("api('portal/detalhar','POST',{})", js)

    def test_send_respects_pilot_user_and_item_block(self):
        from pathlib import Path
        from django.conf import settings
        js = (Path(settings.BASE_DIR) / "static/js/petrobras-followup.js").read_text()
        self.assertIn("!config.transmissao_usuario||Boolean(row.bloqueio_envio)", js)
        self.assertIn("el('p',row.bloqueio_envio,sendBox,'petrobras-warning')", js)
        self.assertIn("Envio de recurso em piloto; seu usuário ainda não foi liberado.", js)
        # Proofs of an appeal are TISS naturezaGuia 3; the API warns on unsent ones.
        self.assertIn("nature=select(upload,'Natureza da guia',[['3','Recurso de glosa']]);", js)
        self.assertIn("${x.aviso?' · '+x.aviso:''}", js)
        # Portal fallback: saved appeals stay visibly unsent until Efetivar.
        self.assertIn("salvo_portal:'Salvo no portal, não enviado'", js)
        self.assertIn("api(`glosas/${id}/efetivar-portal`,'POST',{ciente_nao_recursados:true})", js)
        self.assertIn('Estou ciente de que ao clicar em "Efetivar/Enviar Recursos Salvos" os itens NÃO RECURSADOS SERÃO MARCADOS COMO ACEITOS!!!', js)
        self.assertIn("ciente.addEventListener('change',()=>{send.disabled=!ciente.checked;});", js)

    @patch("core.petrobras.api_request")
    def test_batch_send_routes_and_panel(self, api):
        api.return_value = Mock(status_code=200, content=b"{}", headers={})
        self.assertEqual(petrobras.bridge(self.request(), "glosas/prontos").status_code, 200)
        self.assertEqual(petrobras.bridge(self.request("post"), "glosas/enviar-lote").status_code, 200)
        self.assertEqual(api.call_args.args[:2], ("POST", "/app_glosas/petrobras/glosas/enviar-lote"))
        self.assertEqual(petrobras.bridge(self.request("post"), "glosas/1/efetivar-portal").status_code, 200)
        self.assertEqual(api.call_args.args[:2], ("POST", "/app_glosas/petrobras/glosas/1/efetivar-portal"))
        self.assertEqual(petrobras.bridge(self.request(), "glosas/1/efetivar-portal").status_code, 200)
        self.assertEqual(api.call_args.args[:2], ("GET", "/app_glosas/petrobras/glosas/1/efetivar-portal"))
        req = self.request("post")
        req.session["api_user"]["perfil"] = "leitura"
        self.assertEqual(petrobras.bridge(req, "glosas/enviar-lote").status_code, 403)
        from pathlib import Path
        from django.conf import settings
        base = Path(settings.BASE_DIR)
        html = (base / "templates/partials/petrobras_followup.html").read_text()
        js = (base / "static/js/petrobras-followup.js").read_text()
        self.assertIn('data-action="batch-list"', html)
        self.assertIn('data-role="batch"', html)
        self.assertIn("api('glosas/prontos')", js)
        self.assertIn("api('glosas/enviar-lote','POST',{itens,confirmar_envio:true})", js)
        self.assertIn("Transmitir por protocolo", js)
        self.assertIn("(r.recursos||[]).forEach", js)

    @patch("core.petrobras.api_request")
    def test_internal_acato_is_proxied(self, api):
        api.return_value = Mock(status_code=200, content=b"{}", headers={})
        req = self.request("put", {"data": "2026-11-02", "quantidade": "1", "valor": "1.00"})
        self.assertEqual(petrobras.bridge(req, "glosas/7/acato").status_code, 200)
        self.assertEqual(api.call_args.args[:2], ("PUT", "/app_glosas/petrobras/glosas/7/acato"))
        self.assertEqual(api.call_args.kwargs["json"]["valor"], "1.00")
        self.assertEqual(petrobras.bridge(self.request("delete"), "glosas/7/acato").status_code, 200)
        self.assertEqual(api.call_args.args[:2], ("DELETE", "/app_glosas/petrobras/glosas/7/acato"))
        self.assertNotIn("json", api.call_args.kwargs)
        self.assertEqual(petrobras.bridge(self.request("delete"), "glosas/7/rascunho").status_code, 404)

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

    def test_guide_senha_fills_reissue_without_senha(self):
        row = petrobras_row(1, None, senha=None)
        row["senha_guia"] = "B542433"
        card = petrobras.follow_up_cards({"demonstrativos": ["D1"], "items": [row]})[0]
        self.assertEqual(card["pacientes"][0]["itens"][0]["senha"], "B542433")

    def test_internal_acato_counts_as_treated(self):
        row = petrobras_row(1, ORACLE)
        row["acato"] = {"valor": "0.01", "data": "2026-11-02"}
        row["valor_tratado"] = "0.01"
        cards = petrobras.follow_up_cards({"demonstrativos": ["D1"], "items": [row]})
        self.assertEqual(str(cards[0]["valor_total_tratado"]), "0.01")

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


class PetrobrasGlosaPorGuiaTests(SimpleTestCase):
    """Payment demonstrativo glosa: one row per guide, no items or reasons."""

    def guide_row(self, oracle):
        row = petrobras_row(
            1,
            oracle,
            demonstrativo="1544637",
            carteira=None,
            descricao="Glosa da guia (itens não detalhados)",
            escopo="guia_pagamento",
            senha="B507714",
            valor_informado=None,
            valor_processado="1029.50",
            valor_liberado="948.14",
            motivos=[],
            cobertura="guia_sem_itens",
        )
        row["guia_prestador"] = "B507714"
        row["tuss"] = None
        row["valor_glosa"] = "81.36"
        return row

    def test_card_flags_guide_glosa_and_uses_mv_account(self):
        oracle = {**ORACLE, "nr_carteira": "000000000123", "valor_mv": "1029.50"}
        oracle.pop("descricao")
        card = petrobras.follow_up_cards(
            {"demonstrativos": ["1544637"], "items": [self.guide_row(oracle)]}
        )[0]
        self.assertEqual(card["processo"]["status_processo"], "Glosa por guia")
        item = card["pacientes"][0]["itens"][0]
        self.assertEqual(item["descricao"], "Glosa da guia (itens não detalhados)")
        self.assertEqual(item["senha"], "B507714")
        self.assertEqual(str(item["vl_total_conta"]), "1029.50")
        self.assertEqual(str(item["valor_glosa"]), "81.36")
        self.assertEqual(item["cd_atendimento"], 789)
        extras = petrobras.follow_up_campos_extras([{
            "origem": "petrobras",
            "atendimentos_paciente": [
                {"grupos_procedimento": [{"itens": [item]}]}
            ],
        }])
        atendimento = extras[0]["atendimentos_paciente"][0]
        self.assertEqual(atendimento["carteira"], "000000000123")
        self.assertEqual(atendimento["campos_extras"], [("GUIA", "B507714")])

    def test_guide_lists_mv_items_as_reference(self):
        oracle = {
            **ORACLE,
            "itens": [
                {"cd_pro_fat": "10101012", "cd_tuss": None, "descricao": "CONSULTA",
                 "ds_gru_fat": "HONORARIOS", "quantidade": "1", "valor": "1000.00"},
                {"cd_pro_fat": "90000001", "cd_tuss": "40301630", "descricao": "EXAME",
                 "ds_gru_fat": None, "quantidade": "2", "valor": "29.50"},
            ],
        }
        card = petrobras.follow_up_cards(
            {"demonstrativos": ["1544637"], "items": [self.guide_row(oracle)]}
        )[0]
        item = card["pacientes"][0]["itens"][0]
        self.assertEqual([i["codigo"] for i in item["itens_mv"]], ["10101012", "40301630"])
        self.assertEqual(str(item["itens_mv"][1]["valor"]), "29.50")
        self.assertNotIn("valor_glosa", item["itens_mv"][0])
        # The guide glosa stays a single treatable row.
        self.assertEqual(len(card["pacientes"][0]["itens"]), 1)
        from pathlib import Path
        from django.conf import settings
        html = (Path(settings.BASE_DIR) / "templates/follow_up_glosas.html").read_text()
        self.assertIn("{% for mv in item.itens_mv %}", html)
        self.assertIn("a Petrobras não informa qual item foi glosado", html)

    def test_item_glosa_has_no_mv_reference_list(self):
        item = petrobras.follow_up_cards(
            {"demonstrativos": ["D1"], "items": [petrobras_row(oracle=ORACLE)]}
        )[0]["pacientes"][0]["itens"][0]
        self.assertEqual(item["itens_mv"], [])

    def test_patient_name_comes_from_any_linked_item(self):
        first = petrobras_row(1, None, demonstrativo="D1")
        linked = petrobras_row(2, ORACLE, demonstrativo="D1")
        card = petrobras.follow_up_cards(
            {"demonstrativos": ["D1"], "items": [first, linked]}
        )[0]
        self.assertEqual(len(card["pacientes"]), 1)
        self.assertEqual(card["pacientes"][0]["nm_paciente"], "Paciente Sintético")
        self.assertEqual(card["pacientes"][0]["codigo_paciente"], 51)

    def test_unmatched_guide_keeps_values_unknown(self):
        card = petrobras.follow_up_cards(
            {"demonstrativos": ["1544637"], "items": [self.guide_row(None)]}
        )[0]
        item = card["pacientes"][0]["itens"][0]
        self.assertIsNone(item["vl_total_conta"])
        self.assertEqual(card["pacientes"][0]["nm_paciente"], "Não informado")

    def test_modal_explains_guide_without_items(self):
        from pathlib import Path
        from django.conf import settings
        js = (Path(settings.BASE_DIR) / "static/js/petrobras-followup.js").read_text()
        self.assertIn("'Dados da guia'", js)
        self.assertIn("fonte.escopo === 'guia_pagamento'", js)
        self.assertIn("A operadora não detalhou os motivos", js)
        for label in ["Remessa MV", "Data de atendimento", "Itens no MV", "Valor no MV", "Valor processado", "Data de pagamento"]:
            self.assertIn(f"['{label}',", js)


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
    def test_transmit_filled_buttons_on_payer_cards(self, views_api, payer_api):
        views_api.return_value = {"cards": [], "total": 0}
        payer_api.return_value = self.payer(1, [petrobras_row(7, None)])
        response = self.client.get("/follow-up-glosas/")
        self.assertContains(response, 'data-petrobras-batch disabled>Transmitir recursos preenchidos</button>')
        self.assertContains(response, 'data-petrobras-batch="7 ">Transmitir preenchidos desta guia</button>')
        self.assertContains(response, 'id="petrobras-batch-dialog"')
        # Standard payers only: no Petrobras transmit button.
        payer_api.return_value = self.payer(0, [])
        views_api.return_value = {"cards": [], "total": 0}
        self.assertNotContains(self.client.get("/follow-up-glosas/"), "data-petrobras-batch")

    @patch("core.petrobras.api_get")
    @patch("core.views.api_get")
    def test_protocolo_petro_filters_only_payer_records(self, views_api, payer_api):
        payer_api.return_value = self.payer(1, [petrobras_row(1, None)])
        response = self.client.get("/follow-up-glosas/?protocolo_petro=13827422")
        self.assertEqual(response.status_code, 200)
        views_api.assert_not_called()
        self.assertEqual(payer_api.call_args.kwargs["params"]["protocolo"], "13827422")
        self.assertEqual(response.context["pagination"]["total"], 1)
        self.assertContains(response, 'name="protocolo_petro" value="13827422"')
        self.assertContains(response, "Protocolo Petro")
        # Without the payer filter the standard search keeps running.
        views_api.return_value = {"cards": [], "total": 0}
        self.client.get("/follow-up-glosas/?numero_protocolo=1")
        self.assertNotIn("protocolo_petro", views_api.call_args.kwargs["params"])

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
        self.assertContains(response, "<small>GUIA</small>")
        self.assertNotContains(response, "GUIA DO PRESTADOR")
        self.assertNotContains(response, "<small>SENHA</small>")
        html = response.content.decode()
        self.assertLess(html.index("<small>CARTEIRA</small>"), html.index("<small>CÓD. ATENDIMENTO</small>"))
        self.assertContains(response, "B386348")
        self.assertContains(response, "data-petrobras-item=")
        self.assertContains(response, 'class="petrobras-item-action"')
        self.assertNotContains(response, "Oracle: Não localizado")
        self.assertNotContains(response, "Recurso: Rascunho")
        self.assertContains(response, ">+Acatar</button>")
        self.assertContains(response, "Conferir todos no MV Sistema")
        self.assertNotContains(response, "Conferir todos no Oracle")
        self.assertContains(response, "?v=20261103-recurso-protocolo")
        self.assertContains(response, "data-petrobras-acato=")
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


class PetrobrasModalScriptTests(SimpleTestCase):
    def test_only_chevron_expands_follow_up_cards(self):
        from pathlib import Path
        from django.conf import settings
        html = (Path(settings.BASE_DIR) / "templates/follow_up_glosas.html").read_text()
        for header in ("follow-up-glosa-card-header", "follow-up-glosa-remessa-header", "follow-up-glosa-patient-header"):
            self.assertNotIn(f'<button type="button" class="{header}', html)
            self.assertNotIn(f'class="{header}"\n            @click', html)
        self.assertEqual(html.count('class="follow-up-glosa-chevron"'), 3)
        for toggle in ('@click="open = !open"', '@click="toggleRemessa()"', '@click="patientOpen = !patientOpen"'):
            self.assertEqual(html.count(toggle), 1)
        css = (Path(settings.BASE_DIR) / "static/css/app.css").read_text()
        self.assertIn("/* Follow-Up: only the chevron expands; header data stays selectable. */", css)

    def test_dialogs_are_compact_with_side_by_side_forms(self):
        from pathlib import Path
        from django.conf import settings
        base = Path(settings.BASE_DIR)
        css = (base / "static/css/petrobras-followup.css").read_text()
        js = (base / "static/js/petrobras-followup.js").read_text()
        html = (base / "templates/partials/petrobras_followup.html").read_text()
        self.assertIn("/* Compact dialogs:", css)
        self.assertIn(".petrobras-dialog-body > .petrobras-half { grid-column: auto; }", css)
        for title in ["Justificativa e prazo", "Comprovantes", "Processo original", "Tratamento por acato"]:
            self.assertRegex(js, rf"section\(box,'{title}','[^']*petrobras-half'\)")
        self.assertIn("petrobras-followup.css' %}?v=20261103-recurso-protocolo", html)
        self.assertIn("petrobras-followup.js' %}?v=20261105-fallback-portal", html)

    def test_modal_shows_item_data_without_origin_split(self):
        from pathlib import Path
        from django.conf import settings
        js = (Path(settings.BASE_DIR) / "static/js/petrobras-followup.js").read_text()
        self.assertNotIn("Retorno parcial da operadora", js)
        self.assertNotIn("Conferência operadora x MV Sistema", js)
        self.assertIn("'Dados do item'", js)
        self.assertLess(js.index("reasons(box,row);"), js.index("details(box,row);"))
        for label in ["Paciente", "Atendimento", "Carteira", "Guia (senha)", "Lote", "Cód. do procedimento", "Procedimento", "Data de realização", "Quantidade", "Valor"]:
            self.assertIn(f"['{label}',", js)
