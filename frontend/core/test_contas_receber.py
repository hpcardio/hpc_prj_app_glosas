"""Contas a Receber · Fluxo do recebimento (entrega 1: leitura)."""
from unittest.mock import patch

from django.test import TestCase

from core import tests as existing_tests
from core.access import RESTRICTED_KEYS, allowed_screen_keys
from core.contas_receber import competencia_label, faixa_dias
from core.services import ApiError

URL = "/financeiro/contas-a-receber/fluxo/"


def payload():
    etapas = [
        {"chave": "remessa", "rotulo": "Remessa fechada no MV", "status": "concluida", "data": "2026-11-03"},
        {"chave": "protocolo", "rotulo": "Protocolada no convênio", "status": "concluida", "data": None},
        {"chave": "nfse", "rotulo": "NFS emitida", "status": "atual", "data": None},
        {"chave": "recebimento", "rotulo": "Recebimento financeiro", "status": "futura", "data": None},
    ]
    return {
        "resumo": {
            "etapas": [
                {"chave": "protocolo", "quantidade": 3, "valor": "300.00"},
                {"chave": "nfse", "quantidade": 1, "valor": "1771.52"},
                {"chave": "recebimento", "quantidade": 0, "valor": "0.00"},
                {"chave": "concluido", "quantidade": 0, "valor": "0.00"},
            ],
            "total_remessas": 4,
            "valor_total": "2071.52",
            "protocolos_a_confirmar": 2,
        },
        "itens": [
            {
                "cd_remessa": 20323, "cd_convenio": 46, "convenio": "ISSEC",
                "competencia": "2026-10-01", "valor": "1771.52", "qt_contas": 4,
                "dt_fechamento": "2026-11-03",
                "protocolo": {"id": None, "numero": "2600044294", "origem": "portal", "situacao": "automatico",
                              "valor_cobrado": "1771.52", "valor_liberado": "1190.47",
                              "data_protocolo": None, "solicitacao_nota_em": None},
                "protocolo_candidatos": [], "nfse": None, "recebimentos": [], "valor_recebido": "0.00",
                "etapa_atual": "nfse", "pendencia": "Aguardando NFS", "etapas": etapas,
                "dias_na_etapa": 21, "solicitacao_nota": False,
            },
            {
                "cd_remessa": 20111, "cd_convenio": 46, "convenio": "ISSEC",
                "competencia": "2026-09-01", "valor": "300.00", "qt_contas": 1,
                "dt_fechamento": "2026-10-03", "protocolo": None,
                "protocolo_candidatos": [
                    {"numero": "2600037001", "valor_cobrado": "300.00", "valor_liberado": None},
                    {"numero": "2600037002", "valor_cobrado": "300.00", "valor_liberado": "300.00"},
                ],
                "nfse": None, "recebimentos": [], "valor_recebido": "0.00",
                "etapa_atual": "protocolo", "pendencia": "Aguardando protocolo", "etapas": etapas,
                "dias_na_etapa": 52, "solicitacao_nota": False,
            },
        ],
        "total": 4, "total_pages": 1, "page": 1, "page_size": 10,
        "valor_total_filtrado": "2071.52",
        "convenios": [{"codigo": 46, "nome": "ISSEC", "remessas": 4}],
    }


class FluxoTests(TestCase):
    def setUp(self):
        existing_tests.ContasPagarTests.setUp(self)
        session = self.client.session
        session["api_user"] = {**session["api_user"], "telas_permitidas": ["contas_receber_fluxo"], "perfil": "usuario"}
        session.save()

    @patch("core.contas_receber.api_get")
    def test_funil_cards_e_linha_do_tempo(self, api_get):
        api_get.return_value = payload()

        response = self.client.get(URL, {"convenio": "46", "competencia_de": "2026-01"})

        self.assertEqual(response.status_code, 200)
        params = api_get.call_args.args[1]
        self.assertEqual(params["convenio"], "46")
        self.assertEqual(params["competencia_de"], "2026-01")
        self.assertEqual(params["page_size"], 10)
        self.assertContains(response, "Onde está o dinheiro dos convênios?")
        self.assertContains(response, "Aguardando protocolo")
        self.assertContains(response, "etapa=nfse")
        self.assertContains(response, "75% das remessas")
        self.assertContains(response, "2600044294")
        self.assertContains(response, 'cr-tag--auto')
        self.assertContains(response, "out/2026")
        self.assertContains(response, "cr-dias--atencao")
        self.assertContains(response, "cr-dias--critico")
        self.assertContains(response, "2 sugestões")
        self.assertContains(response, "2600037002")
        self.assertContains(response, "<b>2</b> remessas com mais de um protocolo")
        self.assertContains(response, "GLOSADO NO PORTAL")
        self.assertContains(response, "cr-passo--atual")

    @patch("core.contas_receber.api_get")
    def test_card_ativo_remove_o_proprio_filtro(self, api_get):
        api_get.return_value = payload()

        response = self.client.get(URL, {"etapa": "nfse", "convenio": "46"})

        self.assertEqual(api_get.call_args.args[1]["etapa"], "nfse")
        cards = {c["chave"]: c for c in response.context["funil"]}
        self.assertTrue(cards["nfse"]["ativo"])
        self.assertEqual(cards["nfse"]["url"], "?convenio=46")
        self.assertIn("etapa=protocolo", cards["protocolo"]["url"])

    @patch("core.contas_receber.api_get")
    def test_filtro_a_confirmar_e_parametros_invalidos(self, api_get):
        api_get.return_value = payload()

        self.client.get(URL, {"a_confirmar": "1", "etapa": "xpto", "ordenar_por": "drop", "convenio": "abc"})

        params = api_get.call_args.args[1]
        self.assertEqual(params["a_confirmar"], "true")
        self.assertNotIn("etapa", params)
        self.assertNotIn("ordenar_por", params)
        self.assertNotIn("convenio", params)

    @patch("core.contas_receber.api_get")
    def test_erro_da_api_mostra_mensagem(self, api_get):
        api_get.side_effect = ApiError("MV indisponível")
        response = self.client.get(URL)
        self.assertContains(response, "Não foi possível carregar o fluxo do Contas a Receber")
        self.assertContains(response, "Nenhuma remessa nesta seleção.")

    def test_tela_restrita_e_rotulos(self):
        self.assertIn("contas_receber_fluxo", RESTRICTED_KEYS)
        self.assertNotIn("contas_receber_fluxo", allowed_screen_keys({"telas_permitidas": None}))
        self.assertEqual(competencia_label("2026-10-01"), "out/2026")
        self.assertEqual([faixa_dias(d) for d in (None, 3, 30, 90)], ["", "ok", "atencao", "critico"])


class FluxoSemPermissaoTests(TestCase):
    setUp = existing_tests.ContasPagarTests.setUp

    def test_usuario_sem_a_tela_nao_entra(self):
        session = self.client.session
        session["api_user"] = {**session["api_user"], "telas_permitidas": ["follow_up_glosas"], "perfil": "usuario"}
        session.save()
        response = self.client.get(URL)
        self.assertNotEqual(response.status_code, 200)
