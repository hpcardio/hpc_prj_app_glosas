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
                {"chave": "nfs_autorizada", "quantidade": 1, "valor": "1771.52"},
                {"chave": "nao_autorizada", "quantidade": 0, "valor": "0.00"},
                {"chave": "aguardando_recebimento", "quantidade": 0, "valor": "0.00"},
                {"chave": "recebido_sem_conciliacao", "quantidade": 0, "valor": "0.00"},
                {"chave": "recebido", "quantidade": 0, "valor": "0.00"},
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
                              "valor_cobrado": "1771.52", "valor_liberado": "1190.47", "situacao_portal": "Em Análise",
                              "data_protocolo": None, "solicitacao_nota_em": None},
                "protocolo_candidatos": [], "nfse": None, "recebimentos": [], "valor_recebido": "0.00",
                "etapa_atual": "nfse", "situacao_fluxo": "nfs_autorizada", "pendencia": "NFS autorizadas",
                "nota_portal": {"situacao": "pendente", "nf_a_enviar": True, "valor": "1190.47", "nup": "G-77",
                                "pago_desde": None},
                "etapas": etapas,
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
                "etapa_atual": "protocolo", "situacao_fluxo": "protocolo", "pendencia": "Aguardando protocolo",
                "etapas": etapas,
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
        self.assertContains(response, "etapa=nfs_autorizada")
        self.assertEqual([c["chave"] for c in response.context["funil"]], [
            "protocolo", "nfs_autorizada", "nao_autorizada", "aguardando_recebimento",
            "recebido_sem_conciliacao", "recebido"])
        self.assertContains(response, "Recebidas s/ conciliação bancária")
        self.assertContains(response, "NFS não autorizadas")
        self.assertContains(response, "NFS autorizadas")
        self.assertContains(response, "cr-card--nfs_autorizada")
        self.assertContains(response, "portal: pede a NF (ENVIAR)")
        self.assertContains(response, "Pendente · ENVIAR a NF")
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
        self.assertContains(response, "SITUAÇÃO NO PORTAL")
        self.assertContains(response, "Em Análise")
        self.assertContains(response, "cr-passo--atual")

    @patch("core.contas_receber.api_get")
    def test_card_ativo_remove_o_proprio_filtro(self, api_get):
        api_get.return_value = payload()

        response = self.client.get(URL, {"etapa": "nao_autorizada", "convenio": "46"})

        self.assertEqual(api_get.call_args.args[1]["etapa"], "nao_autorizada")
        cards = {c["chave"]: c for c in response.context["funil"]}
        self.assertTrue(cards["nao_autorizada"]["ativo"])
        self.assertEqual(cards["nao_autorizada"]["url"], "?convenio=46")
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
            "valor": "7190.00", "etapa_atual": "recebimento", "situacao_fluxo": "aguardando_recebimento",
        })
        api_get.return_value = dados

        response = self.client.get(URL, {"convenio": "46", "etapa": "aguardando_recebimento", "export": "xlsx"})

        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment;", response["Content-Disposition"])
        params = api_get.call_args.args[1]
        self.assertEqual(params["exportar"], "true")
        self.assertEqual(params["convenio"], "46")
        self.assertEqual(params["etapa"], "aguardando_recebimento")
        self.assertNotIn("page", params)
        sheet = load_workbook(BytesIO(response.content))["BASE"]
        self.assertEqual(
            HEADERS[:6], ("Convênio", "Remessa", "Competência", "Data Entrega", "Valor Total", "Dias"))
        self.assertEqual(HEADERS[19:23], ("Ano2", "mês", "ano prev.recb", "mês prev.rcbto"))
        self.assertEqual(HEADERS[23:], ("SITUAÇÃO", "PARADA HÁ (DIAS)", "SITUAÇÃO NO PORTAL",
                                        "PROTOCOLO A CONFERIR", "VALORES ESTIMADOS"))
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
        self.assertEqual(com_nf["total"], Decimal("562.69"))          # valor da remessa no MV
        self.assertEqual(com_nf["glosas"], Decimal("159.96"))
        self.assertEqual(com_nf["valor_bruto"], Decimal("402.73"))    # total − glosas
        self.assertEqual(com_nf["retencoes"], Decimal("35.64"))
        self.assertEqual(com_nf["liquido"], Decimal("367.09"))
        self.assertEqual(com_nf["origem_glosa"], "nf")

        pelo_portal = valores_do_card({
            "valor": "1771.52",
            "protocolo": {"glosa_portal": Decimal("581.05")},
        })
        self.assertEqual(pelo_portal["glosas"], Decimal("581.05"))
        self.assertEqual(pelo_portal["origem_glosa"], "portal")
        self.assertEqual(pelo_portal["valor_bruto"], Decimal("1190.47"))
        self.assertIsNone(pelo_portal["retencoes"])
        self.assertIsNone(pelo_portal["liquido"])

        nf_fora_da_base = valores_do_card({"valor": "100", "nfse": {"valor": None}})
        self.assertIsNone(nf_fora_da_base["glosas"])
        self.assertIsNone(nf_fora_da_base["retencoes"])
        self.assertIsNone(nf_fora_da_base["valor_bruto"])

    @patch("core.contas_receber.api_get")
    def test_card_mostra_os_quatro_valores(self, api_get):
        dados = payload()
        dados["itens"][0]["nfse"] = {"numero": "25618", "valor": "1500.00", "valor_liquido": "1400.00",
                                     "data_emissao": "2026-02-12", "origem": "planilha"}
        api_get.return_value = dados
        response = self.client.get(URL)
        html = response.content.decode()
        # Ordem lógica: total → glosas → bruto → impostos → líquido.
        rotulos = ("VALOR TOTAL</small>", "GLOSAS</small>", "VALOR BRUTO</small>", "IMPOSTOS</small>", "VALOR LÍQUIDO</small>")
        posicoes = [html.index(r) for r in rotulos]
        self.assertEqual(posicoes, sorted(posicoes))
        self.assertContains(response, "R$ 1.771,52")  # total
        self.assertContains(response, "R$ 271,52")    # glosas: 1.771,52 − 1.500,00
        self.assertContains(response, "R$ 1.500,00")  # bruto
        self.assertContains(response, "R$ 100,00")    # impostos
        self.assertContains(response, "R$ 1.400,00")  # líquido = bruto − impostos


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

    def test_planilha_marca_pago_quando_pago_no_portal(self):
        from datetime import date

        from core.contas_receber_export import _contexto

        hoje = date(2026, 11, 25)
        pago = _contexto({"valor": "10", "situacao_fluxo": "recebido_sem_conciliacao"}, hoje)
        self.assertEqual((pago["status"], pago["status2"]), ("1-REALIZADO", "PAGO"))
        pendente = _contexto({"valor": "10", "situacao_fluxo": "aguardando_recebimento", "nfse": {"numero": "1"}}, hoje)
        self.assertEqual(pendente["status2"], "NF EMITIDA")


