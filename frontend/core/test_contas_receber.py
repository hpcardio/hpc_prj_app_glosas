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


class ExportacaoTests(TestCase):
    setUp = FluxoTests.setUp

    @patch("core.contas_receber.api_get")
    def test_planilha_no_layout_da_demo_com_o_filtro_da_tela(self, api_get):
        from datetime import date
        from io import BytesIO

        from openpyxl import load_workbook

        from core.contas_receber_export import HEADERS

        dados = payload()
        dados["itens"][0].update({
            "dt_entrega": "2026-01-12", "dt_prevista_pgto": "2026-02-26",
            "nfse": {"numero": "25618", "data_emissao": "2026-02-12", "valor": "7190.00",
                     "valor_liquido": "6888.02", "origem": "planilha"},
            "valor": "7190.00", "etapa_atual": "recebimento",
        })
        api_get.return_value = dados

        response = self.client.get(URL, {"convenio": "46", "etapa": "recebimento", "export": "xlsx"})

        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment;", response["Content-Disposition"])
        params = api_get.call_args.args[1]
        self.assertEqual(params["exportar"], "true")
        self.assertEqual(params["convenio"], "46")
        self.assertEqual(params["etapa"], "recebimento")
        self.assertNotIn("page", params)
        sheet = load_workbook(BytesIO(response.content))["BASE"]
        self.assertEqual(
            HEADERS[:6], ("Convênio", "Remessa", "Competência", "Data Entrega", "Valor Total", "Dias"))
        self.assertEqual(HEADERS[-4:], ("Ano2", "mês", "ano prev.recb", "mês prev.rcbto"))
        self.assertEqual([c.value for c in sheet[1]], list(HEADERS))
        linha = {h: c.value for h, c in zip(HEADERS, sheet[2])}
        self.assertEqual(linha["Convênio"], "ISSEC")
        self.assertEqual(linha["Remessa"], 20323)
        self.assertEqual(linha["Competência"], "OUT.26")
        self.assertEqual(linha["Data Entrega"].date(), date(2026, 1, 12))
        self.assertEqual(linha["Status"], "VENCIDOS")
        self.assertEqual(linha["VLR BRUTO"], 7190)
        self.assertAlmostEqual(linha["IMPOSTOS"], 301.98)
        self.assertAlmostEqual(linha["VLR LIQ NF"], 6888.02)
        self.assertEqual(linha["GLOSA"], 0)
        self.assertEqual(linha["NF"], 25618)
        self.assertEqual(linha["PROCESSO"], 2600044294)
        self.assertEqual(linha["STATUS2"], "NF EMITIDA")
        self.assertEqual(linha["ano prev.recb"], 2026)
        self.assertEqual(linha["mês prev.rcbto"], "FEVEREIRO")
        self.assertEqual(linha["ANO"], "FEVEREIRO-2026")
        segunda = {h: c.value for h, c in zip(HEADERS, sheet[3])}
        self.assertEqual(segunda["PROCESSO TRUE"], "NÃO")
        self.assertIsNone(segunda["NF"])

    @patch("core.contas_receber.api_get")
    def test_botao_exportar_leva_os_filtros(self, api_get):
        api_get.return_value = payload()
        response = self.client.get(URL, {"convenio": "46", "q": "203"})
        self.assertIn("export=xlsx", response.context["export_url"])
        self.assertIn("convenio=46", response.context["export_url"])
        self.assertContains(response, "Exportar Excel")


class ValoresDoCardTests(TestCase):
    setUp = FluxoTests.setUp

    def test_bruto_glosas_retencoes_liquido(self):
        from decimal import Decimal

        from core.contas_receber import valores_do_card

        com_nf = valores_do_card({
            "valor": "562.69",
            "nfse": {"valor": "402.73", "valor_liquido": "367.09"},
        })
        self.assertEqual(com_nf["bruto"], Decimal("562.69"))
        self.assertEqual(com_nf["glosas"], Decimal("159.96"))
        self.assertEqual(com_nf["retencoes"], Decimal("35.64"))
        self.assertEqual(com_nf["liquido"], Decimal("367.09"))
        self.assertEqual(com_nf["origem_glosa"], "nf")

        pelo_portal = valores_do_card({
            "valor": "1771.52",
            "protocolo": {"glosa_portal": Decimal("581.05")},
        })
        self.assertEqual(pelo_portal["glosas"], Decimal("581.05"))
        self.assertEqual(pelo_portal["origem_glosa"], "portal")
        self.assertIsNone(pelo_portal["retencoes"])
        self.assertIsNone(pelo_portal["liquido"])

        nf_fora_da_base = valores_do_card({"valor": "100", "nfse": {"valor": None}})
        self.assertIsNone(nf_fora_da_base["glosas"])
        self.assertIsNone(nf_fora_da_base["retencoes"])

    @patch("core.contas_receber.api_get")
    def test_card_mostra_os_quatro_valores(self, api_get):
        dados = payload()
        dados["itens"][0]["nfse"] = {"numero": "25618", "valor": "1500.00", "valor_liquido": "1400.00",
                                     "data_emissao": "2026-02-12", "origem": "planilha"}
        api_get.return_value = dados
        response = self.client.get(URL)
        for rotulo in ("VALOR BRUTO", "GLOSAS", "RETENÇÕES", "VALOR LÍQUIDO"):
            self.assertContains(response, rotulo)
        self.assertContains(response, "R$ 271,52")   # 1.771,52 - 1.500,00
        self.assertContains(response, "R$ 100,00")   # retenções
        self.assertContains(response, "R$ 1.400,00")  # líquido