class SugestaoPlanilhaTests(TestCase):
    def setUp(self):
        FluxoTests.setUp(self)

    @patch("core.contas_receber.api_get")
    def test_protocolo_da_planilha_nao_confirmado_vira_sugestao(self, api_get):
        dados = payload()
        dados["itens"][1].update({
            "protocolo_candidatos": [],
            "protocolo_planilha": {"numero": "2600035769", "origem": "planilha",
                                   "motivo": "Protocolo não encontrado no portal do convênio",
                                   "valor_cobrado": None, "competencia_portal": None},
        })
        api_get.return_value = dados

        response = self.client.get(URL)

        self.assertContains(response, "Planilha DEMO: 2600035769")
        self.assertContains(response, "A planilha DEMO indica o protocolo <b>2600035769</b>")
        self.assertContains(response, "protocolo não encontrado no portal do convênio")
        self.assertContains(response, "Confirmar protocolo 2600035769")
        self.assertContains(response, "Planilha: Nº 2600035769 a conferir")
        html = response.content.decode()
        bloco = html[html.index("Confirmar protocolo 2600035769") - 600:html.index("Confirmar protocolo 2600035769")]
        self.assertNotIn("confirmado_do_portal", bloco)

    @patch("core.contas_receber.api_put")
    @patch("core.contas_receber.api_get")
    def test_confirmar_sugestao_da_planilha_grava_manual(self, api_get, api_put):
        api_get.return_value = payload()
        self.client.post(URL, {"acao": "protocolo_salvar", "cd_remessa": "20111", "numero": "2600035769"})
        self.assertFalse(api_put.call_args.args[1]["confirmado_do_portal"])


class OrigemNupGrupoTests(TestCase):
    def test_rotulo_da_nf_do_grupo(self):
        from core.contas_receber import ORIGENS

        self.assertIn("NUP do processo agrupador", ORIGENS["nup_grupo"])


class ExplicacaoRetencaoTests(TestCase):
    def test_title_mostra_os_impostos_da_configuracao(self):
        from core.contas_receber import valores_do_card

        grupo = valores_do_card({
            "valor": "1000.00",
            "nfse": {"numero": "9", "valor": "5000.00", "valor_liquido": "4900.00"},
            "nf_rateio": {"compartilhada": True, "remessas_na_nf": 2, "valor": "1000.00",
                          "retencoes": "88.50", "liquido": "911.50", "aliquota": "8.85",
                          "fonte_aliquota": "configuracao",
                          "retencoes_detalhe": {"iss": "30.00", "ir": "12.00", "pis": "6.50",
                                                "cofins": "30.00", "csll": "10.00"}},
        })
        self.assertIn("8,85% (percentuais da Configuração por Convênio)", grupo["explicacao_retencao"])
        self.assertIn("ISS R$ 30,00 · IR R$ 12,00 · PIS R$ 6,50", grupo["explicacao_retencao"])

        estimado = valores_do_card({
            "valor": "1000.00",
            "estimativa": {"aliquota": "4.20", "fonte_aliquota": "configuracao", "origem_base": "bruto",
                           "retencoes": "42.00", "liquido": "958.00",
                           "retencoes_detalhe": {"iss": "30.00", "ir": "12.00"}},
        })
        self.assertIn("4,20% da Configuração por Convênio", estimado["explicacao_estimativa"])
        self.assertIn("(ISS R$ 30,00 · IR R$ 12,00)", estimado["explicacao_estimativa"])


class VinculosManuaisTests(TestCase):
    """Entrega 2: protocolo, solicitação de nota e NFS pela tela."""

    def setUp(self):
        session = self.client.session
        session["api_access_token"] = "token"
        session["api_user"] = {"id": 7, "nome": "Financeiro", "perfil": "usuario", "telas_permitidas": ["contas_receber_fluxo"]}
        session.save()

    @patch("core.contas_receber.api_get", return_value=payload())
    def test_card_mostra_acoes_de_vinculo(self, _):
        html = self.client.get(URL + "?aberta=20323").content.decode()

        self.assertIn('id="remessa-20323"', html)
        self.assertIn("Confirmar protocolo 2600044294", html)              # sugerido pelo portal
        self.assertIn('name="protocolo_numero" value="2600044294"', html)  # solicitação/NF gravam o protocolo junto
        self.assertIn("Solicitação de nota pelo convênio", html)
        self.assertIn("Pesquisar a NFS para vincular", html)
        self.assertEqual(html.count('value="protocolo_salvar"') >= 3, True)  # confirmar candidatos da 20111 + formulários

    @patch("core.contas_receber.api_put", return_value={})
    def test_confirmar_protocolo_do_portal(self, api_put):
        response = self.client.post(URL, {
            "acao": "protocolo_salvar", "cd_remessa": "20323", "numero": " 2600044294 ",
            "confirmado_do_portal": "1", "convenio": "46", "page": "2",
        })

        api_put.assert_called_once_with("/app_glosas/financeiro/contas-a-receber/remessas/20323/protocolo", {
            "numero": "2600044294", "data_protocolo": None, "confirmado_do_portal": True,
        })
        self.assertRedirects(response, URL + "?convenio=46&page=2&aberta=20323#remessa-20323", fetch_redirect_response=False)

    @patch("core.contas_receber.api_put", return_value={})
    def test_solicitacao_e_nfse_levam_o_protocolo_sugerido(self, api_put):
        self.client.post(URL, {"acao": "solicitacao_salvar", "cd_remessa": "20323", "data": "2026-11-20",
                               "observacao": " por e-mail ", "protocolo_numero": "2600044294"})
        api_put.assert_called_with("/app_glosas/financeiro/contas-a-receber/remessas/20323/solicitacao-nota", {
            "data": "2026-11-20", "observacao": "por e-mail", "protocolo_numero": "2600044294"})

        self.client.post(URL, {"acao": "nfse_vincular", "cd_remessa": "20323", "prestador_cnpj": "08.711.085/0001-28",
                               "numero_nfse": "30083", "protocolo_numero": "2600044294"})
        api_put.assert_called_with("/app_glosas/financeiro/contas-a-receber/remessas/20323/nfse", {
            "prestador_cnpj": "08711085000128", "numero_nfse": "30083", "protocolo_numero": "2600044294"})

    @patch("core.contas_receber.api_delete", return_value=None)
    def test_remover_vinculos(self, api_delete):
        for acao, caminho in (("protocolo_remover", "protocolo"), ("solicitacao_remover", "solicitacao-nota"), ("nfse_remover", "nfse")):
            self.client.post(URL, {"acao": acao, "cd_remessa": "20323"})
            api_delete.assert_called_with(f"/app_glosas/financeiro/contas-a-receber/remessas/20323/{caminho}")

    @patch("core.contas_receber.api_put", side_effect=ApiError('{"detail":"Vincule o protocolo da remessa antes."}', 409))
    def test_erro_da_api_vira_mensagem(self, _):
        response = self.client.post(URL, {"acao": "solicitacao_salvar", "cd_remessa": "20323", "data": "2026-11-20"}, follow=True)
        self.assertContains(response, "Vincule o protocolo da remessa antes.")

    def test_acao_invalida(self):
        response = self.client.post(URL, {"acao": "apagar_tudo", "cd_remessa": "20323"}, follow=True)
        self.assertContains(response, "Ação inválida.")

    @patch("core.contas_receber.api_get")
    def test_pesquisa_de_nfse_no_card(self, api_get):
        notas = {"notas": [{"prestador_cnpj": "08711085000128", "numero_nfse": "30083", "tomador": "FASSEC",
                            "data_emissao": "2026-11-10", "valor": "572156.82", "valor_liquido": "548126.24",
                            "discriminacao": "Serviços", "protocolos_vinculados": ["2600033103", "2600033215"]}]}
        api_get.side_effect = lambda path, params=None, **kw: notas if path.endswith("/nfse") else payload()

        html = self.client.get(URL + "?aberta=20323&nf_q=30083").content.decode()

        api_get.assert_any_call("/app_glosas/financeiro/contas-a-receber/nfse", {"q": "30083"}, timeout=60)
        self.assertIn("2 protocolos", html)
        self.assertIn('name="numero_nfse" value="30083"', html)