class RetencaoEstimadaTests(TestCase):
    setUp = FluxoTests.setUp

    def test_usa_estimativa_so_sem_nf_e_explica(self):
        from decimal import Decimal

        from core.contas_receber import valores_do_card

        estimado = valores_do_card({
            "valor": "1968.18",
            "protocolo": {"glosa_portal": Decimal("639.88")},
            "estimativa": {"aliquota": "4.20", "origem_base": "liberado",
                           "retencoes": "55.79", "liquido": "1272.51"},
        })
        self.assertTrue(estimado["estimado"])
        self.assertEqual(estimado["retencoes"], Decimal("55.79"))
        self.assertEqual(estimado["liquido"], Decimal("1272.51"))
        self.assertIn("4,20% retido nas NFs do convênio", estimado["explicacao_estimativa"])
        self.assertIn("liberado no portal", estimado["explicacao_estimativa"])

        real = valores_do_card({
            "valor": "7190.00",
            "nfse": {"valor": "7190.00", "valor_liquido": "6888.02"},
            "estimativa": None,
        })
        self.assertFalse(real["estimado"])
        self.assertEqual(real["retencoes"], Decimal("301.98"))

    @patch("core.contas_receber.api_get")
    def test_card_marca_valores_estimados(self, api_get):
        dados = payload()
        dados["itens"][0]["estimativa"] = {"aliquota": "4.20", "origem_base": "liberado",
                                           "retencoes": "50.00", "liquido": "1140.47"}
        api_get.return_value = dados
        response = self.client.get(URL)
        self.assertContains(response, "R$ 50,00")
        self.assertContains(response, "são estimados pela alíquota")
        self.assertContains(response, "is-estimado")


class NfDeGrupoTests(TestCase):
    setUp = FluxoTests.setUp

    def test_card_usa_a_parte_da_nf_de_grupo(self):
        from decimal import Decimal

        from core.contas_receber import valores_do_card

        item = {
            "valor": "2118.58",
            "nfse": {"numero": "30083", "valor": "572156.82", "valor_liquido": "548126.24"},
            "nf_rateio": {"compartilhada": True, "remessas_na_nf": 20, "valor": "2118.58",
                          "retencoes": "88.98", "liquido": "2029.60", "aliquota": "4.20"},
        }
        v = valores_do_card(item)
        self.assertEqual(v["glosas"], Decimal("0.00"))
        self.assertEqual(v["retencoes"], Decimal("88.98"))
        self.assertEqual(v["liquido"], Decimal("2029.60"))
        self.assertFalse(v["estimado"])

    def test_glosa_sem_nf_usa_a_parte_do_liberado(self):
        from decimal import Decimal

        from core.contas_receber import valores_do_card

        v = valores_do_card({
            "valor": "1200.00",
            "protocolo": {"glosa_portal": Decimal("0.00")},
            "estimativa": {"base": "1080.00", "origem_base": "liberado_proporcional",
                           "aliquota": "4.20", "retencoes": "45.36", "liquido": "1034.64"},
        })
        self.assertEqual(v["glosas"], Decimal("120.00"))

    def test_planilha_usa_a_parte_da_nf(self):
        from datetime import date

        from core.contas_receber_export import _contexto

        c = _contexto({
            "valor": "2118.58", "etapa_atual": "recebimento",
            "nfse": {"numero": "30083", "valor": "572156.82", "valor_liquido": "548126.24"},
            "nf_rateio": {"compartilhada": True, "valor": "2118.58", "liquido": "2029.60"},
        }, date(2026, 11, 25))
        self.assertEqual(str(c["bruto"]), "2118.58")
        self.assertEqual(str(c["impostos"]), "88.98")
        self.assertEqual(str(c["glosa"]), "0.00")