class ExportacaoIgualATelaTests(TestCase):
    def setUp(self):
        FluxoTests.setUp(self)

    @patch("core.contas_receber.api_get")
    def test_planilha_tem_os_valores_e_a_situacao_do_card(self, api_get):
        from decimal import Decimal
        from io import BytesIO

        from openpyxl import load_workbook

        from core.contas_receber import valores_do_card
        from core.contas_receber_export import HEADERS

        dados = payload()
        item = dados["itens"][0]
        item.update({
            "estimativa": {"aliquota": "4.20", "fonte_aliquota": "configuracao", "origem_base": "liberado",
                           "base": "1190.47", "retencoes": "50.00", "liquido": "1140.47"},
            "protocolo_planilha": None,
        })
        dados["itens"][1]["protocolo_planilha"] = {"numero": "2600035769", "motivo": "x"}
        api_get.return_value = dados

        response = self.client.get(URL, {"convenio": "46", "export": "xlsx"})

        sheet = load_workbook(BytesIO(response.content))["BASE"]
        linhas = [{h: c.value for h, c in zip(HEADERS, row)} for row in sheet.iter_rows(min_row=2)]
        primeira = next(l for l in linhas if l["Remessa"] == 20323)
        card = valores_do_card({**item, "protocolo": {**item["protocolo"], "glosa_portal": Decimal("581.05")}})
        self.assertEqual(Decimal(str(primeira["VLR BRUTO"])), card["valor_bruto"])
        self.assertEqual(Decimal(str(primeira["IMPOSTOS"])), Decimal("50.00"))
        self.assertEqual(Decimal(str(primeira["VLR LIQ NF"])), Decimal("1140.47"))
        self.assertEqual(primeira["VALORES ESTIMADOS"], "SIM")
        self.assertEqual(primeira["SITUAÇÃO"], "NFS autorizadas")
        self.assertEqual(primeira["PARADA HÁ (DIAS)"], 21)
        self.assertEqual(primeira["SITUAÇÃO NO PORTAL"], "Pendente · ENVIAR a NF")
        segunda = next(l for l in linhas if l["Remessa"] == 20111)
        self.assertEqual(segunda["PROTOCOLO A CONFERIR"], 2600035769)
