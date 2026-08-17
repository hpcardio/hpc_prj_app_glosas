from datetime import date
from pathlib import Path
from threading import Barrier
from unittest.mock import Mock, call, patch

from django.contrib.staticfiles import finders
from django.core.cache import cache
from django.test import RequestFactory, TestCase

from core.access import SCREEN_KEYS
from core.services import ApiError
from core.views import (
    ACOMPANHAMENTO_PARTICULAR_CALENDARIO_SESSION_KEY,
    ACOMPANHAMENTO_BUCKETS,
    ACOMPANHAMENTO_GLOSAS_CACHE_KEY,
    apply_dashboard_filters,
    attach_registros_glosa,
    build_acompanhamento_cards,
    build_acompanhamento_rows,
    build_acompanhamento_resumo,
    build_api_cache_key,
    build_conciliacao_faturamento_payload,
    build_edicao_conciliacao_payload,
    build_recebimento_remessa_payload,
    build_dashboard_indicadores,
    build_geral_indicators,
    build_kanban_columns,
    build_recuperacao_indicators,
    CONCILIACAO_FATURAMENTO_PATH,
    contextualize_registro_glosa_error,
    DASHBOARD_GLOSAS_CACHE_KEY,
    ATENDIMENTO_NOTA_CACHE_NAMESPACE,
    disabled_convenio_ids,
    extract_api_error_message,
    get_dashboard_filters,
    group_follow_up_glosas_by_process,
    is_enabled_convenio_registro,
    is_recebido_registro,
    REQUISICOES_NOTA_PATH,
    subtract_months,
)


class ContaAtendimentoRegistroTests(TestCase):
    def _conta(self):
        return {
            'cd_remessa': 15588,
            'cd_atendimento': 265501,
            'cd_reg': 326615,
            'cd_pro_fat': '40304361',
            'nr_guia': 'G1',
        }

    def _registro(self, sn_glosado):
        return {
            'id': 77,
            'cd_remessa': 15588,
            'cd_atendimento': 265501,
            'conta': 326615,
            'procedimento': '40304361',
            'guia': 'G1',
            'sn_glosado': sn_glosado,
            'processo_controle_fatura_gab': '131313/2026',
            'processo_recurso': '16161616/2026',
            'data_glosa': '2026-07-03',
            'dt_pagamento': '2026-07-10',
            'dt_recurso': '2026-07-16',
            'motivo_glosa': '1016 - BENEFICIARIO COM ATENDIMENTO SUSPENSO',
            'qtd_glosada': 1,
            'valor_glosado': 31.92,
            'descricao_glosa': 'descricao teste',
        }

    @patch('core.views.get_cached_api_payload')
    def test_recusa_nao_preenche_dados_do_acato(self, get_cached_api_payload):
        contas = [self._conta()]
        get_cached_api_payload.return_value = {'glosas': [self._registro('true')]}

        attach_registros_glosa(contas, {})

        self.assertEqual(contas[0]['registro_recusa']['id'], 77)
        self.assertEqual(contas[0]['registro_acato'], {})
        self.assertEqual(contas[0]['registro_glosa_status'], 'true')

    @patch('core.views.get_cached_api_payload')
    def test_recusa_com_status_booleano_e_identificada(self, get_cached_api_payload):
        contas = [self._conta()]
        get_cached_api_payload.return_value = {'glosas': [self._registro(True)]}

        attach_registros_glosa(contas, {})

        self.assertEqual(contas[0]['registro_recusa']['id'], 77)
        self.assertEqual(contas[0]['registro_glosa_status'], 'true')

    @patch('core.views.get_cached_api_payload')
    def test_acato_nao_preenche_dados_da_recusa(self, get_cached_api_payload):
        contas = [self._conta()]
        get_cached_api_payload.return_value = {'glosas': [self._registro('not')]}

        attach_registros_glosa(contas, {})

        self.assertEqual(contas[0]['registro_acato']['id'], 77)
        self.assertEqual(contas[0]['registro_recusa'], {})
        self.assertEqual(contas[0]['registro_glosa_status'], 'not')

    @patch('core.views.get_cached_api_payload')
    def test_guia_vazia_e_hifen_casam_mesmo_registro(self, get_cached_api_payload):
        conta = {
            **self._conta(),
            'cd_pro_fat': '90438787',
            'nr_guia': '-',
        }
        registro = {
            **self._registro('true'),
            'id': 781,
            'procedimento': '90438787',
            'guia': None,
            'valor_glosado': 0.45,
        }
        get_cached_api_payload.return_value = {'glosas': [registro]}

        attach_registros_glosa([conta], {})

        self.assertEqual(conta['registro_recusa']['id'], 781)
        self.assertEqual(conta['registro_glosa_status'], 'true')

    @patch('core.views.get_cached_api_payload')
    def test_guia_divergente_casa_por_conta_atendimento_e_procedimento(
        self,
        get_cached_api_payload,
    ):
        conta = {**self._conta(), 'nr_guia': 'GUIA-CONTA'}
        registro = {**self._registro('true'), 'guia': 'GUIA-REGISTRO'}
        get_cached_api_payload.return_value = {'glosas': [registro]}

        attach_registros_glosa([conta], {})

        self.assertEqual(conta['registro_recusa']['id'], 77)
        self.assertEqual(conta['registro_glosa_status'], 'true')

    @patch('core.views.get_cached_api_payload')
    def test_guia_divergente_nao_casa_quando_chave_sem_guia_e_ambigua(
        self,
        get_cached_api_payload,
    ):
        conta = {**self._conta(), 'nr_guia': 'GUIA-CONTA'}
        get_cached_api_payload.return_value = {
            'glosas': [
                {**self._registro('true'), 'id': 77, 'guia': 'GUIA-1'},
                {**self._registro('true'), 'id': 78, 'guia': 'GUIA-2'},
            ]
        }

        attach_registros_glosa([conta], {})

        self.assertNotIn('registro_glosa_id', conta)
        self.assertEqual(conta['registro_recusa'], {})

    @patch('core.views.get_cached_api_payload')
    def test_lancamento_diferencia_itens_da_mesma_conta(
        self,
        get_cached_api_payload,
    ):
        contas = [
            {**self._conta(), 'cd_lancamento': 1},
            {**self._conta(), 'cd_lancamento': 2},
        ]
        get_cached_api_payload.return_value = {
            'glosas': [
                {**self._registro('true'), 'id': 91, 'cd_lancamento': 1},
                {**self._registro('true'), 'id': 92, 'cd_lancamento': 2},
            ]
        }

        attach_registros_glosa(contas, {})

        self.assertEqual(contas[0]['registro_glosa_id'], 91)
        self.assertEqual(contas[1]['registro_glosa_id'], 92)


class AcompanhamentoRowsTests(TestCase):
    def test_glosa_aguardando_tratativa_nao_aparece_no_acompanhamento(self):
        registros = [
            {
                'id': item_id,
                'sn_glosado': 'true',
                'conciliacao_remessa_id': 45,
                'cd_remessa': 987,
                'cd_atendimento': atendimento,
                'conta': conta,
                'cd_lancamento': lancamento,
                'data_glosa': '2026-07-15',
                'qtd_registro': 1,
                'valor': 60,
                'valor_glosa_origem': 20,
                'valor_glosa_pendente': 20,
            }
            for item_id, atendimento, conta, lancamento in (
                (1, 101, 1001, 1),
                (2, 102, 1002, 2),
            )
        ]

        rows = build_acompanhamento_rows(registros)
        resumo = build_acompanhamento_resumo(rows)

        self.assertEqual(rows, [])
        self.assertEqual(resumo['processos'], 0)
        self.assertEqual(resumo['em_aberto'], 0)
        self.assertEqual(resumo['valor_em_aberto_total'], 0)
        self.assertNotIn('aguardando_tratativa', ACOMPANHAMENTO_BUCKETS)

        template = Path(
            finders.find('css/app.css')
        ).parent.parent.parent / 'templates' / 'acompanhamento.html'
        self.assertNotIn('Aguardando tratativa', template.read_text())

    def test_convenio_desabilitado_exclui_registro_do_acompanhamento(self):
        convenios_desabilitados = disabled_convenio_ids(
            [{'cd_convenio': 7, 'habilitado': False}]
        )

        self.assertFalse(
            is_enabled_convenio_registro(
                {'cd_convenio': 7},
                convenios_desabilitados,
            )
        )
        self.assertTrue(
            is_enabled_convenio_registro(
                {'cd_convenio': 8},
                convenios_desabilitados,
            )
        )

    def test_recebido_exige_data_valor_e_quantidade(self):
        self.assertTrue(
            is_recebido_registro(
                {
                    'dt_recebimento': '2026-07-20',
                    'valor_recebido': 10,
                    'qtd_recebida': 1,
                }
            )
        )
        self.assertFalse(
            is_recebido_registro(
                {
                    'dt_recebimento': '2026-07-20',
                    'valor_recebido': 10,
                    'qtd_recebida': None,
                }
            )
        )

    def test_build_acompanhamento_rows_inclui_campos_da_tabela(self):
        rows = build_acompanhamento_rows(
            [
                {
                    'id': 10,
                    'sn_glosado': 'true',
                    'processo_recurso': 'REC-1',
                    'dt_recurso': '2026-07-04',
                    'processo_controle_fatura_gab': 'ORI-1',
                    'nm_paciente': 'Paciente Teste',
                    'data_glosa': '2026-07-03',
                    'dt_recebimento': '2026-07-15',
                    'qtd_registro': 4,
                    'qtd_glosada': 2,
                    'qtd_recebida': 1,
                    'valor': 200,
                    'valor_glosado': 80,
                    'valor_recebido': 50,
                }
            ]
        )

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['data_glosa_formatada'], '03/07/2026')
        self.assertEqual(row['recebido_label'], 'Parcial')
        self.assertFalse(row['recebido'])
        self.assertTrue(row['recebimento_parcial'])
        self.assertEqual(row['valor_em_aberto'], 30)
        self.assertEqual(row['valor_glosado_total_formatado'], 'R$ 200,00')
        self.assertEqual(row['valor_recurso_formatado'], 'R$ 80,00')
        self.assertEqual(row['valor_recebido_formatado'], 'R$ 50,00')
        self.assertEqual(row['qtd_glosada'], 4)
        self.assertEqual(row['qtd_recurso'], 2)
        self.assertEqual(row['qtd_recebida'], 1)
        self.assertEqual(row['dt_recebimento_modal'], '2026-07-15')
        self.assertEqual(row['valor_recebido_modal'], 'R$ 50,00')
        self.assertEqual(row['qtd_recebida_modal'], '1')

    def test_modal_de_recebimento_ignora_recebimento_incompleto(self):
        rows = build_acompanhamento_rows(
            [
                {
                    'id': 10,
                    'sn_glosado': 'true',
                    'processo_recurso': 'REC-1',
                    'dt_recurso': '2026-07-04',
                    'processo_controle_fatura_gab': 'ORI-1',
                    'nm_paciente': 'Paciente Teste',
                    'data_glosa': '2026-07-03',
                    'dt_recebimento': '2026-07-15',
                    'qtd_recebida': 0,
                    'valor': 200,
                    'valor_glosado': 80,
                    'valor_recebido': 50,
                    'observacao_recebimento': 'Recebimento parcial inválido',
                }
            ]
        )

        row = rows[0]

        self.assertFalse(row['recebido'])
        self.assertEqual(row['recebido_label'], 'Não')
        self.assertEqual(row['dt_recebimento_formatada'], '15/07/2026')
        self.assertEqual(row['valor_recebido_formatado'], 'R$ 50,00')
        self.assertEqual(row['qtd_recebida'], 0)
        self.assertEqual(row['dt_recebimento_modal'], '')
        self.assertEqual(row['valor_recebido_modal'], '')
        self.assertEqual(row['qtd_recebida_modal'], '')
        self.assertEqual(row['observacao_recebimento_modal'], '')

    def test_resumo_em_aberto_exige_recebimento_completo(self):
        rows = [
            {
                'processo_recurso': 'REC-1',
                'processo_controle_fatura_gab': 'ORI-1',
                'valor_recurso': 100,
                'valor_recebido': 0,
                'qtd_recurso': 1,
                'data_glosa_formatada': '01/07/2026',
                'paciente_label': 'Paciente 1',
                'dt_recebimento': '',
            },
            {
                'processo_recurso': 'REC-2',
                'processo_controle_fatura_gab': 'ORI-2',
                'valor_recurso': 0.45,
                'valor_recebido': 0.45,
                'qtd_recurso': 1,
                'data_glosa_formatada': '01/07/2026',
                'paciente_label': 'Paciente 2',
                'dt_recebimento': '2026-07-20',
            },
            {
                'processo_recurso': 'REC-3',
                'processo_controle_fatura_gab': 'ORI-3',
                'valor_recurso': 20,
                'valor_recebido': 20,
                'qtd_recebida': 1,
                'qtd_recurso': 1,
                'data_glosa_formatada': '01/07/2026',
                'paciente_label': 'Paciente 3',
                'dt_recebimento': '2026-07-20',
            },
            {
                'processo_recurso': 'REC-4',
                'processo_controle_fatura_gab': 'ORI-4',
                'valor_recurso': 30,
                'valor_recebido': 30,
                'qtd_recebida': 1,
                'qtd_recurso': 1,
                'data_glosa_formatada': '01/07/2026',
                'paciente_label': 'Paciente 4',
                'dt_recebimento': '',
            },
        ]

        resumo = build_acompanhamento_resumo(rows)

        self.assertEqual(resumo['em_aberto'], 3)
        self.assertEqual(resumo['valor_em_aberto_total'], 130.45)
        self.assertEqual(resumo['recebidos'], 1)

    def test_recebimento_parcial_usa_mesma_regra_dos_indicadores(self):
        registros = [
            {
                'id': 10,
                'sn_ativo': 'true',
                'sn_glosado': 'true',
                'processo_recurso': 'REC-PARCIAL',
                'dt_recurso': '2026-07-04',
                'processo_controle_fatura_gab': 'ORI-1',
                'data_glosa': '2026-07-03',
                'qtd_registro': 1,
                'qtd_recursado': 1,
                'qtd_recebida': 1,
                'valor': 100,
                'valor_recursado': 100,
                'valor_recebido': 40,
                'dt_recebimento': '2026-07-15',
            }
        ]
        rows = build_acompanhamento_rows(registros)

        resumo = build_acompanhamento_resumo(rows)
        indicadores = build_dashboard_indicadores(registros)
        colunas = build_kanban_columns(build_acompanhamento_cards(rows))
        recebidas = next(
            coluna for coluna in colunas if coluna['key'] == 'recebidas'
        )

        self.assertEqual(resumo['em_aberto'], 0)
        self.assertEqual(resumo['recebidos'], 1)
        self.assertEqual(resumo['valor_em_aberto_total'], 0)
        self.assertEqual(resumo['valor_recebido_total'], 40)
        self.assertEqual(len(recebidas['cards']), 1)
        self.assertEqual(recebidas['valor_total'], 40)
        self.assertEqual(
            recebidas['valor_total'],
            indicadores['kpis']['total_recebido'],
        )
        self.assertEqual(
            resumo['recebidos'],
            indicadores['kpis']['total_recuperado'],
        )


class DashboardIndicadoresTests(TestCase):
    def test_dashboard_e_acompanhamento_compartilham_cache_de_glosas(self):
        self.assertEqual(ACOMPANHAMENTO_GLOSAS_CACHE_KEY, DASHBOARD_GLOSAS_CACHE_KEY)

    def test_limite_do_dashboard_comporta_dataset_consolidado(self):
        from .views import DASHBOARD_GLOSAS_LIMIT

        self.assertGreaterEqual(DASHBOARD_GLOSAS_LIMIT, 17166)

    def test_acoes_dos_filtros_permanecem_dentro_do_painel(self):
        css = Path(finders.find('css/app.css')).read_text()

        self.assertIn(
            'repeat(4, minmax(145px, 1fr));',
            css,
        )
        self.assertNotIn(
            'repeat(4, minmax(145px, 1fr))\n    max-content;',
            css,
        )
        self.assertIn(
            '.indicator-filter-actions {\n  grid-column: 1 / -1;',
            css,
        )

    def test_extract_api_error_message_remove_payload_bruto_da_validacao(self):
        exc = ApiError(
            '{"detail":[{"type":"value_error","loc":["body"],'
            '"msg":"Value error, O valor glosado/acatado nao pode exceder o valor do registro.",'
            '"input":{"codigo_paciente":107821,"valor_glosado":86.0}}]}',
            422,
        )

        self.assertEqual(
            extract_api_error_message(exc),
            'O valor glosado/acatado nao pode exceder o valor do registro.',
        )

    def test_contextualize_registro_glosa_error_usa_valor_recursado(self):
        self.assertEqual(
            contextualize_registro_glosa_error(
                'O valor glosado/acatado nao pode exceder o valor do registro.',
                is_acatar=False,
            ),
            'O valor recursado nao pode exceder o valor do registro.',
        )

    def test_contextualize_registro_glosa_error_usa_valor_acatado(self):
        self.assertEqual(
            contextualize_registro_glosa_error(
                'O valor glosado/acatado nao pode exceder o valor do registro.',
                is_acatar=True,
            ),
            'O valor acatado nao pode exceder o valor do registro.',
        )

    def test_dashboard_filters_aplica_periodo_padrao_de_doze_meses(self):
        request = RequestFactory().get('/indicadores/')
        today = date.today()

        filtros = get_dashboard_filters(request)

        self.assertEqual(
            filtros['periodo_inicio'],
            subtract_months(today, 11).strftime('%Y-%m-%d'),
        )
        self.assertEqual(filtros['periodo_fim'], today.strftime('%Y-%m-%d'))

    def test_dashboard_filters_preserva_periodo_informado(self):
        request = RequestFactory().get(
            '/indicadores/',
            {
                'periodo_inicio': '2026-01-10',
                'periodo_fim': '2026-03-20',
            },
        )

        filtros = get_dashboard_filters(request)

        self.assertEqual(filtros['periodo_inicio'], '2026-01-10')
        self.assertEqual(filtros['periodo_fim'], '2026-03-20')

    def test_dashboard_filters_aceita_multiplos_valores(self):
        request = RequestFactory().get(
            '/indicadores/',
            {
                'convenio': ['AMIL', 'BACEN'],
                'prestador': ['Prestador A', 'Prestador B'],
                'tipo_atendimento': ['Urgência', 'Internação'],
                'motivo_glosa': ['Motivo A', 'Motivo B'],
            },
        )

        filtros = get_dashboard_filters(request)

        self.assertEqual(filtros['convenio'], ['AMIL', 'BACEN'])
        self.assertEqual(filtros['prestador'], ['Prestador A', 'Prestador B'])
        self.assertEqual(filtros['tipo_atendimento'], ['Urgência', 'Internação'])
        self.assertEqual(filtros['motivo_glosa'], ['Motivo A', 'Motivo B'])

    def test_dashboard_filters_aplica_multiplos_valores(self):
        rows = [
            {
                'data_glosa': '2026-01-10',
                'convenio': 'AMIL',
                'prestador': 'Prestador A',
                'tp_atendimento': 'Urgência',
                'motivo_glosa': 'Motivo A',
                'sn_glosado': 'true',
            },
            {
                'data_glosa': '2026-01-10',
                'convenio': 'BACEN',
                'prestador': 'Prestador B',
                'tp_atendimento': 'Internação',
                'motivo_glosa': 'Motivo B',
                'sn_glosado': 'true',
            },
            {
                'data_glosa': '2026-01-10',
                'convenio': 'BRADESCO',
                'prestador': 'Prestador C',
                'tp_atendimento': 'Externo',
                'motivo_glosa': 'Motivo C',
                'sn_glosado': 'true',
            },
        ]

        filtered = apply_dashboard_filters(
            rows,
            {
                'periodo_inicio': '2026-01-01',
                'periodo_fim': '2026-01-31',
                'convenio': ['AMIL', 'BACEN'],
                'prestador': ['Prestador A', 'Prestador B'],
                'tipo_atendimento': ['Urgência', 'Internação'],
                'motivo_glosa': ['Motivo A', 'Motivo B'],
                'tratativa': '',
            },
        )

        self.assertEqual(len(filtered), 2)
        self.assertEqual([row['convenio'] for row in filtered], ['AMIL', 'BACEN'])

    def test_dashboard_soma_glosa_pendente_da_conciliacao_uma_unica_vez(self):
        registros = [
            {
                'id': 1,
                'sn_ativo': 'true',
                'sn_glosado': 'true',
                'conciliacao_remessa_id': 10,
                'status_tratativa': 'pendente',
                'valor_indicador': 40,
                'valor_glosa_pendente': 40,
                'data_glosa': '2026-07-10',
                'convenio': 'Convenio A',
                'motivo_glosa': 'Glosa da conciliacao',
                'valor': 100,
            },
            {
                'id': 2,
                'sn_ativo': 'true',
                'sn_glosado': 'true',
                'conciliacao_remessa_id': 10,
                'status_tratativa': 'pendente',
                'valor_indicador': 0,
                'valor_glosa_pendente': 40,
                'data_glosa': '2026-07-10',
                'convenio': 'Convenio A',
                'motivo_glosa': 'Glosa da conciliacao',
                'valor': 100,
            },
            {
                'id': 3,
                'sn_ativo': 'true',
                'sn_glosado': 'true',
                'processo_recurso': 'REC-1',
                'dt_recurso': '2026-07-11',
                'valor_indicador': 60,
                'valor_recursado': 60,
                'data_glosa': '2026-07-10',
                'convenio': 'Convenio A',
                'motivo_glosa': 'Glosa tratada',
                'valor': 60,
            },
        ]

        indicadores = build_dashboard_indicadores(registros)

        self.assertEqual(indicadores['kpis']['total_registros'], 2)
        self.assertEqual(indicadores['kpis']['total_glosado'], 100)
        self.assertEqual(indicadores['kpis']['total_recursos'], 1)
        self.assertEqual(indicadores['kpis']['total_recursos_valor'], 60)
        self.assertEqual(indicadores['kpis']['total_glosas_sem_processo'], 1)
        self.assertEqual(
            indicadores['kpis']['total_glosas_sem_processo_valor'],
            40,
        )

    def test_recuperacao_exibe_todos_motivos_com_valor(self):
        rows = [
            {
                'sn_ativo': 'true',
                'sn_glosado': 'true',
                'processo_recurso': f'REC-{index}',
                'dt_recurso': '2026-01-10',
                'data_glosa': '2026-01-01',
                'motivo_glosa': f'Motivo {index:02d}',
	                'convenio': 'Convenio A',
	                'valor_glosado': 1000 + index,
	                'valor_recebido': 500,
	                'qtd_recebida': 1,
	                'dt_recebimento': '2026-01-20',
	            }
            for index in range(13)
        ]

        indicadores = build_recuperacao_indicators(rows)

        self.assertEqual(indicadores['total_motivos'], 13)
        self.assertEqual(len(indicadores['scatter']), 13)

    def test_recuperacao_mensal_usa_periodo_informado(self):
        indicadores = build_recuperacao_indicators(
            [
                {
                    'sn_ativo': 'true',
                    'sn_glosado': 'true',
                    'processo_recurso': 'REC-1',
                    'dt_recurso': '2026-02-10',
                    'data_glosa': '2026-02-01',
                    'motivo_glosa': 'Motivo teste',
	                    'convenio': 'Convenio A',
	                    'valor_glosado': 1000,
	                    'valor_recebido': 500,
	                    'qtd_recebida': 1,
	                    'dt_recebimento': '2026-02-20',
	                }
            ],
            '2026-01-10',
            '2026-03-20',
        )

        self.assertEqual(
            indicadores['mensal']['months'],
            ['01/2026', '02/2026', '03/2026'],
        )
        self.assertEqual(indicadores['mensal']['month_count'], 3)
        self.assertEqual(indicadores['mensal']['period_label'], '01/2026 a 03/2026')

    def test_dashboard_kpi_em_aberto_usa_mesma_regra_do_acompanhamento(self):
        registros = [
            {
                'id': 1,
                'sn_ativo': 'true',
                'sn_glosado': 'true',
                'processo_recurso': 'REC-1',
                'dt_recurso': '2026-01-10',
                'data_glosa': '2026-01-01',
                'valor_glosado': 100,
            },
            {
                'id': 2,
                'sn_ativo': 'true',
                'sn_glosado': 'true',
                'processo_recurso': 'REC-2',
                'dt_recurso': '2026-01-10',
                'data_glosa': '2026-01-01',
                'valor_glosado': 0.45,
                'valor_recebido': 0.45,
                'dt_recebimento': '2026-01-20',
            },
            {
                'id': 3,
                'sn_ativo': 'true',
                'sn_glosado': 'true',
                'processo_recurso': 'REC-3',
                'dt_recurso': '2026-01-10',
                'data_glosa': '2026-01-01',
                'valor_glosado': 20,
                'valor_recebido': 20,
                'qtd_recebida': 1,
                'dt_recebimento': '2026-01-20',
            },
        ]

        indicadores = build_dashboard_indicadores(registros)
        resumo = build_acompanhamento_resumo(build_acompanhamento_rows(registros))

        self.assertEqual(
            indicadores['kpis']['total_sem_recuperacao'],
            resumo['em_aberto'],
        )
        self.assertEqual(
            indicadores['kpis']['total_sem_recuperacao_valor'],
            resumo['valor_em_aberto_total'],
        )
        self.assertEqual(resumo['valor_em_aberto_total'], 100.45)

    def test_recuperacao_tooltip_usa_legenda_de_eficiencia_operacional(self):
        indicadores = build_recuperacao_indicators(
            [
                {
                    'sn_ativo': 'true',
                    'sn_glosado': 'true',
                    'processo_recurso': 'REC-1',
                    'dt_recurso': '2026-01-10',
                    'data_glosa': '2026-01-01',
                    'motivo_glosa': 'Motivo teste',
	                    'convenio': 'Convenio A',
	                    'valor_glosado': 1000,
	                    'valor_recebido': 500,
	                    'qtd_recebida': 1,
	                    'dt_recebimento': '2026-01-20',
	                }
            ]
        )

        tooltip = indicadores['scatter'][0]['tooltip']

        self.assertIn(
            'Taxa Eficiência Op. (vl. recuperado / vl. recursado): 50.0%',
            tooltip,
        )
        self.assertNotIn('Taxa de sucesso do recurso', tooltip)

    def test_geral_consolida_funil_por_convenio_e_taxas_mensais(self):
        indicadores = build_geral_indicators(
            [
                {
                    'sn_ativo': 'true',
                    'sn_glosado': 'true',
                    'processo_recurso': 'REC-1',
                    'dt_recurso': '2026-01-10',
                    'data_glosa': '2026-01-01',
                    'motivo_glosa': 'Motivo A',
                    'convenio': 'Convenio A',
	                    'valor': 10000,
	                    'valor_glosado': 1000,
	                    'valor_recebido': 500,
	                    'qtd_recebida': 1,
	                    'dt_recebimento': '2026-01-20',
	                },
                {
                    'sn_ativo': 'true',
                    'sn_glosado': 'not',
                    'processo_recurso': 'AC-1',
                    'dt_recurso': '2026-01-12',
                    'data_glosa': '2026-01-01',
                    'motivo_glosa': 'Motivo B',
                    'convenio': 'Convenio B',
                    'valor': 5000,
                    'valor_glosado': 250,
                    'valor_recebido': 0,
                },
            ],
            '2026-01-01',
            '2026-01-31',
        )

        self.assertEqual(indicadores['totals']['fatura'], 15000)
        self.assertEqual(indicadores['totals']['glosa'], 1250)
        self.assertEqual(indicadores['totals']['recursado'], 1000)
        self.assertEqual(indicadores['totals']['acato'], 250)
        self.assertEqual(indicadores['funnel'][0]['label'], '1. Fatura Total')
        self.assertEqual(len(indicadores['funnel'][0]['segments']), 2)
        self.assertEqual(indicadores['funnel'][3]['reference_key'], 'recursado')
        self.assertEqual(indicadores['funnel'][3]['reference_label'], 'Recursos')
        self.assertEqual(indicadores['funnel'][3]['conversion'], 50.0)
        self.assertEqual(indicadores['funnel'][4]['reference_key'], 'recursado')
        self.assertEqual(indicadores['funnel'][4]['reference_label'], 'Recursos')
        self.assertEqual(indicadores['funnel'][4]['conversion'], 25.0)
        self.assertEqual(indicadores['mensal'][0]['taxa_glosa'], 8.3)
        self.assertEqual(indicadores['mensal'][0]['taxa_acato'], 20.0)
        self.assertIn('Motivo B: 1 acato', indicadores['mensal'][0]['motivos_tooltip'])


class LoginFlowTests(TestCase):
    def test_renderiza_tela_de_login(self):
        response = self.client.get('/login/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Gestão de Glosas')
        self.assertContains(response, 'login-brand-slogan')
        self.assertContains(response, 'Hospital Prontocardio')
        self.assertIsNotNone(finders.find('img/roger.jpeg'))

    def test_redireciona_visitante_para_login(self):
        response = self.client.get('/')

        self.assertRedirects(
            response,
            '/login/?next=%2F',
            fetch_redirect_response=False,
        )

    def test_login_sem_barra_nao_e_tratado_como_tela_restrita(self):
        response = self.client.get('/login')

        self.assertRedirects(
            response,
            '/login/',
            status_code=301,
            fetch_redirect_response=False,
        )
        self.assertNotIn('acesso_negado', response.url)
        self.assertNotIn('next=', response.url)

    @patch('core.views.api_get')
    @patch('core.views.api_authenticate')
    def test_login_armazena_token_e_usuario(self, authenticate, api_get):
        authenticate.return_value = {
            'access_token': 'token-seguro',
            'token_type': 'Bearer',
        }
        api_get.return_value = {
            'id': 1,
            'nome': 'Usuário Teste',
            'email': 'usuario@teste.com',
        }

        response = self.client.post(
            '/login/',
            {
                'email': 'usuario@teste.com',
                'password': 'senha',
                'next': '/',
            },
        )

        self.assertRedirects(response, '/', fetch_redirect_response=False)
        self.assertEqual(
            self.client.session['api_access_token'],
            'token-seguro',
        )
        self.assertEqual(
            self.client.session['api_user']['nome'],
            'Usuário Teste',
        )
        api_get.assert_called_once_with('/usuarios/me', token='token-seguro')

    @patch('core.views.api_get')
    @patch('core.views.api_authenticate')
    def test_login_abre_primeira_tela_permitida_sem_alerta_falso(
        self,
        authenticate,
        api_get,
    ):
        authenticate.return_value = {
            'access_token': 'token-seguro',
            'token_type': 'Bearer',
        }
        api_get.return_value = {
            'id': 10,
            'nome': 'Solicitante',
            'email': 'solicitante@teste.com',
            'perfil': 'usuario',
            'telas_permitidas': ['solicitar_nota'],
        }

        response = self.client.post(
            '/login/',
            {
                'email': 'solicitante@teste.com',
                'password': 'senha',
                'next': '/',
            },
        )

        self.assertRedirects(
            response,
            '/requisicao/solicitacao-nota/',
            fetch_redirect_response=False,
        )
        self.assertNotIn('acesso_negado', response.url)

    @patch('core.views.api_get')
    @patch('core.views.api_authenticate')
    def test_login_nao_redireciona_para_tela_sem_permissao(
        self,
        authenticate,
        api_get,
    ):
        authenticate.return_value = {
            'access_token': 'token-seguro',
            'token_type': 'Bearer',
        }
        api_get.return_value = {
            'id': 10,
            'nome': 'Solicitante',
            'email': 'solicitante@teste.com',
            'perfil': 'usuario',
            'telas_permitidas': ['solicitar_nota'],
        }

        response = self.client.post(
            '/login/',
            {
                'email': 'solicitante@teste.com',
                'password': 'senha',
                'next': '/acompanhamento/',
            },
        )

        self.assertRedirects(
            response,
            '/requisicao/solicitacao-nota/',
            fetch_redirect_response=False,
        )
        self.assertNotIn('acesso_negado', response.url)

    @patch('core.views.api_get')
    @patch('core.views.api_authenticate')
    def test_login_remove_alerta_antigo_da_tela_permitida(
        self,
        authenticate,
        api_get,
    ):
        authenticate.return_value = {
            'access_token': 'token-seguro',
            'token_type': 'Bearer',
        }
        api_get.return_value = {
            'id': 10,
            'nome': 'Solicitante',
            'email': 'solicitante@teste.com',
            'perfil': 'usuario',
            'telas_permitidas': ['solicitar_nota'],
        }

        response = self.client.post(
            '/login/',
            {
                'email': 'solicitante@teste.com',
                'password': 'senha',
                'next': (
                    '/requisicao/solicitacao-nota/'
                    '?acesso_negado=1&origem=login'
                ),
            },
        )

        self.assertRedirects(
            response,
            '/requisicao/solicitacao-nota/?origem=login',
            fetch_redirect_response=False,
        )
        self.assertNotIn('acesso_negado', response.url)

    @patch('core.views.api_get')
    @patch('core.views.api_authenticate')
    def test_login_rejeita_redirecionamento_externo(self, authenticate, api_get):
        authenticate.return_value = {'access_token': 'token-seguro'}
        api_get.return_value = {
            'id': 1,
            'nome': 'Usuário Teste',
            'email': 'usuario@teste.com',
        }

        response = self.client.post(
            '/login/',
            {
                'email': 'usuario@teste.com',
                'password': 'senha',
                'next': 'https://site-malicioso.example',
            },
        )

        self.assertRedirects(response, '/', fetch_redirect_response=False)

    def test_logout_limpa_sessao(self):
        session = self.client.session
        session['api_access_token'] = 'token-seguro'
        session['api_user'] = {'nome': 'Usuário Teste'}
        session.save()

        response = self.client.post('/logout/')

        self.assertRedirects(
            response,
            '/login/',
            fetch_redirect_response=False,
        )
        self.assertNotIn('api_access_token', self.client.session)

    @patch('core.views.api_post')
    def test_solicita_recuperacao_de_senha(self, api_post):
        response = self.client.post(
            '/esqueci-senha/', {'email': 'usuario@teste.com'}
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Verifique seu e-mail')
        api_post.assert_called_once_with(
            '/autenticacao/esqueci-senha',
            {'email': 'usuario@teste.com'},
        )

    @patch('core.views.api_post')
    def test_redefine_senha_com_token(self, api_post):
        response = self.client.post(
            '/redefinir-senha/',
            {
                'token': 'token-seguro-com-tamanho-suficiente',
                'password': 'nova-senha-segura',
                'password_confirmation': 'nova-senha-segura',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Senha atualizada')
        api_post.assert_called_once()

    def test_rota_redefinicao_compativel_com_api(self):
        response = self.client.get(
            '/autenticacao/redefinir-senha/',
            {'token': 'token-seguro-com-tamanho-suficiente'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Crie uma nova senha')

    def test_bloqueia_gestao_de_acessos_para_usuario_comum(self):
        session = self.client.session
        session['api_access_token'] = 'token-seguro'
        session['api_user'] = {
            'nome': 'Usuário',
            'perfil': 'usuario',
            'telas_permitidas': list(SCREEN_KEYS),
        }
        session.save()

        response = self.client.get('/administrativo/acessos/')

        self.assertRedirects(
            response,
            '/?acesso_negado=1',
            fetch_redirect_response=False,
        )

    def test_bloqueia_url_e_oculta_menus_sem_permissao(self):
        session = self.client.session
        session['api_access_token'] = 'token-seguro'
        session['api_user'] = {
            'id': 7,
            'nome': 'Solicitante',
            'perfil': 'usuario',
            'telas_permitidas': ['solicitar_nota'],
        }
        session.save()

        response = self.client.get('/')

        self.assertRedirects(
            response,
            '/requisicao/solicitacao-nota/?acesso_negado=1',
            fetch_redirect_response=False,
        )
        response = self.client.get('/requisicao/solicitacao-nota/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Solicitar Nota')
        self.assertNotContains(
            response,
            '<span class="nav-label">Indicadores</span>',
        )
        self.assertNotContains(
            response,
            '<span class="nav-label">Financeiro</span>',
        )
        self.assertNotContains(
            response,
            '<span class="nav-label">Administrativo</span>',
        )

    @patch('core.middleware.api_get')
    def test_atualiza_permissoes_da_sessao_para_exibir_acompanhamento(
        self,
        api_get,
    ):
        session = self.client.session
        session['api_access_token'] = 'token-seguro'
        session['api_user'] = {
            'id': 7,
            'nome': 'Financeiro',
            'perfil': 'usuario',
            'telas_permitidas': [
                'solicitar_nota',
                'emissao_nfse',
            ],
        }
        session.save()
        api_get.return_value = {
            **session['api_user'],
            'telas_permitidas': [
                'solicitar_nota',
                'emissao_nfse',
                'acompanhamento_particular',
            ],
        }

        response = self.client.get('/requisicao/solicitacao-nota/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            '<span class="nav-label">Acompanhamento Particular</span>',
        )
        self.assertIn(
            'acompanhamento_particular',
            self.client.session['api_user']['telas_permitidas'],
        )
        api_get.assert_called_once_with(
            '/usuarios/me',
            token='token-seguro',
        )

    @patch('core.views.api_get_stream')
    def test_usuario_das_solicitacoes_visualiza_pdf_emitido(
        self,
        api_get_stream,
    ):
        session = self.client.session
        session['api_access_token'] = 'token-seguro'
        session['api_user'] = {
            'id': 7,
            'nome': 'Solicitante',
            'perfil': 'usuario',
            'telas_permitidas': ['solicitacoes_cadastradas'],
        }
        session.save()
        upstream = Mock()
        upstream.headers = {
            'Content-Type': 'application/pdf',
            'Content-Disposition': 'inline; filename="nfse-42.pdf"',
        }
        upstream.iter_content.return_value = [b'%PDF-1.4']
        api_get_stream.return_value = upstream

        response = self.client.get(
            '/requisicao/emissao-nfse/itens/42/pdf/?download=false'
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.streaming)
        self.assertNotIn('Location', response)
        self.assertEqual(
            b''.join(response.streaming_content),
            b'%PDF-1.4',
        )
        api_get_stream.assert_called_once_with(
            '/app_glosas/requisicoes/emissoes-nfse/itens/42/pdf',
            {'download': 'false'},
        )

    def test_permissao_do_pdf_nao_libera_tela_de_emissao(self):
        session = self.client.session
        session['api_access_token'] = 'token-seguro'
        session['api_user'] = {
            'id': 7,
            'nome': 'Solicitante',
            'perfil': 'usuario',
            'telas_permitidas': ['solicitacoes_cadastradas'],
        }
        session.save()

        response = self.client.get('/requisicao/emissao-nfse/')

        self.assertRedirects(
            response,
            '/requisicao/solicitacoes-cadastradas/?acesso_negado=1',
            fetch_redirect_response=False,
        )

    def test_usuario_sem_telas_recebe_orientacao(self):
        session = self.client.session
        session['api_access_token'] = 'token-seguro'
        session['api_user'] = {
            'id': 8,
            'nome': 'Sem acesso',
            'perfil': 'usuario',
            'telas_permitidas': [],
        }
        session.save()

        response = self.client.get('/')
        self.assertRedirects(
            response,
            '/acesso-negado/?acesso_negado=1',
            fetch_redirect_response=False,
        )
        response = self.client.get('/acesso-negado/')
        self.assertEqual(response.status_code, 403)
        self.assertContains(
            response,
            'Seu usuário ainda não possui telas liberadas.',
            status_code=403,
        )

    @patch('core.views.api_get')
    def test_ti_visualiza_cadastro_e_selecao_de_telas(self, api_get):
        session = self.client.session
        session['api_access_token'] = 'token-seguro'
        session['api_user'] = {
            'id': 1,
            'nome': 'Administrador',
            'perfil': 'ti',
            'telas_permitidas': [],
        }
        session.save()
        api_get.return_value = {
            'usuarios': [
                {
                    'id': 9,
                    'nome': 'Usuário Financeiro',
                    'email': 'financeiro@teste.com',
                    'perfil': 'usuario',
                    'ativo': True,
                    'telas_permitidas': [
                        'conciliacao_manual',
                        'consultar_conciliacoes',
                    ],
                }
            ]
        }

        response = self.client.get('/administrativo/acessos/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Novo acesso')
        self.assertContains(response, 'Telas visíveis')
        self.assertContains(
            response,
            'access-permission-groups--create',
        )
        self.assertContains(
            response,
            'aria-label="Telas disponíveis para o novo usuário"',
        )
        self.assertContains(response, 'Criar acesso')
        self.assertContains(
            response,
            'name="access-user-management"',
            count=1,
        )
        self.assertContains(response, 'Salvar telas visíveis')
        self.assertContains(
            response,
            'value="conciliacao_manual"',
        )
        self.assertContains(response, 'Gestão de acessos')
        css = Path(finders.find('css/app.css')).read_text()
        self.assertIn(
            '.access-admin-page {\n'
            '  display: grid;\n'
            '  flex: 1;',
            css,
        )
        self.assertIn('  overflow-y: auto;', css)

    @patch('core.views.api_patch')
    def test_ti_salva_telas_visiveis_do_usuario(self, api_patch):
        session = self.client.session
        session['api_access_token'] = 'token-seguro'
        session['api_user'] = {
            'id': 1,
            'nome': 'Administrador',
            'perfil': 'ti',
            'telas_permitidas': list(SCREEN_KEYS),
        }
        session.save()

        response = self.client.post(
            '/administrativo/acessos/',
            {
                'action': 'permissions',
                'user_id': '9',
                'telas_permitidas': [
                    'indicadores',
                    'follow_up_solicitacoes',
                ],
            },
        )

        self.assertRedirects(
            response,
            '/administrativo/acessos/',
            fetch_redirect_response=False,
        )
        api_patch.assert_called_once_with(
            '/usuarios/9/permissoes',
            {
                'telas_permitidas': [
                    'indicadores',
                    'follow_up_solicitacoes',
                ]
            },
        )

    @patch('core.views.api_post')
    def test_somente_ti_cadastra_usuario_com_telas_marcadas(
        self,
        api_post,
    ):
        session = self.client.session
        session['api_access_token'] = 'token-seguro'
        session['api_user'] = {
            'id': 1,
            'nome': 'Administrador',
            'perfil': 'ti',
            'telas_permitidas': list(SCREEN_KEYS),
        }
        session.save()

        response = self.client.post(
            '/administrativo/acessos/',
            {
                'action': 'create',
                'nome': 'Novo usuário',
                'email': 'novo@teste.com',
                'senha': 'senha-segura',
                'perfil': 'usuario',
                'telas_permitidas': [
                    'triagem',
                    'acompanhamento',
                ],
            },
        )

        self.assertRedirects(
            response,
            '/administrativo/acessos/',
            fetch_redirect_response=False,
        )
        api_post.assert_called_once_with(
            '/usuarios/',
            {
                'nome': 'Novo usuário',
                'email': 'novo@teste.com',
                'senha': 'senha-segura',
                'perfil': 'usuario',
                'telas_permitidas': [
                    'triagem',
                    'acompanhamento',
                ],
            },
        )

    @patch('core.views.api_get')
    @patch('core.middleware.api_get')
    def test_atualiza_sessao_antiga_e_exibe_gestao_de_acessos(
        self,
        middleware_api_get,
        view_api_get,
    ):
        session = self.client.session
        session['api_access_token'] = 'token-seguro'
        session['api_user'] = {
            'id': 1,
            'nome': 'Administrador',
            'perfil': 'usuario',
        }
        session.save()
        middleware_api_get.return_value = {
            'id': 1,
            'nome': 'Administrador',
            'email': 'admin@teste.com',
            'perfil': 'ti',
            'ativo': True,
            'telas_permitidas': list(SCREEN_KEYS),
        }
        view_api_get.return_value = {'usuarios': []}

        response = self.client.get('/administrativo/acessos/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Novo acesso')
        self.assertEqual(self.client.session['api_user']['perfil'], 'ti')
        middleware_api_get.assert_called_once_with(
            '/usuarios/me',
            token='token-seguro',
        )


class FollowUpGlosasTests(TestCase):
    def setUp(self):
        session = self.client.session
        session['api_access_token'] = 'token-seguro'
        session['api_user'] = {
            'id': 1,
            'nome': 'Núcleo de Glosas',
            'email': 'glosas@teste.com',
            'perfil': 'usuario',
            'telas_permitidas': list(SCREEN_KEYS),
        }
        session.save()

    def _api_payload(self):
        return {
            'cards': [
                {
                    'conciliacao_remessa_id': 12,
                    'cd_remessa': 987,
                    'numero_lote': 'TISS_0000123_4207',
                    'convenio': 'Convênio Teste',
                    'data_competencia': '2026-07-01',
                    'data_entrega': '2026-07-10',
                    'numero_nfse': 'NFS-5333',
                    'valor_remessa': '1000.00',
                    'valor_itens': '150.00',
                    'valor_glosado': '150.00',
                    'valor_glosa_pendente': '100.00',
                    'valor_total_tratado': '50.00',
                    'processo': {
                        'numero_processo': 'CONC-12',
                        'data_abertura': '2026-07-09',
                        'status_processo': 'Em análise',
                        'motivo_finalizacao': None,
                    },
                    'recebimentos': [
                        {
                            'banco': 'Banco Teste',
                            'conta': '12345-6',
                            'codigo_agencia': '0001',
                            'empenho': 'EMPENHO-12.pdf',
                        }
                    ],
                    'fiscal': {
                        'numero_nfse': 'NFS-5333',
                        'valor_servicos': '1000.00',
                        'impostos': '50.00',
                        'valor_liquido_nfse': '950.00',
                        'data_emissao': '2026-07-10T12:30:00',
                    },
                    'pacientes': [
                        {
                            'codigo_paciente': 51,
                            'nm_paciente': 'Maria da Silva',
                            'valor_itens': '150.00',
                            'valor_glosado': '150.00',
                            'valor_total_tratado': '50.00',
                            'itens': [
                                {
                                    'cd_paciente': 51,
                                    'nm_paciente': 'Maria da Silva',
                                    'cd_remessa': 987,
                                    'cd_atendimento': 789,
                                    'cd_reg': 456,
                                    'cd_lancamento': 3,
                                    'cd_prestador': 4,
                                    'nm_prestador': 'Hospital Prontocardio',
                                    'cd_convenio': 5,
                                    'nm_convenio': 'Convênio Teste',
                                    'tp_atendimento': 'Internação',
                                    'cd_pro_fat': 'PROC-10',
                                    'cd_tuss': '1714',
                                    'codigo_servico': '1714',
                                    'cd_gru_pro': 10,
                                    'ds_gru_pro': 'Diagnóstico',
                                    'cd_gru_fat': 1,
                                    'ds_gru_fat': 'EXAMES E DIAGNÓSTICOS',
                                    'descricao': 'Procedimento analítico',
                                    'nr_guia': 'GUIA-20',
                                    'dt_atendimento': '2026-07-01T08:00:00',
                                    'dt_alta': '2026-07-03T10:00:00',
                                    'dt_lancamento': '2026-07-02T09:30:00',
                                    'qt_lancamento': '2.00',
                                    'vl_total_conta': '150.00',
                                    'valor_processado': '150.00',
                                    'valor_glosa': '150.00',
                                    'valor_liberado': '0.00',
                                    'valor_total_tratado': '50.00',
                                    'valor_pendente': '100.00',
                                    'motivo_glosa_codigo': '1714',
                                    'motivo_glosa_descricao': (
                                        'VALOR GLOSADO INDEVIDAMENTE'
                                    ),
                                    'criterios_correspondencia': [
                                        'competencia_tuss_carteira_valor'
                                    ],
                                    'registro_glosa': {
                                        'id': 71,
                                        'sn_ativo': 'true',
                                        'sn_glosado': 'true',
                                        'processo_controle_fatura_gab': 'CONC-12',
                                        'processo_recurso': None,
                                        'data_glosa': '2026-07-10',
                                        'motivo_glosa': '1714',
                                        'descricao_glosa': '',
                                        'qtd_recursado': None,
                                        'valor_recursado': None,
                                        'dt_recurso': None,
                                        'dt_pagamento': None,
                                    },
                                }
                            ],
                        }
                    ],
                }
            ],
            'total': 1,
            'quantidade_glosas': 1,
            'valor_total_glosado': '150.00',
            'valor_total_pendente': '100.00',
            'valor_total_tratado': '50.00',
            'limit': 10,
            'offset': 0,
        }

    def test_ordena_processos_e_remessas_por_competencia_decrescente(self):
        cards = [
            {
                'cd_remessa': 1,
                'data_competencia': '2026-04-01',
                'processo': {'numero_processo': 'P-ANTIGO'},
            },
            {
                'cd_remessa': 2,
                'data_competencia': '2026-05-01',
                'processo': {'numero_processo': 'P-RECENTE'},
            },
            {
                'cd_remessa': 3,
                'data_competencia': '2026-07-01',
                'processo': {'numero_processo': 'P-RECENTE'},
            },
        ]

        processos = group_follow_up_glosas_by_process(cards)

        self.assertEqual(
            [item['processo']['numero_processo'] for item in processos],
            ['P-RECENTE', 'P-ANTIGO'],
        )
        self.assertEqual(
            [item['cd_remessa'] for item in processos[0]['remessas']],
            [3, 2],
        )

    @patch('core.views.get_cached_api_payload')
    @patch('core.views.api_get')
    def test_listagem_carrega_detalhamento_somente_ao_expandir(
        self,
        api_get,
        get_cached_api_payload,
    ):
        api_get.return_value = self._api_payload()
        get_cached_api_payload.return_value = {
            'convenios': [
                {
                    'cd_convenio': 5,
                    'nm_convenio': 'Convênio Teste',
                },
            ],
            'itens': [
                {
                    'codigo_termo': '1016',
                    'termo': 'BENEFICIARIO COM ATENDIMENTO SUSPENSO',
                },
                {
                    'codigo_termo': '1305',
                    'termo': 'CONTA SEM ASSINATURA DO PACIENTE',
                },
            ]
        }

        response = self.client.get('/follow-up-glosas/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '#987')
        self.assertContains(response, 'name="processo_original"')
        self.assertContains(response, 'name="processo_recurso"')
        self.assertContains(response, 'name="paciente"')
        self.assertContains(response, 'name="cd_remessa"')
        self.assertContains(response, 'name="cd_atendimento"')
        self.assertContains(response, 'name="tipo_atendimento"')
        self.assertContains(
            response,
            'id="follow-up-glosa-convenio" '
            'name="convenio" class="form-select"',
        )
        self.assertContains(
            response,
            '<option value="Convênio Teste" >Convênio Teste</option>',
        )
        self.assertNotContains(response, 'name="q"')
        self.assertContains(
            response,
            'hx-get="/follow-up-glosas/?detalhar_vinculo=12"',
        )
        self.assertContains(response, 'hx-trigger="follow-up-load once"')
        self.assertContains(response, '>Expandir</button>')
        self.assertContains(response, '>Colapsar todos</button>')
        self.assertContains(response, '@expand-follow-up-level-1.window')
        self.assertContains(response, 'id="follow-up-page-select"')
        self.assertContains(response, '<option value="1" selected>1</option>')
        self.assertContains(response, '<span>de 1</span>')
        self.assertContains(response, 'Valor tratado')
        self.assertContains(response, 'VALOR TRATADO')
        self.assertContains(response, 'R$ 50,00', count=3)
        self.assertEqual(
            response.context['resumo']['valor_total_tratado'],
            50.0,
        )
        self.assertContains(
            response,
            'Carregando detalhamento da remessa...',
        )
        self.assertNotContains(response, 'Maria da Silva')
        self.assertEqual(response.context['cards'][0]['pacientes'], [])
        api_get.assert_called_once_with(
            '/app_glosas/financeiro/conciliacao-faturamento/glosas-pendentes',
            params={
                'limit': 10,
                'offset': 0,
                'incluir_detalhes': 'false',
                'agrupar_por_processo': 'true',
            },
        )

    @patch('core.views.get_cached_api_payload')
    @patch('core.views.api_get')
    def test_card_cogestao_sem_demonstrativo_exibe_aviso(
        self,
        api_get,
        get_cached_api_payload,
    ):
        payload = self._api_payload()
        card = payload['cards'][0]
        card['conciliacao_remessa_id'] = None
        card['processo']['numero_processo'] = 'P249767/2026'
        card['processo']['status_processo'] = 'TRAMITANDO'
        card['pacientes'] = []
        payload['valor_total_tratado'] = '0.00'
        api_get.return_value = payload
        get_cached_api_payload.return_value = {'itens': []}

        response = self.client.get('/follow-up-glosas/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'P249767/2026')
        self.assertContains(
            response,
            'Ainda não há detalhamento no portal SPU Virtual do IPM.',
        )
        self.assertNotContains(response, 'detalhar_vinculo=')
        self.assertEqual(
            response.context['cards'][0]['detalhe_dom_id'],
            'cogestao-987',
        )
        self.assertTrue(
            response.context['cards'][0]['detalhes_carregados']
        )

    @patch('core.views.get_cached_api_payload')
    @patch('core.views.api_get')
    def test_processo_aberto_preserva_detalhes_do_demonstrativo(
        self,
        api_get,
        get_cached_api_payload,
    ):
        payload = self._api_payload()
        card = payload['cards'][0]
        card['conciliacao_remessa_id'] = None
        card['processo']['numero_processo'] = 'P249767/2026'
        card['processo']['status_processo'] = 'TRAMITANDO'
        item = card['pacientes'][0]['itens'][0]
        item['registro_glosa'] = None
        item['registro_recusa'] = None
        item['registro_acato'] = None
        item['tratativa_disponivel'] = True
        item['data_glosa'] = '2026-05-10'
        item['dt_pagamento'] = None
        item['valor_limite_tratativa'] = '25.00'
        api_get.return_value = payload
        get_cached_api_payload.return_value = {'itens': []}

        response = self.client.get('/follow-up-glosas/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'P249767/2026')
        self.assertContains(response, 'Maria da Silva')
        self.assertNotContains(
            response,
            'Ainda não há detalhamento no portal SPU Virtual do IPM.',
        )
        self.assertContains(response, '>+RECUSAR</button>')
        self.assertContains(response, '>+ACATO</button>')
        self.assertContains(
            response,
            'name="processo_controle_fatura_gab" value="P249767/2026"',
        )
        self.assertContains(
            response,
            'name="data_glosa" value="2026-05-10"',
        )

    @patch('core.views.get_cached_api_payload')
    @patch('core.views.api_get')
    def test_pagina_dois_renderiza_item_sem_processo_e_sem_registro(
        self,
        api_get,
        get_cached_api_payload,
    ):
        payload = self._api_payload()
        payload['total'] = 11
        payload['offset'] = 10
        card = payload['cards'][0]
        card['conciliacao_remessa_id'] = None
        card['processo']['numero_processo'] = None
        item = card['pacientes'][0]['itens'][0]
        item['registro_glosa'] = None
        item['registro_recusa'] = None
        item['registro_acato'] = None
        item['tratativa_disponivel'] = True
        api_get.return_value = payload
        get_cached_api_payload.return_value = {'itens': []}

        response = self.client.get('/follow-up-glosas/?page=2')

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'name="processo_controle_fatura_gab" value=""',
        )
        api_get.assert_called_once_with(
            '/app_glosas/financeiro/conciliacao-faturamento/glosas-pendentes',
            params={
                'limit': 10,
                'offset': 10,
                'incluir_detalhes': 'false',
                'agrupar_por_processo': 'true',
            },
        )

    @patch('core.views.get_cached_api_payload')
    @patch('core.views.api_get')
    def test_agrupa_remessas_do_mesmo_processo_e_totaliza_itens_no_cabecalho(
        self,
        api_get,
        get_cached_api_payload,
    ):
        payload = self._api_payload()
        segunda_remessa = {
            **payload['cards'][0],
            'conciliacao_remessa_id': 13,
            'cd_remessa': 988,
            'numero_nfse': 'NFS-5444',
            'valor_remessa': '2000.00',
            'valor_itens': '75.00',
            'valor_glosado': '25.00',
            'valor_glosa_pendente': '15.00',
            'valor_total_tratado': '10.00',
            'pacientes': [],
        }
        payload['cards'].append(segunda_remessa)
        payload['total'] = 1
        api_get.return_value = payload
        get_cached_api_payload.return_value = {'itens': []}

        response = self.client.get('/follow-up-glosas/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['processos']), 1)
        processo = response.context['processos'][0]
        self.assertEqual(processo['processo']['numero_processo'], 'CONC-12')
        self.assertEqual(processo['total_remessas'], 2)
        self.assertEqual(processo['valor_total'], 225.0)
        self.assertEqual(processo['valor_glosado'], 175.0)
        self.assertEqual(processo['valor_total_tratado'], 60.0)
        self.assertEqual(processo['valor_glosa_pendente'], 115.0)
        cabecalho = response.content.decode().split(
            'class="follow-up-glosa-card-header"',
            1,
        )[1].split('</button>', 1)[0]
        rotulos = (
            'PROCESSO',
            'CONVÊNIO',
            'COMP. PROD.',
            'DT. ABERTURA',
            'STATUS',
            'VALOR TOTAL',
            'VALOR GLOSADO',
            'VALOR TRATADO',
            'VALOR PENDENTE',
        )
        self.assertEqual(
            [cabecalho.index(rotulo) for rotulo in rotulos],
            sorted(cabecalho.index(rotulo) for rotulo in rotulos),
        )
        self.assertContains(response, '#987')
        self.assertContains(response, '#988')
        self.assertContains(
            response,
            '<small>COMP. PROD.</small><strong>07/2026</strong>',
            html=True,
        )
        self.assertContains(
            response,
            '<small>DT. ABERTURA</small><strong>09/07/2026</strong>',
            html=True,
        )
        self.assertContains(
            response,
            '<small>STATUS</small><strong>Em análise</strong>',
            html=True,
        )
        self.assertContains(response, 'R$ 225,00')
        self.assertContains(response, 'R$ 175,00')
        self.assertContains(response, 'R$ 60,00')
        self.assertContains(response, 'R$ 115,00')
        self.assertContains(response, 'VALOR TRATADO')
        self.assertContains(response, 'VALOR PENDENTE')

    def test_lista_follow_up_preserva_altura_natural_dos_cards(self):
        css = Path(finders.find('css/app.css')).read_text()

        self.assertIn(
            '.follow-up-glosa-list {\n  display: block;\n}',
            css,
        )
        self.assertIn(
            '.follow-up-glosa-card {\n  margin-bottom: 12px;',
            css,
        )

        base_template = Path(
            finders.find('css/app.css')
        ).parent.parent.parent / 'templates' / 'base.html'
        self.assertIn(
            '?v=20260814-associacoes-remessas-3',
            base_template.read_text(),
        )

    @patch('core.views.get_cached_api_payload')
    @patch('core.views.api_get')
    def test_paginacao_preserva_filtro_e_exibe_navegacao_superior_e_inferior(
        self,
        api_get,
        get_cached_api_payload,
    ):
        payload = self._api_payload()
        payload.update({'total': 25, 'limit': 10, 'offset': 10})
        api_get.return_value = payload
        get_cached_api_payload.return_value = {
            'itens': [
                {
                    'codigo_termo': '1016',
                    'termo': 'BENEFICIARIO COM ATENDIMENTO SUSPENSO',
                },
                {
                    'codigo_termo': '1305',
                    'termo': 'CONTA SEM ASSINATURA DO PACIENTE',
                },
            ]
        }

        response = self.client.get(
            '/follow-up-glosas/',
            {
                'processo_original': 'CONC-12',
                'processo_recurso': 'REC-71',
                'paciente': 'Maria',
                'cd_remessa': '987',
                'convenio': 'IPM',
                'cd_atendimento': '789',
                'tipo_atendimento': 'Internação',
                'page': '2',
            },
        )

        self.assertEqual(response.status_code, 200)
        pagination = response.context['pagination']
        query = (
            'processo_original=CONC-12&processo_recurso=REC-71&'
            'convenio=IPM&paciente=Maria&cd_remessa=987&'
            'cd_atendimento=789&tipo_atendimento=Interna%C3%A7%C3%A3o'
        )
        self.assertEqual(pagination['previous_url'], f'?{query}&page=1')
        self.assertEqual(pagination['next_url'], f'?{query}&page=3')
        self.assertContains(response, '<option value="2" selected>2</option>')
        self.assertContains(
            response,
            '<option value="IPM" selected>IPM</option>',
        )
        self.assertContains(response, 'Página 2 de 3')
        escaped_query = query.replace('&', '&amp;')
        self.assertContains(
            response,
            f'href="?{escaped_query}&amp;page=1"',
            count=2,
        )
        self.assertContains(
            response,
            f'href="?{escaped_query}&amp;page=3"',
            count=2,
        )
        api_get.assert_called_once_with(
            '/app_glosas/financeiro/conciliacao-faturamento/glosas-pendentes',
            params={
                'limit': 10,
                'offset': 10,
                'incluir_detalhes': 'false',
                'agrupar_por_processo': 'true',
                'processo_original': 'CONC-12',
                'processo_recurso': 'REC-71',
                'paciente': 'Maria',
                'cd_remessa': '987',
                'convenio': 'IPM',
                'cd_atendimento': '789',
                'tipo_atendimento': 'Internação',
            },
        )

    @patch('core.views.get_cached_api_payload')
    @patch('core.views.api_get')
    def test_renderiza_remessa_pacientes_itens_e_menu(
        self,
        api_get,
        get_cached_api_payload,
    ):
        api_get.return_value = self._api_payload()
        get_cached_api_payload.return_value = {
            'itens': [
                {
                    'codigo_termo': '1016',
                    'termo': 'BENEFICIARIO COM ATENDIMENTO SUSPENSO',
                },
                {
                    'codigo_termo': '1305',
                    'termo': 'CONTA SEM ASSINATURA DO PACIENTE',
                },
            ]
        }

        response = self.client.get(
            '/follow-up-glosas/',
            {'detalhar_vinculo': '12'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            '<span class="nav-label">Núcleo Gestor de Glosas</span>',
        )
        for hidden_menu in (
            'Glosas',
            'Remessas',
            'Recursos',
            'Recebimentos',
        ):
            self.assertNotContains(
                response,
                f'<span class="nav-label">{hidden_menu}</span>',
            )
        css = Path(finders.find('css/app.css')).read_text()
        nav_group_rule = css.split('.nav-group-toggle {', 1)[1].split(
            '}',
            1,
        )[0]
        self.assertIn('text-transform: none;', nav_group_rule)
        self.assertNotIn('text-transform: uppercase;', nav_group_rule)
        self.assertIn('font-family: inherit;', nav_group_rule)
        self.assertIn('font-size: inherit;', nav_group_rule)
        self.assertIn('font-weight: 400;', nav_group_rule)
        content = response.content.decode()
        self.assertLess(
            content.index('Follow-Up de Glosas'),
            content.index('title="Triagem"'),
        )
        for expected in (
            'REMESSA',
            'NÚMERO DO LOTE',
            'TISS_0000123_4207',
            'COMPETÊNCIA',
            '07/2026',
            '#987',
            'CONVÊNIO',
            'Convênio Teste',
            'PROCESSO',
            'VALOR GLOSADO',
            'R$ 150,00',
            'TOTAL TRATADO',
            'R$ 50,00',
            'Maria da Silva',
            'Procedimento analítico',
            'CÓD. ATENDIMENTO',
            'DT. ATENDIMENTO',
            'DT. ALTA',
            'TIPO ATENDIMENTO',
            'EXAMES E DIAGNÓSTICOS',
            'CÓD. ITEM',
            'VALOR PROCESSADO',
            'VALOR LIBERADO',
            'Descrição do motivo',
            'VALOR GLOSADO INDEVIDAMENTE',
            'Processo Origem',
            'Qtd item',
            'Valor item',
            '+RECUSAR',
            '+ACATO',
            'follow-up-glosa-records-scroll',
            '<template x-if="patientOpen">',
        ):
            self.assertContains(response, expected)
        self.assertNotContains(response, 'GRUPO DO PROCEDIMENTO ·')
        for removed_detail in (
            'Dados do Processo',
            'Dados do Recebimento',
            'Dados Fiscais',
            'EMPENHO-12.pdf',
            'Valor líquido NFS-e',
        ):
            self.assertNotContains(response, removed_detail)
        self.assertNotContains(response, 'atdOpen')
        remessa_header = content.split(
            'class="follow-up-glosa-remessa-header"',
            1,
        )[1].split('</button>', 1)[0]
        remessa_rotulos = (
            'REMESSA',
            'NÚMERO DO LOTE',
            'COMPETÊNCIA',
            'VALOR TOTAL',
            'VALOR GLOSADO',
            'VALOR TRATADO',
            'TOTAL PENDENTE',
        )
        self.assertEqual(
            [remessa_header.index(rotulo) for rotulo in remessa_rotulos],
            sorted(remessa_header.index(rotulo) for rotulo in remessa_rotulos),
        )
        self.assertIn(
            '<small>VALOR TOTAL</small><strong>R$ 150,00</strong>',
            remessa_header,
        )
        self.assertNotIn('NFS-E', remessa_header)
        self.assertNotIn('R$ 1.000,00', remessa_header)
        paciente_header = content.split(
            'class="follow-up-glosa-patient-header"',
            1,
        )[1].split('</button>', 1)[0]
        paciente_rotulos = (
            'PACIENTE',
            'CÓD. ATENDIMENTO',
            'TIPO ATENDIMENTO',
            'DT. ATENDIMENTO',
            'DT. ALTA',
        )
        self.assertEqual(
            [paciente_header.index(rotulo) for rotulo in paciente_rotulos],
            sorted(
                paciente_header.index(rotulo)
                for rotulo in paciente_rotulos
            ),
        )
        self.assertIn('cell-mono">1714</td>', content)
        self.assertContains(
            response,
            'class="col-valor follow-up-glosa-value-heading"',
            count=2,
        )
        self.assertContains(
            response,
            'value="VALOR GLOSADO INDEVIDAMENTE" readonly',
        )
        self.assertNotContains(
            response,
            '<textarea class="form-control form-control-readonly"',
        )
        self.assertContains(
            response,
            "editingExisting ? 'Salvar alterações' : "
            "(modal === 'acatar' ? 'Registrar acato' : 'Registrar recurso')",
        )
        self.assertNotContains(response, 'list="tiss-motivo-options"')
        self.assertNotContains(
            response,
            'Processo / N Controle / N Fatura / N GAB (Processo Original)',
        )
        self.assertNotContains(response, '<label>Data da glosa')
        self.assertNotContains(response, '<label>Dt pagamento')
        self.assertContains(
            response,
            'name="processo_controle_fatura_gab" value="CONC-12"',
        )
        self.assertContains(
            response,
            'name="data_glosa" value="2026-07-10"',
        )
        self.assertContains(
            response,
            'name="dt_pagamento" value="2026-07-10"',
        )
        self.assertContains(
            response,
            'name="qt_lancamento" value="2.00"',
        )
        self.assertContains(
            response,
            'name="vl_total_conta" value="150.00"',
        )
        self.assertContains(response, 'data-max-quantity="2.00"')
        self.assertContains(response, 'data-max-value="150.00"')
        paciente = response.context['cards'][0]['pacientes'][0]
        atendimentos_paciente = response.context['cards'][0][
            'atendimentos_paciente'
        ]
        self.assertEqual(paciente['total_atendimentos'], 1)
        self.assertEqual(len(atendimentos_paciente), 1)
        self.assertEqual(atendimentos_paciente[0]['nm_paciente'], 'Maria da Silva')
        self.assertEqual(atendimentos_paciente[0]['cd_atendimento'], 789)
        self.assertEqual(paciente['atendimentos'][0]['total_grupos'], 1)
        self.assertEqual(
            paciente['atendimentos'][0]['grupos_procedimento'][0][
                'cd_gru_fat'
            ],
            1,
        )
        api_get.assert_called_once_with(
            '/app_glosas/financeiro/conciliacao-faturamento/glosas-pendentes',
            params={
                'limit': 1,
                'offset': 0,
                'incluir_detalhes': 'true',
                'conciliacao_remessa_id': 12,
            },
        )

    @patch('core.views.get_cached_api_payload')
    @patch('core.views.api_get')
    def test_separa_cards_por_atendimento_e_codigo_item_tem_fallback(
        self,
        api_get,
        get_cached_api_payload,
    ):
        payload = self._api_payload()
        paciente = payload['cards'][0]['pacientes'][0]
        item_original = paciente['itens'][0]
        segundo_item = {
            **item_original,
            'cd_atendimento': 790,
            'cd_pro_fat': 'PROC-20',
            'cd_tuss': '',
            'codigo_servico': '',
            'descricao': 'Procedimento sem código TUSS',
            'dt_atendimento': '2026-07-05T08:00:00',
            'dt_alta': '2026-07-06T10:00:00',
            'tp_atendimento': 'Urgência',
            'cd_gru_fat': 2,
            'ds_gru_fat': 'PROCEDIMENTOS CLÍNICOS',
            'registro_glosa': {
                **item_original['registro_glosa'],
                'id': 72,
            },
        }
        paciente['itens'].append(segundo_item)
        paciente['valor_itens'] = '300.00'
        payload['cards'][0]['valor_itens'] = '300.00'
        api_get.return_value = payload
        get_cached_api_payload.return_value = {'itens': []}

        response = self.client.get(
            '/follow-up-glosas/',
            {'detalhar_vinculo': '12'},
        )

        self.assertEqual(response.status_code, 200)
        agrupamentos = response.context['cards'][0][
            'atendimentos_paciente'
        ]
        self.assertEqual(len(agrupamentos), 2)
        self.assertEqual(
            [item['cd_atendimento'] for item in agrupamentos],
            [789, 790],
        )
        self.assertEqual(
            agrupamentos[1]['dt_atendimento_formatada'],
            '05/07/2026',
        )
        self.assertEqual(agrupamentos[1]['dt_alta_formatada'], '06/07/2026')
        self.assertEqual(agrupamentos[1]['tp_atendimento'], 'Urgência')
        codigos = [
            item['codigo_item']
            for atendimento in agrupamentos
            for grupo in atendimento['grupos_procedimento']
            for item in grupo['itens']
        ]
        self.assertEqual(codigos, ['1714', 'PROC-20'])
        self.assertContains(response, 'PROCEDIMENTOS CLÍNICOS')
        self.assertContains(response, 'cell-mono">PROC-20</td>')

    @patch('core.views.api_put')
    def test_recursar_atualiza_registro_analitico_existente(self, api_put):
        api_put.return_value = {'id': 71, 'sn_glosado': 'true'}
        response = self.client.post(
            '/follow-up-glosas/',
            {
                'registro_glosa_id': '71',
                'cd_paciente': '51',
                'nm_paciente': 'Maria da Silva',
                'cd_remessa': '987',
                'cd_atendimento': '789',
                'cd_reg': '456',
                'cd_lancamento': '3',
                'cd_prestador': '4',
                'nm_prestador': 'Hospital Prontocardio',
                'cd_convenio': '5',
                'nm_convenio': 'Convênio Teste',
                'tp_atendimento': 'Internação',
                'cd_pro_fat': 'PROC-10',
                'cd_gru_pro': '10',
                'ds_gru_pro': 'Diagnóstico',
                'cd_gru_fat': '1',
                'ds_gru_fat': 'EXAMES E DIAGNÓSTICOS',
                'descricao': 'Procedimento analítico',
                'nr_guia': 'GUIA-20',
                'dt_atendimento': '2026-07-01T08:00:00',
                'dt_alta': '2026-07-03T10:00:00',
                'dt_lancamento': '2026-07-02T09:30:00',
                'qt_lancamento': '2',
                'vl_total_conta': '150.00',
                'sn_glosado': 'true',
                'processo_controle_fatura_gab': 'CONC-12',
                'data_glosa': '2026-07-10',
                'dt_pagamento': '2026-07-10',
                'motivo_glosa': '1016 - Motivo TISS',
                'cd_tuss': '1714',
                'processo_recurso': 'REC-71',
                'dt_recurso': '2026-07-11',
                'qtd_glosada': '1',
                'valor_glosado': 'R$ 75,00',
                'descricao_glosa': 'Recurso enviado',
                'form_action': 'salvar',
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(
            response.content,
            {
                'ok': True,
                'message': 'Recurso registrado no Follow-Up de Glosas.',
                'tag': 'success',
                'payload': {'id': 71, 'sn_glosado': 'true'},
            },
        )
        path, payload = api_put.call_args.args
        self.assertTrue(path.endswith('/71'))
        self.assertEqual(payload['processo_controle_fatura_gab'], 'CONC-12')
        self.assertEqual(payload['data_glosa'], '2026-07-10')
        self.assertEqual(payload['dt_pagamento'], '2026-07-10')
        self.assertEqual(payload['descricao_item'], 'Procedimento analítico')
        self.assertEqual(payload['cd_gru_pro'], 10)
        self.assertEqual(payload['ds_gru_pro'], 'Diagnóstico')
        self.assertEqual(payload['cd_gru_fat'], 1)
        self.assertEqual(payload['ds_gru_fat'], 'EXAMES E DIAGNÓSTICOS')
        self.assertEqual(payload['cd_tuss'], '1714')
        self.assertEqual(payload['motivo_glosa'], '1016')
        self.assertEqual(payload['processo_recurso'], 'REC-71')
        self.assertEqual(payload['valor_recursado'], 75.0)

    @patch('core.views.api_put')
    def test_acatar_registra_tratamento_no_item_existente(self, api_put):
        api_put.return_value = {'id': 72, 'sn_glosado': 'not'}

        response = self.client.post(
            '/follow-up-glosas/',
            {
                'registro_glosa_id': '71',
                'sn_glosado': 'not',
                'motivo_glosa': '1714',
                'dt_recurso': '2026-07-11',
                'qtd_glosada': '1',
                'valor_glosado': 'R$ 25,00',
                'descricao_glosa': 'Valor acatado pelo hospital',
                'form_action': 'salvar',
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(
            response.content,
            {
                'ok': True,
                'message': 'Valor acatado no Follow-Up de Glosas.',
                'tag': 'warning',
                'payload': {'id': 72, 'sn_glosado': 'not'},
            },
        )
        path, payload = api_put.call_args.args
        self.assertTrue(path.endswith('/71'))
        self.assertEqual(payload['sn_glosado'], 'not')
        self.assertEqual(payload['motivo_glosa'], '1714')
        self.assertEqual(payload['qtd_recursado'], 1)
        self.assertEqual(payload['valor_recursado'], 25.0)

    @patch('core.views.api_delete')
    def test_excluir_tratamento_desfaz_registro_existente(self, api_delete):
        response = self.client.post(
            '/follow-up-glosas/',
            {
                'registro_glosa_id': '72',
                'form_action': 'desfazer',
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(
            response.content,
            {
                'ok': True,
                'message': 'Tratamento desfeito no Follow-Up de Glosas.',
                'tag': 'warning',
                'payload': None,
            },
        )
        api_delete.assert_called_once()
        self.assertTrue(api_delete.call_args.args[0].endswith('/72'))

    @patch('core.views.get_cached_api_payload')
    @patch('core.views.api_get')
    def test_modal_nao_reutiliza_valor_ao_trocar_tipo_de_tratativa(
        self,
        api_get,
        get_cached_api_payload,
    ):
        payload = self._api_payload()
        registro = payload['cards'][0]['pacientes'][0]['itens'][0][
            'registro_glosa'
        ]
        registro.update(
            {
                'processo_recurso': 'REC-71',
                'dt_recurso': '2026-07-11',
                'qtd_recursado': '1.00',
                'valor_recursado': '75.00',
                'motivo_glosa': '3021 - MOTIVO DO RECURSO',
                'sn_glosado': 'true',
            }
        )
        api_get.return_value = payload
        get_cached_api_payload.return_value = {'itens': []}

        response = self.client.get(
            '/follow-up-glosas/',
            {'detalhar_vinculo': '12'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "registroRecusaId: '71'")
        self.assertContains(response, "registroAcatoId: ''")
        self.assertContains(response, "valorRecusado: 'R$ 75,00'")
        self.assertContains(response, "valorAcatado: ''")
        item_context = response.context['cards'][0]['pacientes'][0][
            'itens'
        ][0]
        self.assertEqual(
            item_context['registro_recusa']['motivo_glosa'],
            '3021 - MOTIVO DO RECURSO',
        )
        self.assertFalse(
            item_context['registro_acato'].get('motivo_glosa')
        )
        self.assertContains(response, "motivoAcato: ''")
        self.assertContains(response, "@click=\"openTreatment('acatar')\"")
        self.assertContains(response, 'x-model="qtdTratada"')
        self.assertContains(
            response,
            "this.valorTratado = acato ? "
            "this.valorAcatado : this.valorRecusado;",
        )
        self.assertContains(
            response,
            "this.motivoTratado = (acato ? "
            "this.motivoAcato : this.motivoRecusa) || this.motivoOrigem;",
        )
        self.assertContains(response, ':required="modal !== \'acatar\'"')


class AssociacoesRemessasIpmTests(TestCase):
    def setUp(self):
        session = self.client.session
        session['api_access_token'] = 'token-seguro'
        session['api_user'] = {
            'id': 1,
            'nome': 'Núcleo de Glosas',
            'email': 'glosas@teste.com',
            'perfil': 'usuario',
            'telas_permitidas': list(SCREEN_KEYS),
        }
        session.save()

    @patch('core.views.api_get')
    def test_renderiza_processo_e_remessas_oracle(self, api_get):
        api_get.return_value = {
            'total': 1,
            'limit': 10,
            'offset': 0,
            'resumo': {
                'processos_pendentes': 1,
                'nrs_pendentes': 1,
                'associacoes_realizadas': 0,
                'remessas_disponiveis': 1,
            },
            'processos': [
                {
                    'numero_processo': 'P123/2026',
                    'competencia_producao': '05/2026',
                    'data_abertura': '2026-06-01',
                    'convenio': 'IPM',
                    'status': 'FINALIZADO',
                    'valor_protocolado': '1000.00',
                    'valor_aprovado': '900.00',
                    'valor_glosado': '100.00',
                    'nrs': [
                        {
                            'nr': 'NR-100',
                            'valor_protocolado': '1000.00',
                            'valor_aprovado': '900.00',
                            'valor_glosado': '100.00',
                            'associacoes': [],
                            'remessas': [
                                {
                                    'cd_remessa': 16040,
                                    'competencia': '05/2026',
                                    'nm_convenio': 'IPM',
                                    'valor_total': '1000.00',
                                    'associacao_id': None,
                                }
                            ],
                        }
                    ],
                }
            ],
        }

        response = self.client.get('/associacoes-remessas-ipm/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<h1>Associações de Remessas</h1>')
        self.assertNotContains(response, 'Associações de Remessas IPM')
        self.assertContains(response, 'P123/2026')
        self.assertContains(response, '#16040')
        self.assertContains(response, 'Associar')
        self.assertContains(response, '<small>NR</small>')
        self.assertContains(response, 'NR-100')
        self.assertContains(response, 'Processos pendentes')
        self.assertContains(response, 'NRs pendentes')
        self.assertContains(response, 'Associações realizadas')
        self.assertContains(response, 'Remessas disponíveis')
        self.assertContains(response, '>Expandir</button>')
        self.assertContains(response, '>Colapsar todos</button>')
        api_get.assert_called_once_with(
            '/app_glosas/financeiro/associacoes-remessas-ipm',
            params={'limit': 10, 'offset': 0},
        )
        css = Path(finders.find('css/app.css')).read_text()
        self.assertIn(
            '.ipm-association-page {\n'
            '  display: flex;\n'
            '  flex: 1 1 auto;',
            css,
        )
        self.assertIn(
            '.ipm-association-list {\n'
            '  flex: 1 1 0;\n'
            '  min-height: 0;\n'
            '  align-content: start;\n'
            '  grid-auto-rows: max-content;\n'
            '  padding-right: 4px;\n'
            '  overflow-x: hidden;\n'
            '  overflow-y: auto;',
            css,
        )

    @patch('core.views.api_get')
    def test_pagina_processos_preservando_filtros(self, api_get):
        api_get.return_value = {
            'total': 21,
            'limit': 10,
            'offset': 10,
            'processos': [
                {
                    'numero_processo': 'P456/2026',
                    'competencia_producao': '06/2026',
                    'nrs': [],
                }
            ],
        }

        response = self.client.get(
            '/associacoes-remessas-ipm/',
            {'page': 2, 'competencia': '06/2026'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '11-11 de 21 processos exibidos')
        self.assertContains(response, 'Página 2 de 3')
        api_get.assert_called_once_with(
            '/app_glosas/financeiro/associacoes-remessas-ipm',
            params={
                'competencia': '06/2026',
                'limit': 10,
                'offset': 10,
            },
        )

    @patch('core.views.api_post')
    def test_associa_remessa_ao_processo(self, api_post):
        response = self.client.post(
            '/associacoes-remessas-ipm/',
            {
                'acao': 'associar',
                'numero_processo': 'P123/2026',
                'competencia_producao': '05/2026',
                'nr': 'NR-100',
                'cd_remessa': '16040',
            },
        )

        self.assertEqual(response.status_code, 302)
        api_post.assert_called_once_with(
            '/app_glosas/financeiro/associacoes-remessas-ipm',
            {
                'numero_processo': 'P123/2026',
                'competencia_producao': '05/2026',
                'nr': 'NR-100',
                'cd_remessa': 16040,
            },
        )

    @patch('core.views.api_put')
    def test_edita_associacao(self, api_put):
        response = self.client.post(
            '/associacoes-remessas-ipm/',
            {
                'acao': 'editar',
                'associacao_id': '9',
                'cd_remessa': '16041',
            },
        )

        self.assertEqual(response.status_code, 302)
        api_put.assert_called_once_with(
            '/app_glosas/financeiro/associacoes-remessas-ipm/9',
            {'cd_remessa': 16041},
        )

    @patch('core.views.api_delete')
    def test_exclui_associacao(self, api_delete):
        response = self.client.post(
            '/associacoes-remessas-ipm/',
            {'acao': 'excluir', 'associacao_id': '9'},
        )

        self.assertEqual(response.status_code, 302)
        api_delete.assert_called_once_with(
            '/app_glosas/financeiro/associacoes-remessas-ipm/9'
        )


class ConciliacaoFaturamentoTests(TestCase):
    def setUp(self):
        cache.clear()
        session = self.client.session
        session['api_access_token'] = 'token-seguro'
        session['api_user'] = {
            'id': 1,
            'nome': 'Financeiro',
            'email': 'financeiro@teste.com',
            'perfil': 'usuario',
            'telas_permitidas': list(SCREEN_KEYS),
        }
        session.save()

    def test_monta_payload_com_remessa_e_notas(self):
        payload = build_conciliacao_faturamento_payload(
            {
                'cd_remessa': '10',
                'processo_recebimento': ' PROC-1 ',
                'notas_json': (
                    '[{"nfse_row_hash": "hash-1", '
                    '"valor_liquido_nfse": "100.00", '
                    '"sn_glosado": false, "valor_glosado": "20.00", '
                    '"valor_impostos": "5.00", '
                    '"data_previsao_recebimento": "2026-08-10"}]'
                ),
            }
        )

        self.assertEqual(payload['cd_remessa'], 10)
        self.assertEqual(payload['processo_recebimento'], 'PROC-1')
        self.assertEqual(payload['notas'][0]['nfse_row_hash'], 'hash-1')
        self.assertEqual(payload['notas'][0]['valor_alocado'], '100.00')
        self.assertEqual(payload['notas'][0]['valor_impostos'], '5.00')
        self.assertTrue(payload['notas'][0]['sn_glosado'])
        self.assertNotIn('valor_liquido_nfse', payload['notas'][0])

    def test_impede_valor_liquido_zerado(self):
        with self.assertRaisesRegex(
            ValueError,
            'valor líquido conciliado da NFS-e deve ser maior que zero',
        ):
            build_conciliacao_faturamento_payload(
                {
                    'cd_remessa': '10',
                    'processo_recebimento': 'PROC-1',
                    'notas_json': (
                        '[{"nfse_row_hash": "hash-1", '
                        '"valor_liquido_nfse": "0.00", '
                        '"valor_glosado": "20.00"}]'
                    ),
                }
            )

    @patch('core.views.api_get')
    def test_renderiza_remessas_pendentes_e_menu_financeiro(self, api_get):
        def resposta(path, params=None):
            if path.endswith('/remessas'):
                return {
                    'remessas': [
                        {
                            'cd_remessa': 987,
                            'data_competencia': '2026-07-01',
                            'convenio': 'Convênio Teste',
                            'cnpj_convenio': '12345678000190',
                            'valor_remessa': '120.00',
                            'valor_conciliado': '80.00',
                            'valor_acatado': '0.00',
                            'valor_nao_conciliado': '40.00',
                            'valor_recurso_disponivel': '20.00',
                            'valor_disponivel_conciliacao': '20.00',
                            'processo_recebimento': 'PROC-987',
                            'historico': [
                                {
                                    'numero_nfse': '12345',
                                    'data_emissao': '2026-07-10T10:00:00',
                                    'valor_alocado': '80.00',
                                    'valor_glosado': '20.00',
                                    'data_previsao_recebimento': '2026-08-10',
                                    'data_recebimento': None,
                                }
                            ],
                        }
                    ],
                    'total': 250,
                    'valor_total_conciliado': '98765.43',
                    'valor_total_nao_conciliado': '123456.78',
                    'limit': 25,
                    'offset': 0,
                }
            if path.endswith('/convenios'):
                return {
                    'convenios': [
                        {
                            'cd_convenio': 5,
                            'nm_convenio': 'Convênio Teste',
                        },
                    ],
                }
            return {'contas': []}

        api_get.side_effect = resposta

        response = self.client.get(
            '/financeiro/conciliacao-fiscal-faturamento/'
        )

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertContains(response, 'Conciliação Manual')
        self.assertNotContains(
            response,
            'Conciliação (Faturamento X Fiscal)',
        )
        self.assertContains(response, '987')
        self.assertContains(response, '12345')
        self.assertContains(response, 'Convênio Teste')
        self.assertContains(response, 'Financeiro')
        self.assertContains(response, '<span>TOTAL REMESSAS</span>')
        self.assertContains(response, '<span>TOTAL CONCILIADO</span>')
        self.assertContains(response, '<span>SALDO NÃO CONCILIADO</span>')
        self.assertContains(response, '<strong>R$ 98.765,43</strong>')
        self.assertContains(response, '<strong>R$ 123.456,78</strong>')
        self.assertLess(
            content.index('<span>TOTAL REMESSAS</span>'),
            content.index('<span>TOTAL CONCILIADO</span>'),
        )
        self.assertLess(
            content.index('<span>TOTAL CONCILIADO</span>'),
            content.index('<span>SALDO NÃO CONCILIADO</span>'),
        )
        self.assertNotContains(response, '250 pendentes')
        self.assertNotContains(response, 'Página atual')
        self.assertNotContains(response, 'Regra da conciliação')
        self.assertContains(response, '<div class="pagination-control">')
        self.assertContains(response, 'class="page-select-form"')
        self.assertContains(response, '<option value="1" selected>1</option>')
        self.assertContains(response, '<span>Pagina 1 de 10</span>')
        self.assertContains(response, 'href="?page=2">Proxima</a>')
        self.assertContains(response, 'Operação Não Permitida')
        self.assertContains(response, 'novalidate')
        self.assertContains(response, 'form.checkValidity()')
        self.assertContains(
            response,
            '<button class="btn btn-primary" type="submit">'
            'Conciliar remessa</button>',
        )
        self.assertContains(response, 'panel filter-panel finance-search-bar')
        self.assertContains(response, 'name="numero_nfse"')
        self.assertContains(response, 'placeholder="Número da NFS-e"')
        self.assertContains(response, 'name="cd_remessa"')
        self.assertContains(response, 'placeholder="Código da remessa"')
        self.assertContains(
            response,
            '<select class="form-select" name="convenio">',
        )
        self.assertContains(
            response,
            '<option value="Convênio Teste" >Convênio Teste</option>',
        )
        self.assertNotContains(response, 'name="q"')
        self.assertContains(response, '>Expandir</button>')
        self.assertContains(response, '>Colapsar todos</button>')
        self.assertContains(
            response,
            '@expand-all-fiscal-reconciliations.window="open = true"',
        )
        self.assertContains(
            response,
            '@collapse-all-fiscal-reconciliations.window="open = false"',
        )
        self.assertLess(
            content.index('class="collapse-actions"'),
            content.index('class="results-toolbar finance-results-toolbar"'),
        )
        self.assertLess(
            content.index('>Expandir</button>'),
            content.index('>Colapsar todos</button>'),
        )
        self.assertContains(response, 'panel finance-results-panel')
        self.assertContains(response, 'results-toolbar finance-results-toolbar')
        self.assertContains(response, '<small>Número remessa</small>')
        self.assertContains(response, '<small>Data competência</small>')
        self.assertContains(response, '<small>Valor remessa</small>')
        self.assertContains(response, '<small>Valor conciliado</small>')
        self.assertContains(response, '<small>Valor não conciliado</small>')
        self.assertNotContains(response, 'GLOSAR?')
        self.assertNotContains(response, 'RECURSO DISPONÍVEL')
        self.assertNotContains(response, 'VALOR ACATADO')
        self.assertNotContains(response, 'DISPONÍVEL PARA ESTA CONCILIAÇÃO')
        self.assertContains(response, 'Conciliações anteriores da remessa')
        self.assertContains(response, '<small>Valor impostos</small>')
        self.assertContains(response, '<small>Valor glosa</small>')
        self.assertContains(response, 'Valor líquido NFS-e *')
        self.assertContains(response, 'Imposto na remessa *')
        self.assertContains(response, 'Previsão recebimento *')
        self.assertContains(response, '<span>Recebimento</span>')
        self.assertContains(response, 'Impostos NFS-e')
        self.assertContains(response, 'Saldo impostos')
        self.assertContains(
            response,
            'Total das retenções presente na nota fiscal.',
        )
        self.assertContains(
            response,
            'Saldo de impostos da nota disponível para esta remessa.',
        )
        self.assertContains(response, 'finance-money-input')
        self.assertContains(
            response,
            "updateMoney(nota, 'valor_liquido_nfse'",
        )
        self.assertContains(response, 'Valor bruto NFS-e')
        self.assertContains(response, 'Valor líquido NFS-e (conciliado)')
        self.assertContains(response, 'Saldo após conciliação')
        self.assertContains(response, 'normalizeOneCentExcess()')
        self.assertContains(response, 'if (excessCents !== 1) return false;')
        self.assertContains(
            response,
            'Number(nota.saldo_nfse || 0) - '
            'Number(nota.valor_liquido_nfse || 0)',
        )
        self.assertNotContains(
            response,
            'return Math.max(this.valorDisponivel - '
            'this.totalComprometido',
        )
        self.assertContains(response, 'this.notaResults.filter(')
        self.assertContains(response, "this.searchTerm = '';")
        self.assertContains(
            response,
            'A soma das parcelas conciliadas excede o saldo '
            'disponível da remessa.',
        )

    @patch('core.views.api_get')
    def test_paginacao_mantem_filtro_da_pesquisa(self, api_get):
        def resposta(path, params=None):
            if path.endswith('/remessas'):
                return {
                    'remessas': [],
                    'total': 250,
                    'valor_total_nao_conciliado': '123456.78',
                    'limit': 25,
                    'offset': 25,
                }
            return {'contas': []}

        api_get.side_effect = resposta

        response = self.client.get(
            '/financeiro/conciliacao-fiscal-faturamento/',
            {
                'numero_nfse': '12345',
                'cd_remessa': '987',
                'convenio': 'Convênio A',
                'page': 2,
            },
        )

        self.assertEqual(response.status_code, 200)
        pagination = response.context['pagination']
        self.assertEqual(
            pagination['previous_url'],
            '?numero_nfse=12345&cd_remessa=987&'
            'convenio=Conv%C3%AAnio+A&page=1',
        )
        self.assertEqual(
            pagination['next_url'],
            '?numero_nfse=12345&cd_remessa=987&'
            'convenio=Conv%C3%AAnio+A&page=3',
        )
        self.assertEqual(
            api_get.call_args_list[0].args[1],
            {
                'numero_nfse': '12345',
                'cd_remessa': '987',
                'convenio': 'Convênio A',
                'limit': 25,
                'offset': 25,
            },
        )
        self.assertContains(
            response,
            '<input type="hidden" name="numero_nfse" value="12345">',
        )
        self.assertContains(
            response,
            '<input type="hidden" name="cd_remessa" value="987">',
        )
        self.assertContains(
            response,
            '<input type="hidden" name="convenio" value="Convênio A">',
        )
        self.assertContains(response, '<option value="2" selected>2</option>')

    @patch('core.views.api_post')
    def test_envia_conciliacao_para_api(self, api_post):
        response = self.client.post(
            '/financeiro/conciliacao-fiscal-faturamento/',
            {
                'cd_remessa': '10',
                'processo_recebimento': 'PROC-1',
                'notas_json': (
                    '[{"nfse_row_hash": "hash-1", '
                    '"valor_liquido_nfse": "80.00", '
                    '"sn_glosado": false, "valor_glosado": "20.00", '
                    '"data_previsao_recebimento": "2026-08-10"}]'
                ),
            },
        )

        self.assertRedirects(
            response,
            '/financeiro/conciliacao-fiscal-faturamento/',
            fetch_redirect_response=False,
        )
        path, sent_payload = api_post.call_args.args
        self.assertTrue(path.endswith('/remessas/10/conciliar'))
        self.assertEqual(sent_payload['notas'][0]['nfse_row_hash'], 'hash-1')
        self.assertEqual(sent_payload['notas'][0]['valor_alocado'], '80.00')
        self.assertEqual(sent_payload['notas'][0]['valor_glosado'], '20.00')

    @patch('core.views.api_get')
    def test_reutiliza_cache_na_listagem_de_remessas(self, api_get):
        api_get.side_effect = lambda path, params=None: (
            {
                'remessas': [],
                'total': 0,
                'valor_total_nao_conciliado': '0.00',
                'limit': 25,
                'offset': 0,
            }
            if path.endswith('/remessas')
            else {'contas': []}
        )

        self.client.get('/financeiro/conciliacao-fiscal-faturamento/')
        self.client.get('/financeiro/conciliacao-fiscal-faturamento/')

        self.assertEqual(api_get.call_count, 3)
        paths = [call.args[0] for call in api_get.call_args_list]
        self.assertEqual(paths.count(CONCILIACAO_FATURAMENTO_PATH + '/remessas'), 1)
        self.assertEqual(paths.count('/app_glosas/financeiro/contas-bancarias'), 1)
        self.assertEqual(paths.count('/app_glosas/convenios'), 1)

    @patch('core.views.api_post')
    @patch('core.views.api_get')
    def test_atualiza_card_em_cache_apos_conciliar(
        self,
        api_get,
        api_post,
    ):
        api_get.side_effect = lambda path, params=None: (
            {
                'remessas': [
                    {
                        'cd_remessa': 987,
                        'valor_nao_conciliado': '100.00',
                    }
                ],
                'total': 1,
                'valor_total_nao_conciliado': '100.00',
                'limit': 25,
                'offset': 0,
            }
            if path.endswith('/remessas')
            else {'contas': []}
        )
        url = (
            '/financeiro/conciliacao-fiscal-faturamento/'
            '?cd_remessa=987&page=1'
        )
        self.client.get(url)
        api_get.reset_mock()
        api_post.return_value = {
            'cd_remessa': 987,
            'valor_nao_conciliado': '0.00',
            'remessa': {
                'cd_remessa': 987,
                'valor_nao_conciliado': '0.00',
            },
        }

        response = self.client.post(
            url,
            {
                'cd_remessa': '987',
                'processo_recebimento': 'PROC-987',
                'notas_json': (
                    '[{"nfse_row_hash": "hash-1", '
                    '"valor_bruto_remessa": "100.00", '
                    '"sn_glosado": false, "valor_glosado": "0.00", '
                    '"data_previsao_recebimento": "2026-08-10"}]'
                ),
            },
        )

        self.assertRedirects(
            response,
            url,
            fetch_redirect_response=False,
        )
        refreshed = self.client.get(url)
        remessas_paths = [
            call.args[0]
            for call in api_get.call_args_list
            if call.args[0].endswith('/remessas')
        ]
        self.assertEqual(remessas_paths, [])
        self.assertEqual(refreshed.context['remessas'], [])
        self.assertEqual(refreshed.context['total_remessas'], 0)
        self.assertEqual(refreshed.context['valor_total_pendente'], '0.00')


class ConciliacoesSemRecebimentoTests(TestCase):
    def setUp(self):
        cache.clear()
        session = self.client.session
        session['api_access_token'] = 'token-seguro'
        session['api_user'] = {
            'id': 1,
            'nome': 'Financeiro',
            'email': 'financeiro@teste.com',
            'perfil': 'usuario',
            'telas_permitidas': list(SCREEN_KEYS),
        }
        session.save()

    @patch('core.views.api_get')
    def test_renderiza_conciliacoes_sem_recebimento(self, api_get):
        conciliacoes_payload = {
            'conciliacoes': [
                {
                    'cd_remessa': 987,
                    'convenio': 'Convênio Teste',
                    'cnpj_convenio': '98765432000110',
                    'processo_recebimento': 'PROC-100',
                    'data_competencia': '2026-07-01',
                    'valor_remessa': '140.00',
                    'quantidade_nfses_sem_recebimento': 2,
                    'valor_total_glosas': '20.00',
                    'valor_total_impostos': '15.00',
                    'valor_liquido': '120.00',
                    'valor_recebido': '50.00',
                    'valor_pendente': '70.00',
                    'situacao': 'recebimento_parcial',
                    'em_atraso': True,
                    'dias_em_atraso': 3,
                    'notas': [
                        {
                            'id': 10,
                            'numero_nfse': 'NF-100',
                            'tp_conciliacao': 'faturamento',
                            'data_previsao_recebimento': '2026-07-10',
                            'data_criacao': '2026-07-01T10:00:00',
                            'valor_nfse': '100.00',
                            'valor_vinculado_remessa': '80.00',
                            'valor_alocado_nfse': '80.00',
                            'valor_impostos': '0.00',
                            'valor_glosado': '20.00',
                            'valor_recebido': '50.00',
                            'valor_pendente': '30.00',
                            'situacao': 'recebimento_parcial',
                            'em_atraso': True,
                            'dias_em_atraso': 3,
                            'recebimentos': [
                                {
                                    'id': 51,
                                    'data_recebimento': '2026-07-08',
                                    'valor_recebido': '45.00',
                                    'saldo_financeiro': '35.00',
                                    'conta_bancaria_id': 7,
                                    'conta_plano_contas': '1.1.1',
                                    'conta_centro_custo': 'CC-10',
                                    'lancamento_extrato_id': 501,
                                    'lancamento_extrato': {
                                        'id': 501,
                                        'conta_bancaria_id': 7,
                                        'data_lancamento': '2026-07-08',
                                        'valor': '45.00',
                                        'descricao': 'Crédito NFS-e NF-100',
                                        'documento': 'DOC-501',
                                    },
                                    'data_registro': (
                                        '2026-07-08T10:00:00'
                                    ),
                                },
                                {
                                    'id': 52,
                                    'data_recebimento': '2026-07-09',
                                    'valor_recebido': '5.00',
                                    'saldo_financeiro': '30.00',
                                    'conta_bancaria_id': 7,
                                    'conta_plano_contas': '1.1.2',
                                    'conta_centro_custo': 'CC-11',
                                    'lancamento_extrato_id': None,
                                    'lancamento_extrato': None,
                                    'data_registro': (
                                        '2026-07-09T10:00:00'
                                    ),
                                },
                            ],
                        },
                        {
                            'id': 11,
                            'numero_nfse': 'NF-101',
                            'tp_conciliacao': 'faturamento',
                            'data_previsao_recebimento': '2026-07-11',
                            'data_criacao': '2026-07-02T10:00:00',
                            'valor_nfse': '40.00',
                            'valor_vinculado_remessa': '40.00',
                            'valor_alocado_nfse': '40.00',
                            'valor_impostos': '0.00',
                            'valor_glosado': '0.00',
                            'valor_recebido': '0.00',
                            'valor_pendente': '40.00',
                            'situacao': 'sem_recebimento',
                            'em_atraso': True,
                            'dias_em_atraso': 2,
                            'recebimentos': [],
                        },
                    ],
                }
            ],
            'total': 1,
            'total_remessas_sem_recebimento': 1,
            'valor_total_recebido': '50.00',
            'valor_total_pendente': '70.00',
            'limit': 25,
            'offset': 0,
        }
        def resposta(path, params=None):
            if path.endswith('/sem-recebimento'):
                return conciliacoes_payload
            if path.endswith('/convenios'):
                return {
                    'convenios': [
                        {
                            'cd_convenio': 5,
                            'nm_convenio': 'Convênio Teste',
                        },
                    ],
                }
            return {
                'contas': [
                    {
                        'id': 7,
                        'banco': 'Banco Teste',
                        'agencia': '1234',
                        'conta': '56789',
                    }
                ]
            }

        api_get.side_effect = resposta

        response = self.client.get(
            '/financeiro/conciliacoes-sem-recebimento/'
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<h1>Conciliação Financeira</h1>')
        self.assertNotContains(response, '<h1>Conciliação Recebimento</h1>')
        self.assertContains(response, 'name="numero_nfse"')
        self.assertContains(response, 'placeholder="Número da NFS-e"')
        self.assertContains(response, 'name="cd_remessa"')
        self.assertContains(response, 'placeholder="Código da remessa"')
        self.assertContains(
            response,
            '<select class="form-select" name="convenio">',
        )
        self.assertContains(
            response,
            '<option value="Convênio Teste" >Convênio Teste</option>',
        )
        self.assertContains(response, 'name="processo_recebimento"')
        self.assertContains(
            response,
            'placeholder="Processo de recebimento"',
        )
        self.assertContains(response, '>Expandir</button>')
        self.assertContains(response, '>Colapsar todos</button>')
        self.assertContains(
            response,
            '@expand-all-financial-reconciliations.window',
        )
        self.assertContains(
            response,
            '@collapse-all-financial-reconciliations.window',
        )
        content = response.content.decode()
        self.assertLess(
            content.index('class="collapse-actions"'),
            content.index('class="results-toolbar finance-results-toolbar"'),
        )
        self.assertLess(
            content.index('>Expandir</button>'),
            content.index('>Colapsar todos</button>'),
        )
        self.assertContains(response, '<span>REMESSAS PENDENTES</span>')
        self.assertContains(response, '<span>TOTAL RECEBIDO</span>')
        self.assertContains(response, '<span>VALOR PENDENTE</span>')
        self.assertContains(response, '<strong>R$ 50,00</strong>')
        self.assertLess(
            content.index('<span>REMESSAS PENDENTES</span>'),
            content.index('<span>TOTAL RECEBIDO</span>'),
        )
        self.assertLess(
            content.index('<span>TOTAL RECEBIDO</span>'),
            content.index('<span>VALOR PENDENTE</span>'),
        )
        self.assertContains(response, 'NF-100')
        self.assertContains(response, 'NF-101')
        self.assertContains(response, 'Convênio Teste')
        self.assertContains(response, 'PROC-100')
        self.assertContains(response, '<small>Remessa</small>')
        self.assertContains(response, '<strong>987</strong>')
        self.assertContains(response, '<small>NFS-e pendentes</small>')
        self.assertContains(response, '<strong>2</strong>')
        summary_start = content.index('class="finance-pending-summary"')
        summary_end = content.index('</button>', summary_start)
        summary_content = content[summary_start:summary_end]
        summary_labels = (
            'Remessa',
            'Competência',
            'Convênio',
            'NFS-e pendentes',
            'Valor remessa',
            'Valor glosa',
            'Total das retenções',
            'Valor líquido',
        )
        summary_positions = [
            summary_content.index(f'<small>{label}</small>')
            for label in summary_labels
        ]
        self.assertEqual(summary_positions, sorted(summary_positions))
        self.assertNotIn('Valor recebido', summary_content)
        self.assertNotIn('Valor pendente', summary_content)
        self.assertNotIn('finance-pending-statuses', summary_content)
        self.assertContains(response, '<strong>R$ 20,00</strong>')
        self.assertContains(response, '<strong>R$ 15,00</strong>')
        self.assertContains(
            response,
            '<small>Valor conciliado</small><strong>R$ 80,00</strong>',
            count=2,
            html=True,
        )
        self.assertContains(
            response,
            '<small>Valor recebido</small><strong>R$ 45,00</strong>',
            html=True,
        )
        self.assertContains(
            response,
            '<small>Saldo financeiro</small><strong>R$ 35,00</strong>',
            html=True,
        )
        self.assertContains(response, '<strong>R$ 120,00</strong>')
        self.assertContains(
            response,
            'finance-pending-meta finance-pending-meta--process',
        )
        self.assertContains(
            response,
            '<small>Processo de recebimento</small>',
        )
        self.assertNotContains(response, '<small>CNPJ do convênio</small>')
        self.assertNotContains(response, '<small>Total de glosas</small>')
        self.assertContains(response, 'R$ 100,00')
        self.assertContains(
            response,
            'class="finance-note-card finance-pending-card"',
            count=1,
        )
        self.assertNotContains(response, '>Receber</button>')
        self.assertNotContains(response, '>Novo recebimento</button>')
        self.assertContains(response, 'Novo recebimento financeiro')
        self.assertContains(
            response,
            'Informe o saldo de R$ 30,00 ou qualquer valor inferior.',
        )
        self.assertContains(
            response,
            'class="finance-pending-receipt-form '
            'finance-new-receipt-record"',
        )
        new_receipt_form = content.index(
            'class="finance-pending-receipt-form '
            'finance-new-receipt-record"'
        )
        self.assertNotIn(
            'x-show=',
            content[new_receipt_form:content.index('>', new_receipt_form)],
        )
        self.assertContains(response, 'RECEBIMENTO *')
        self.assertContains(response, 'Valor recebido *')
        self.assertNotContains(response, 'readonly aria-readonly="true"')
        self.assertContains(response, '@input="updateMoney($event)"')
        self.assertNotContains(response, 'Valor já recebido')
        self.assertNotContains(response, '<small>Tipo</small>')
        self.assertNotContains(response, '<small>Valor do recebimento</small>')
        self.assertNotContains(response, 'Recebimentos financeiros anteriores')
        self.assertContains(
            response,
            'finance-pending-invoice finance-pending-invoice--history',
            count=3,
        )
        combined_row = content.index(
            'finance-pending-invoice finance-pending-invoice--history'
        )
        receipt_edit_form = content.index(
            'finance-previous-receipt-edit-form',
            combined_row,
        )
        combined_content = content[combined_row:receipt_edit_form]
        detail_labels = (
            'NFS-e',
            'Valor NFS-e',
            'Previsão',
            'Valor conciliado',
            'Valor recebido',
            'Saldo financeiro',
            'RECEBIMENTO',
            'CONTA BANCÁRIA',
            'CONTA PLANO CONTAS',
            'CONTA CENTRO CUSTO',
            'LANÇAMENTO FINANCEIRO',
        )
        for label in detail_labels:
            self.assertIn(f'<small>{label}</small>', combined_content)
        detail_positions = [
            combined_content.index(f'<small>{label}</small>')
            for label in detail_labels
        ]
        self.assertEqual(detail_positions, sorted(detail_positions))
        self.assertContains(response, '<small>RECEBIMENTO</small>', count=3)
        self.assertContains(response, '<small>CONTA BANCÁRIA</small>', count=3)
        self.assertContains(response, '<small>CONTA PLANO CONTAS</small>', count=3)
        self.assertContains(response, '<small>CONTA CENTRO CUSTO</small>', count=3)
        self.assertContains(response, '<small>LANÇAMENTO FINANCEIRO</small>', count=3)
        self.assertContains(response, 'R$ 45,00')
        self.assertContains(response, 'R$ 35,00')
        self.assertContains(response, 'R$ 5,00')
        self.assertContains(response, '08/07/2026')
        self.assertContains(response, 'Banco Teste · Ag. 1234 · C/C 56789')
        self.assertContains(response, 'class="finance-bank-data"')
        self.assertContains(
            response,
            '<span>Banco Teste</span><em>Ag. 1234 · C/C 56789</em>',
        )
        self.assertContains(response, '1.1.1')
        self.assertContains(response, 'CC-10')
        self.assertContains(response, 'Crédito NFS-e NF-100')
        self.assertNotContains(response, 'RECEBIDA FINANCEIRAMENTE')
        self.assertNotContains(response, 'class="finance-pending-statuses"')
        self.assertContains(response, '<small>Saldo financeiro</small>')
        self.assertContains(response, 'name="form_action" value="editar_recebimento"')
        self.assertContains(response, 'name="form_action" value="excluir_recebimento"')
        self.assertContains(response, 'Salvar recebimento')
        self.assertContains(
            response,
            'O valor recebido não pode exceder o saldo',
        )
        self.assertContains(response, 'Conta bancária *')
        self.assertContains(response, 'CONTA PLANO CONTAS')
        self.assertContains(response, 'CONTA CENTRO CUSTO')
        self.assertContains(response, 'Lançamento no extrato')
        self.assertContains(response, 'Banco Teste · Ag. 1234 · C/C 56789')
        self.assertContains(response, 'recebimentoPendenteForm(')
        self.assertContains(response, 'loadLancamentos()')
        self.assertContains(response, 'Editar conciliação')
        self.assertNotContains(response, 'Inativar conciliação')
        self.assertContains(response, '>Excluir</button>')
        self.assertContains(
            response,
            'class="nav-subitem is-active" '
            'href="/financeiro/conciliacoes-sem-recebimento/"',
        )
        self.assertEqual(
            api_get.call_args_list[0].args[1],
            {
                'numero_nfse': None,
                'cd_remessa': None,
                'convenio': None,
                'processo_recebimento': None,
                'limit': 25,
                'offset': 0,
            },
        )

    @patch('core.views.api_get')
    def test_paginacao_mantem_filtro(self, api_get):
        conciliacoes_payload = {
            'conciliacoes': [],
            'total': 250,
            'total_remessas_sem_recebimento': 250,
            'valor_total_pendente': '5000.00',
            'limit': 25,
            'offset': 25,
        }
        api_get.side_effect = lambda path, params=None: (
            conciliacoes_payload
            if path.endswith('/sem-recebimento')
            else {'contas': []}
        )

        response = self.client.get(
            '/financeiro/conciliacoes-sem-recebimento/',
            {
                'numero_nfse': '5032',
                'cd_remessa': '987',
                'convenio': 'BRADESCO',
                'processo_recebimento': 'PROC-987',
                'page': 2,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context['pagination']['previous_url'],
            '?numero_nfse=5032&cd_remessa=987&convenio=BRADESCO&'
            'processo_recebimento=PROC-987&page=1',
        )
        self.assertEqual(
            response.context['pagination']['next_url'],
            '?numero_nfse=5032&cd_remessa=987&convenio=BRADESCO&'
            'processo_recebimento=PROC-987&page=3',
        )
        self.assertEqual(
            api_get.call_args_list[0].args[1],
            {
                'numero_nfse': '5032',
                'cd_remessa': '987',
                'convenio': 'BRADESCO',
                'processo_recebimento': 'PROC-987',
                'limit': 25,
                'offset': 25,
            },
        )
        self.assertContains(
            response,
            '<input type="hidden" name="numero_nfse" value="5032">',
        )
        self.assertContains(
            response,
            '<input type="hidden" name="cd_remessa" value="987">',
        )
        self.assertContains(
            response,
            '<input type="hidden" name="convenio" value="BRADESCO">',
        )
        self.assertContains(
            response,
            '<input type="hidden" name="processo_recebimento" '
            'value="PROC-987">',
        )
        self.assertContains(response, '<option value="2" selected>2</option>')

    def test_monta_payload_de_recebimento(self):
        payload = build_recebimento_remessa_payload(
            {
                'cd_remessa': '987',
                'numero_nfse': ' NF-100 ',
                'data_recebimento': '2026-07-13',
                'valor_recebido': 'R$ 1.234,56',
                'conta_bancaria_id': '7',
                'conta_plano_contas': ' 1.1.1 ',
                'conta_centro_custo': ' CC-10 ',
                'lancamento_extrato_id': '22',
            }
        )

        self.assertEqual(payload['cd_remessa'], 987)
        self.assertEqual(payload['numero_nfse'], 'NF-100')
        self.assertEqual(payload['valor_recebido'], '1234.56')
        self.assertEqual(payload['conta_bancaria_id'], 7)
        self.assertEqual(payload['conta_plano_contas'], '1.1.1')
        self.assertEqual(payload['conta_centro_custo'], 'CC-10')
        self.assertEqual(payload['lancamento_extrato_id'], 22)

    def test_monta_payload_de_edicao(self):
        payload = build_edicao_conciliacao_payload(
            {
                'processo_recebimento': ' PROC-EDITADO ',
                'data_previsao_recebimento': '2026-08-15',
                'cd_remessa': ['987'],
                'valor_glosado_987': 'R$ 10,00',
                'valor_impostos_987': 'R$ 5,00',
                'valor_recebido_987': 'R$ 90,00',
            }
        )

        self.assertEqual(payload['processo_recebimento'], 'PROC-EDITADO')
        self.assertEqual(
            payload['data_previsao_recebimento'],
            '2026-08-15',
        )
        self.assertEqual(
            payload['remessas'],
            [
                {
                    'cd_remessa': 987,
                    'valor_glosado': '10.00',
                    'valor_impostos': '5.00',
                    'valor_recebido': '90.00',
                }
            ],
        )

    @patch('core.views.clear_filter_caches')
    @patch('core.views.api_patch')
    def test_edita_recebimento_financeiro(
        self,
        api_patch,
        clear_filter_caches,
    ):
        response = self.client.post(
            '/financeiro/conciliacoes-sem-recebimento/?q=987',
            {
                'form_action': 'editar_recebimento',
                'recebimento_id': '51',
                'conciliacao_id': '10',
                'cd_remessa': '987',
                'numero_nfse': 'NF-100',
                'data_recebimento': '2026-07-13',
                'valor_recebido': 'R$ 52,85',
                'conta_bancaria_id': '7',
                'conta_plano_contas': '1.1.1',
                'conta_centro_custo': 'CC-10',
                'lancamento_extrato_id': '501',
            },
        )

        self.assertRedirects(
            response,
            '/financeiro/conciliacoes-sem-recebimento/?q=987',
            fetch_redirect_response=False,
        )
        api_patch.assert_called_once_with(
            CONCILIACAO_FATURAMENTO_PATH + '/recebimentos-remessas/51',
            {
                'conciliacao_id': 10,
                'cd_remessa': 987,
                'numero_nfse': 'NF-100',
                'data_recebimento': '2026-07-13',
                'valor_recebido': '52.85',
                'conta_bancaria_id': 7,
                'conta_plano_contas': '1.1.1',
                'conta_centro_custo': 'CC-10',
                'lancamento_extrato_id': 501,
            },
        )
        clear_filter_caches.assert_called_once_with()

    @patch('core.views.clear_filter_caches')
    @patch('core.views.api_delete')
    def test_exclui_recebimento_financeiro(
        self,
        api_delete,
        clear_filter_caches,
    ):
        response = self.client.post(
            '/financeiro/conciliacoes-sem-recebimento/?q=987',
            {
                'form_action': 'excluir_recebimento',
                'recebimento_id': '51',
            },
        )

        self.assertRedirects(
            response,
            '/financeiro/conciliacoes-sem-recebimento/?q=987',
            fetch_redirect_response=False,
        )
        api_delete.assert_called_once_with(
            CONCILIACAO_FATURAMENTO_PATH + '/recebimentos-remessas/51'
        )
        clear_filter_caches.assert_called_once_with()

    @patch('core.views.clear_filter_caches')
    @patch('core.views.api_put')
    def test_edita_conciliacao_pendente(
        self,
        api_put,
        clear_filter_caches,
    ):
        response = self.client.post(
            '/financeiro/conciliacoes-sem-recebimento/?q=987',
            {
                'form_action': 'editar_conciliacao',
                'conciliacao_id': '10',
                'processo_recebimento': 'PROC-NOVO',
                'data_previsao_recebimento': '2026-08-15',
                'cd_remessa': '987',
                'valor_glosado_987': 'R$ 10,00',
                'valor_impostos_987': 'R$ 5,00',
                'valor_recebido_987': 'R$ 90,00',
            },
        )

        self.assertRedirects(
            response,
            '/financeiro/conciliacoes-sem-recebimento/?q=987',
            fetch_redirect_response=False,
        )
        api_put.assert_called_once_with(
            CONCILIACAO_FATURAMENTO_PATH + '/conciliacoes/10',
            {
                'processo_recebimento': 'PROC-NOVO',
                'data_previsao_recebimento': '2026-08-15',
                'remessas': [
                    {
                        'cd_remessa': 987,
                        'valor_glosado': '10.00',
                        'valor_impostos': '5.00',
                        'valor_recebido': '90.00',
                    }
                ],
            },
        )
        clear_filter_caches.assert_called_once_with()

    @patch('core.views.api_get')
    def test_edicao_exibe_valores_da_remessa(self, api_get):
        api_get.side_effect = lambda path, params=None: (
            {
                'conciliacoes': [
                    {
                        'cd_remessa': 987,
                        'convenio': 'Convênio Teste',
                        'cnpj_convenio': '98765432000110',
                        'processo_recebimento': 'PROC-100',
                        'data_competencia': '2026-07-01',
                        'valor_remessa': '100.00',
                        'quantidade_nfses_sem_recebimento': 1,
                        'valor_total_glosas': '10.00',
                        'valor_total_impostos': '5.00',
                        'valor_liquido': '85.00',
                        'valor_recebido': '0.00',
                        'valor_pendente': '90.00',
                        'situacao': 'sem_recebimento',
                        'em_atraso': False,
                        'dias_em_atraso': 0,
                        'notas': [
                            {
                                'id': 10,
                                'numero_nfse': 'NF-100',
                                'tp_conciliacao': 'faturamento',
                                'data_previsao_recebimento': '2026-08-15',
                                'data_criacao': '2026-07-01T10:00:00',
                                'valor_nfse': '100.00',
                                'valor_vinculado_remessa': '100.00',
                                'valor_alocado_nfse': '85.00',
                                'valor_impostos': '5.00',
                                'valor_glosado': '10.00',
                                'valor_pendente': '85.00',
                                'situacao': 'sem_recebimento',
                                'em_atraso': False,
                                'dias_em_atraso': 0,
                            }
                        ],
                    }
                ],
                'total': 1,
                'total_remessas_sem_recebimento': 1,
                'valor_total_pendente': '90.00',
            }
            if path.endswith('/sem-recebimento')
            else {'contas': []}
        )

        response = self.client.get(
            '/financeiro/conciliacoes-sem-recebimento/'
        )

        self.assertContains(response, 'finance-pending-invoice-actions')
        self.assertNotContains(response, '>Receber</button>')
        self.assertContains(
            response,
            'class="finance-pending-receipt-form '
            'finance-new-receipt-record"',
        )
        self.assertContains(response, "'Editar'")
        self.assertContains(response, '>Excluir</button>')
        self.assertNotContains(response, 'Registrar recebimento')
        self.assertNotContains(response, 'Editar conciliação</button>')
        self.assertNotContains(response, 'Inativar conciliação')
        self.assertContains(response, 'Valor glosa *')
        self.assertContains(response, 'Total das retenções *')
        self.assertContains(response, 'Valor líquido conciliado *')
        self.assertContains(response, 'Valor recebido *', count=1)
        self.assertContains(response, 'name="valor_glosado_987"')
        self.assertContains(response, 'value="R$ 10,00"')
        self.assertContains(response, 'name="valor_impostos_987"')
        self.assertContains(response, 'value="R$ 5,00"')
        self.assertContains(response, 'name="valor_recebido_987"')
        self.assertContains(response, 'value="R$ 85,00"')

    @patch('core.views.clear_filter_caches')
    @patch('core.views.api_delete')
    def test_inativa_conciliacao_pendente(
        self,
        api_delete,
        clear_filter_caches,
    ):
        response = self.client.post(
            '/financeiro/conciliacoes-sem-recebimento/?page=2',
            {
                'form_action': 'inativar_conciliacao',
                'conciliacao_id': '10',
            },
        )

        self.assertRedirects(
            response,
            '/financeiro/conciliacoes-sem-recebimento/?page=2',
            fetch_redirect_response=False,
        )
        api_delete.assert_called_once_with(
            CONCILIACAO_FATURAMENTO_PATH + '/conciliacoes/10'
        )
        clear_filter_caches.assert_called_once_with()

    @patch('core.views.api_post')
    def test_registra_recebimento_e_preserva_filtros(self, api_post):
        response = self.client.post(
            '/financeiro/conciliacoes-sem-recebimento/?q=987&page=2',
            {
                'cd_remessa': '987',
                'numero_nfse': 'NF-100',
                'data_recebimento': '2026-07-13',
                'valor_recebido': 'R$ 100,00',
                'conta_bancaria_id': '7',
                'conta_plano_contas': '1.1.1',
                'conta_centro_custo': 'CC-10',
                'lancamento_extrato_id': '22',
            },
        )

        self.assertRedirects(
            response,
            '/financeiro/conciliacoes-sem-recebimento/?q=987&page=2',
            fetch_redirect_response=False,
        )
        path, payload = api_post.call_args.args
        self.assertTrue(path.endswith('/recebimentos-remessas'))
        self.assertEqual(payload['cd_remessa'], 987)
        self.assertEqual(payload['valor_recebido'], '100.00')
        self.assertEqual(payload['conta_bancaria_id'], 7)
        self.assertEqual(payload['lancamento_extrato_id'], 22)


class ConciliacoesFinanceirasTests(TestCase):
    def setUp(self):
        cache.clear()
        session = self.client.session
        session['api_access_token'] = 'token-seguro'
        session['api_user'] = {
            'id': 1,
            'nome': 'Financeiro',
            'email': 'financeiro@teste.com',
            'perfil': 'usuario',
            'telas_permitidas': list(SCREEN_KEYS),
        }
        session.save()

    @patch('core.views.api_get')
    def test_consulta_conciliacao_recebimento_e_auditoria(self, api_get):
        api_get.side_effect = lambda path, params=None: (
            {
                'conciliacoes': [
                    {
                        'cd_remessa': 987,
                        'data_competencia': '2026-06-01',
                        'valor_remessa': '120.00',
                        'valor_alocado_nfse': '100.00',
                        'valor_glosado': '20.00',
                        'convenio': 'Convênio Teste',
                        'cnpj_convenio': '98765432000110',
                        'processo_recebimento': 'PROC-100',
                        'ativo': True,
                        'situacao_recebimento': 'recebido',
                        'notas': [
                            {
                                'id': 10,
                                'numero_nfse': 'NF-100',
                                'tipo_conciliacao': 'faturamento',
                                'valor_nfse': '100.00',
                                'valor_vinculado_remessa': '120.00',
                                'valor_alocado_nfse': '100.00',
                                'valor_glosado': '20.00',
                                'data_previsao_recebimento': '2026-07-10',
                                'data_recebimento': '2026-07-13',
                                'data_criacao': '2026-07-01T10:00:00',
                                'data_atualizacao': '2026-07-13T09:30:00',
                                'data_inativacao': None,
                                'ativo': True,
                                'situacao_recebimento': 'recebido',
                                'usuario_criacao': {
                                    'id': 1,
                                    'nome': 'Ana Financeiro',
                                    'email': 'ana@teste.com',
                                },
                                'usuario_atualizacao': {
                                    'id': 2,
                                    'nome': 'Bruno Recebimento',
                                    'email': 'bruno@teste.com',
                                },
                                'usuario_inativacao': None,
                                'recebimentos': [
                                    {
                                        'id': 5,
                                        'cd_remessa': 987,
                                        'data_recebimento': '2026-07-13',
                                        'valor_recebido': '100.00',
                                        'conta_bancaria_id': 7,
                                        'conta_plano_contas': '1.1.1',
                                        'conta_centro_custo': 'CC-10',
                                        'lancamento_extrato_id': 22,
                                        'data_registro': (
                                            '2026-07-13T09:30:00'
                                        ),
                                        'usuario': {
                                            'id': 2,
                                            'nome': 'Bruno Recebimento',
                                            'email': 'bruno@teste.com',
                                        },
                                    }
                                ],
                            }
                        ],
                        'auditoria': [
                            {
                                'id': 1,
                                'conciliacao_origem_id': 9,
                                'numero_nfse': 'NF-100',
                                'acao': 'criacao',
                                'usuario': {
                                    'id': 1,
                                    'nome': 'Ana Financeiro',
                                    'email': 'ana@teste.com',
                                },
                                'data_operacao': '2026-07-01T10:00:00',
                            },
                            {
                                'id': 2,
                                'conciliacao_origem_id': 10,
                                'numero_nfse': 'NF-100',
                                'acao': 'edicao',
                                'usuario': {
                                    'id': 2,
                                    'nome': 'Bruno Recebimento',
                                    'email': 'bruno@teste.com',
                                },
                                'dados_anteriores': {
                                    'processo_recebimento': 'PROC-ANTIGO',
                                    'data_previsao_recebimento': '2026-07-09',
                                    'ativo': True,
                                    'remessas': [
                                        {
                                            'cd_remessa': 987,
                                            'valor_alocado_nfse': '90.00',
                                            'valor_glosado': '30.00',
                                        }
                                    ],
                                },
                                'dados_novos': {
                                    'processo_recebimento': 'PROC-100',
                                    'data_previsao_recebimento': '2026-07-10',
                                    'ativo': True,
                                    'remessas': [
                                        {
                                            'cd_remessa': 987,
                                            'valor_alocado_nfse': '100.00',
                                            'valor_glosado': '20.00',
                                        }
                                    ],
                                },
                                'data_operacao': '2026-07-10T08:30:00',
                            },
                            {
                                'id': 3,
                                'conciliacao_origem_id': 10,
                                'numero_nfse': 'NF-100',
                                'acao': 'recebimento',
                                'usuario': {
                                    'id': 2,
                                    'nome': 'Bruno Recebimento',
                                    'email': 'bruno@teste.com',
                                },
                                'data_operacao': '2026-07-13T09:30:00',
                            },
                            {
                                'id': 4,
                                'conciliacao_origem_id': 9,
                                'numero_nfse': 'NF-100',
                                'acao': 'inativacao',
                                'usuario': {
                                    'id': 1,
                                    'nome': 'Ana Financeiro',
                                    'email': 'ana@teste.com',
                                },
                                'data_operacao': '2026-07-14T11:00:00',
                            },
                        ],
                    }
                ],
                'total': 30,
                'total_ativas': 1,
                'total_inativas': 0,
                'total_recebidas': 1,
                'total_sem_recebimento': 0,
                'limit': 25,
                'offset': 0,
            }
            if path.endswith('/conciliacoes')
            else {
                'contas': [
                    {
                        'id': 7,
                        'banco': 'Banco Teste',
                        'agencia': '1234',
                        'conta': '56789',
                        'digito': '0',
                    }
                ]
            }
        )

        response = self.client.get(
            '/financeiro/conciliacoes/',
            {
                'numero_nfse': 'NF-100',
                'cd_remessa': '987',
                'convenio': 'Convênio Teste',
                'processo_recebimento': 'PROC-100',
                'situacao': 'recebido',
                'incluir_inativas': '1',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Consultar conciliações')
        self.assertContains(response, '<span class="nav-label">Auditória</span>')
        self.assertContains(response, 'name="numero_nfse"')
        self.assertContains(response, 'name="cd_remessa"')
        self.assertContains(
            response,
            '<select class="form-select" name="convenio">',
        )
        self.assertContains(
            response,
            '<option value="Convênio Teste" selected>'
            'Convênio Teste</option>',
        )
        self.assertContains(response, 'name="processo_recebimento"')
        self.assertContains(response, '>Expandir</button>')
        self.assertContains(response, '>Colapsar todos</button>')
        self.assertContains(response, '@expand-all-finance-history.window')
        self.assertContains(response, '@collapse-all-finance-history.window')
        self.assertContains(response, 'id="finance-history-page-select"')
        self.assertContains(response, '<option value="1" selected>1</option>')
        self.assertContains(response, '<span>de 2</span>')
        self.assertNotContains(response, 'class="pagination-footer"')
        content = response.content.decode()
        self.assertLess(
            content.index('class="collapse-actions"'),
            content.index('class="results-toolbar finance-results-toolbar"'),
        )
        self.assertLess(
            content.index('>Expandir</button>'),
            content.index('>Colapsar todos</button>'),
        )
        self.assertLess(
            content.index('finance-history-totals'),
            content.index('id="finance-history-page-select"'),
        )
        css = Path(finders.find('css/app.css')).read_text()
        self.assertIn(
            '.finance-list-heading > div:not(.collapse-actions),',
            css,
        )
        self.assertContains(response, 'Remessa 987')
        self.assertContains(response, 'Notas fiscais conciliadas')
        self.assertContains(response, 'NF-100')
        self.assertNotContains(response, '<small>TIPO</small>')
        self.assertContains(
            response,
            'class="finance-history-data-grid finance-history-note-grid"',
        )
        self.assertContains(
            response,
            'class="finance-history-data-grid finance-history-receipt-grid"',
        )
        for label in (
            'VALOR NFS-e',
            'VALOR CONCILIADO',
            'VALOR ALOCADO NA NFS-e',
            'VALOR GLOSADO',
            'PREVISÃO',
            'SITUAÇÃO',
            'CRIADA EM',
            'CRIADA POR',
            'ÚLTIMA ALTERAÇÃO',
        ):
            self.assertContains(response, f'<small>{label}</small>')
        self.assertIn(
            '.finance-history-note-grid {\n  grid-template-columns:',
            css,
        )
        self.assertIn(
            '.finance-history-receipt-grid {\n  grid-template-columns:',
            css,
        )
        self.assertContains(response, 'PROC-100')
        self.assertContains(response, 'Ana Financeiro')
        self.assertContains(response, 'Bruno Recebimento')
        self.assertContains(response, 'Banco Teste · Ag. 1234 · C/C 56789-0')
        self.assertContains(response, 'Conciliação criada')
        self.assertContains(response, 'Conciliação editada')
        self.assertContains(response, 'Recebimento registrado')
        self.assertContains(response, 'Conciliação inativada')
        self.assertContains(response, 'finance-history-event is-created')
        self.assertContains(response, 'finance-history-event is-updated')
        self.assertContains(response, 'finance-history-event is-received')
        self.assertContains(response, 'finance-history-event is-deleted')
        self.assertContains(response, '>INCLUSÃO</small>')
        self.assertContains(response, '>MODIFICAÇÃO</small>')
        self.assertContains(response, '>RECEBIMENTO</small>')
        self.assertContains(response, '>EXCLUSÃO</small>')
        self.assertContains(
            response,
            'class="finance-history-event-context"',
            count=4,
        )
        self.assertContains(response, '<small>NFS-e</small>')
        self.assertContains(response, '<small>USUÁRIO</small>', count=4)
        self.assertContains(response, '<small>TIPO DE AÇÃO</small>', count=4)
        self.assertContains(response, 'Campos alterados')
        self.assertContains(response, 'CONCILIAÇÃO ANTERIOR')
        self.assertContains(response, 'Valor recebido · remessa 987')
        self.assertContains(response, 'R$ 90,00')
        self.assertContains(response, 'R$ 100,00')
        self.assertContains(
            response,
            'class="nav-subitem is-active" '
            'href="/financeiro/conciliacoes/"',
        )
        self.assertEqual(
            api_get.call_args_list[0].kwargs['params'],
            {
                'numero_nfse': 'NF-100',
                'cd_remessa': '987',
                'convenio': 'Convênio Teste',
                'processo_recebimento': 'PROC-100',
                'situacao': 'recebido',
                'incluir_inativas': 'true',
                'limit': 25,
                'offset': 0,
            },
        )
        self.assertEqual(
            response.context['pagination']['next_url'],
            '?numero_nfse=NF-100&cd_remessa=987&'
            'convenio=Conv%C3%AAnio+Teste&'
            'processo_recebimento=PROC-100&situacao=recebido&'
            'incluir_inativas=true&page=2',
        )
        response_page_2 = self.client.get(
            '/financeiro/conciliacoes/',
            {
                'numero_nfse': 'NF-100',
                'cd_remessa': '987',
                'convenio': 'Convênio Teste',
                'processo_recebimento': 'PROC-100',
                'situacao': 'recebido',
                'incluir_inativas': '1',
                'page': '2',
            },
        )
        self.assertContains(
            response_page_2,
            '<option value="2" selected>2</option>',
        )
        chamadas_historico = [
            chamada
            for chamada in api_get.call_args_list
            if chamada.args[0].endswith('/conciliacoes')
        ]
        self.assertEqual(len(chamadas_historico), 2)
        self.assertEqual(
            chamadas_historico[1].kwargs['params'],
            {
                'numero_nfse': 'NF-100',
                'cd_remessa': '987',
                'convenio': 'Convênio Teste',
                'processo_recebimento': 'PROC-100',
                'situacao': 'recebido',
                'incluir_inativas': 'true',
                'limit': 25,
                'offset': 25,
            },
        )


class CadastrarNotaTests(TestCase):
    def setUp(self):
        cache.clear()
        session = self.client.session
        session['api_access_token'] = 'token-seguro'
        session['api_user'] = {
            'id': 4,
            'nome': 'Amoras',
            'email': 'raffaekk@gmail.com',
            'perfil': 'usuario',
            'telas_permitidas': list(SCREEN_KEYS),
        }
        session.save()

    def atendimento_payload(self):
        return {
            'codigo_atendimento': 123456,
            'codigo_paciente': 789,
            'codigo_convenio': 20,
            'nm_paciente': 'MARIA DA SILVA',
            'convenio': 'CONVÊNIO TESTE',
            'nr_cpf': '12345678901',
            'nr_cep': '60000000',
            'ds_endereco': 'RUA TESTE',
            'nr_endereco': '100',
            'nm_bairro': 'CENTRO',
            'ds_complemento': 'APTO 10',
            'email': 'maria@example.com',
            'nr_fone': '85999999999',
            'tipo_atendimento': 'Ambulatório',
            'procedimentos_atendimento': [
                {
                    'codigo': '40304361',
                    'descricao': 'ECOCARDIOGRAMA TRANSTORÁCICO',
                    'grupo': 'EXAMES CARDIOLÓGICOS',
                    'convenio': 'PARTICULAR',
                    'convenio_elegivel_nfse': True,
                    'quantidade': '1',
                    'valor_total': '385.50',
                    'realizado_em': '2026-07-23T10:30:00',
                    'prestador': 'DR. TESTE',
                },
            ],
            'procedimentos_atendimento_disponiveis': True,
            'valor_total_procedimentos': '385.50',
            'valor_total_procedimentos_elegiveis_nfse': '385.50',
            'solicitacoes_existentes': [],
        }

    def lista_payload(
        self,
        solicitacoes=None,
        total=0,
        offset=0,
        resumo_status=None,
    ):
        return {
            'solicitacoes': solicitacoes or [],
            'resumo_status': resumo_status or [],
            'total': total,
            'limit': 10,
            'offset': offset,
        }

    def workflow_payload(self, status='PENDENTE_VALIDACAO'):
        return {
            **self.atendimento_payload(),
            'id': 7,
            'local': 'Clinica 1',
            'procedimento': 'Consulta cardiológica',
            'valor_nota': '60.75',
            'empresa_emissora_id': 3,
            'cnpj_emissor': '05613278000158',
            'razao_social_emissor': (
                'PRONTOCARDIO PRONTOATENDIMENTO CARDIOLOGICO LTDA'
            ),
            'usuario_id': 4,
            'cadastrado_por': 'Amoras',
            'data_criacao': '2026-07-23T14:30:00',
            'workflow_id': 9,
            'status': status,
            'validacao': (
                status if status in {'VALIDADA', 'RECUSADA'} else None
            ),
            'motivo_recusa': None,
            'validado_por_id': 4 if status != 'PENDENTE_VALIDACAO' else None,
            'validado_por': (
                'Amoras' if status != 'PENDENTE_VALIDACAO' else None
            ),
            'validado_em': (
                '2026-07-23T15:00:00'
                if status != 'PENDENTE_VALIDACAO'
                else None
            ),
            'workflow_atualizado_em': '2026-07-23T14:30:00',
            'procedimentos_atendimento': [],
            'procedimentos_atendimento_disponiveis': True,
        }

    def empresas_payload(self):
        return {
            'empresas': [
                {
                    'id': 3,
                    'cnpj': '05613278000158',
                    'razao_social': (
                        'PRONTOCARDIO PRONTOATENDIMENTO CARDIOLOGICO LTDA'
                    ),
                    'ativo': True,
                    'atualizado_por': 'Amoras',
                    'data_atualizacao': '2026-07-26T18:30:00',
                },
                {
                    'id': 4,
                    'cnpj': '08711085000128',
                    'razao_social': (
                        'PRONTOCARDIO SERVICOS MEDICOS HOSPITALARES LTDA'
                    ),
                    'ativo': True,
                    'atualizado_por': None,
                    'data_atualizacao': '2026-07-26T18:30:00',
                },
            ],
            'total': 2,
        }

    def acompanhamento_particular_payload(self):
        solicitacao_pendente = {
            **self.workflow_payload('PENDENTE_VALIDACAO'),
            'id': 17,
            'codigo_atendimento': 123456,
            'codigo_paciente': 789,
            'nm_paciente': 'MARIA DA SILVA',
            'tipo_atendimento': 'Ambulatório',
        }
        solicitacao_processando = {
            **self.workflow_payload('EMISSAO_SOLICITADA'),
            'id': 18,
            'codigo_atendimento': 123457,
            'codigo_paciente': 790,
            'nm_paciente': 'JOÃO EM PROCESSAMENTO',
            'tipo_atendimento': 'Urgência',
        }
        solicitacao_emitida = {
            **self.workflow_payload('EMITIDA'),
            'id': 19,
            'codigo_atendimento': 123458,
            'codigo_paciente': 791,
            'nm_paciente': 'ANA COM NOTA',
            'tipo_atendimento': 'Externo',
        }
        return {
            'atendimentos': [
                {
                    'codigo_atendimento': 123456,
                    'codigo_paciente': 789,
                    'codigo_convenio': 3,
                    'nome_paciente': 'MARIA DA SILVA',
                    'convenio': 'PARTICULAR',
                    'tipo_atendimento': 'Ambulatório',
                    'data_atendimento': '2026-07-29T00:00:00',
                    'data_alta': None,
                    'valor_conta': '385.50',
                    'quantidade_lancamentos': 2,
                    'status': 'PENDENTE_VALIDACAO',
                    'solicitacao_id': 17,
                    'workflow_status': 'PENDENTE_VALIDACAO',
                    'emissao_id': None,
                    'emissao_status': None,
                    'numero_nfse': None,
                    'erro_emissao': None,
                    'arquivo_disponivel': False,
                    'solicitada_em': None,
                    'atualizada_em': None,
                    'solicitacao': solicitacao_pendente,
                },
                {
                    'codigo_atendimento': 123457,
                    'codigo_paciente': 790,
                    'codigo_convenio': 3,
                    'nome_paciente': 'JOÃO EM PROCESSAMENTO',
                    'convenio': 'PARTICULAR',
                    'tipo_atendimento': 'Urgência',
                    'data_atendimento': '2026-07-29T00:00:00',
                    'data_alta': None,
                    'valor_conta': '120.00',
                    'quantidade_lancamentos': 1,
                    'status': 'PROCESSANDO',
                    'solicitacao_id': 18,
                    'workflow_status': 'EMISSAO_SOLICITADA',
                    'emissao_id': 41,
                    'emissao_status': 'PROCESSANDO',
                    'numero_nfse': None,
                    'erro_emissao': None,
                    'arquivo_disponivel': False,
                    'solicitada_em': '2026-07-29T10:00:00',
                    'atualizada_em': '2026-07-29T10:05:00',
                    'solicitacao': solicitacao_processando,
                },
                {
                    'codigo_atendimento': 123458,
                    'codigo_paciente': 791,
                    'codigo_convenio': 3,
                    'nome_paciente': 'ANA COM NOTA',
                    'convenio': 'PARTICULAR',
                    'tipo_atendimento': 'Externo',
                    'data_atendimento': '2026-07-29T00:00:00',
                    'data_alta': None,
                    'valor_conta': '210.00',
                    'quantidade_lancamentos': 1,
                    'status': 'EMITIDA',
                    'solicitacao_id': 19,
                    'workflow_status': 'EMITIDA',
                    'emissao_id': 42,
                    'lote_id': 25,
                    'emissao_status': 'EMITIDA',
                    'numero_nfse': '98765',
                    'protocolo': 'PROTOCOLO-98765',
                    'cnpj_emissor': '08711085000128',
                    'razao_social_emissor': (
                        'PRONTOCARDIO SERVICOS MEDICOS HOSPITALARES LTDA'
                    ),
                    'erro_emissao': None,
                    'arquivo_disponivel': True,
                    'solicitada_em': '2026-07-29T09:00:00',
                    'atualizada_em': '2026-07-29T09:10:00',
                    'emissao_atualizada_em': '2026-07-29T09:10:00',
                    'solicitacao': solicitacao_emitida,
                },
            ],
            'resumo_status': [
                {
                    'status': 'PENDENTE_VALIDACAO',
                    'quantidade': 1,
                    'valor_total': '385.50',
                },
                {
                    'status': 'PROCESSANDO',
                    'quantidade': 1,
                    'valor_total': '120.00',
                },
                {
                    'status': 'EMITIDA',
                    'quantidade': 1,
                    'valor_total': '210.00',
                },
            ],
            'resumo_diario': [
                {
                    'data': '2026-07-29',
                    'total': 3,
                    'emitidas': 1,
                    'pendentes': 2,
                    'valor_total': '715.50',
                    'resumo_status': [],
                    'pacientes': [
                        {
                            'nome': 'MARIA DA SILVA',
                            'inicial': 'M',
                            'status': 'PENDENTE_VALIDACAO',
                        },
                        {
                            'nome': 'JOÃO EM PROCESSAMENTO',
                            'inicial': 'J',
                            'status': 'PROCESSANDO',
                        },
                        {
                            'nome': 'ANA COM NOTA',
                            'inicial': 'A',
                            'status': 'EMITIDA',
                        },
                    ],
                    'pacientes_restantes': 0,
                },
            ],
            'data_inicio': '2026-07-01',
            'data_fim': '2026-07-29',
            'total_periodo': 3,
            'total': 3,
            'valor_total_periodo': '715.50',
            'limit': 25,
            'offset': 0,
        }

    def configurar_api_acompanhamento(self, api_get, payload=None):
        payload = payload or self.acompanhamento_particular_payload()

        def responder(path, _params=None):
            if path == '/app_glosas/requisicoes/empresas-emissoras':
                return self.empresas_payload()
            return payload

        api_get.side_effect = responder
        return payload

    @patch('core.views.api_get')
    def test_renderiza_novo_menu_e_formulario(self, api_get):
        response = self.client.get('/requisicao/solicitacao-nota/')

        self.assertEqual(response.status_code, 200)
        api_get.assert_not_called()
        self.assertContains(
            response,
            '<strong>Receita Certa</strong>',
        )
        self.assertNotContains(
            response,
            '<strong>Glosas MV</strong>',
        )
        self.assertNotContains(
            response,
            '<span class="nav-label">Requisição</span>',
        )
        self.assertContains(
            response,
            '<span class="nav-label">Financeiro</span>',
        )
        self.assertContains(
            response,
            '<span class="nav-label">Solicitações Notas</span>',
        )
        self.assertContains(
            response,
            '<span class="nav-label">Solicitação</span>',
        )
        self.assertContains(
            response,
            '<span class="nav-label">Solicitar Nota</span>',
        )
        self.assertContains(
            response,
            '<span class="nav-label">Solicitações cadastradas</span>',
        )
        self.assertContains(
            response,
            '<span class="nav-label">Follow-Up Solicitações</span>',
        )
        self.assertContains(
            response,
            '<span class="nav-label">Solicitações Recusas</span>',
        )
        self.assertContains(
            response,
            '<span class="nav-label">Emissão NFS-e</span>',
        )
        self.assertContains(
            response,
            '<span class="nav-label">Acompanhamento Particular</span>',
        )
        html = response.content.decode()
        self.assertLess(
            html.index('<span class="nav-label">Financeiro</span>'),
            html.index('<span class="nav-label">Solicitações Notas</span>'),
        )
        self.assertLess(
            html.index('<span class="nav-label">Solicitações Notas</span>'),
            html.index(
                '<span class="nav-label">Follow-Up Solicitações</span>'
            ),
        )
        self.assertLess(
            html.index('<span class="nav-label">Emissão NFS-e</span>'),
            html.index(
                '<span class="nav-label">Acompanhamento Particular</span>'
            ),
        )
        self.assertLess(
            html.index(
                '<span class="nav-label">Acompanhamento Particular</span>'
            ),
            html.index('<span class="nav-label">Solicitação</span>'),
        )
        self.assertLess(
            html.index('<span class="nav-label">Solicitação</span>'),
            html.index('<span class="nav-label">Solicitar Nota</span>'),
        )
        self.assertLess(
            html.index('<span class="nav-label">Solicitação</span>'),
            html.index('<span class="nav-label">Solicitações Recusas</span>'),
        )
        self.assertContains(response, '<h1>Solicitar Nota</h1>')
        self.assertNotContains(response, '<span class="panel-title">Solicitações cadastradas</span>')
        self.assertContains(response, 'Código atendimento *')
        self.assertContains(response, 'Nome do paciente')
        self.assertContains(response, 'Convênio')
        self.assertContains(response, 'Telefone / Celular')
        self.assertContains(response, 'Tipo atendimento')
        self.assertContains(response, 'Valor da nota')
        self.assertContains(response, 'data-money-input')
        self.assertContains(response, 'inputmode="numeric"')
        self.assertContains(response, 'Clínica 1')
        self.assertContains(response, 'Clínica 2')
        self.assertContains(response, 'Emergência')
        self.assertContains(response, 'Procedimento *')
        self.assertContains(response, 'Procedimentos e exames realizados')
        self.assertContains(
            response,
            'Consulte um atendimento para visualizar os procedimentos',
        )
        self.assertContains(response, 'loadAttendance')
        cache_control = response.headers['Cache-Control']
        self.assertIn('no-cache', cache_control)
        self.assertIn('no-store', cache_control)
        self.assertIn('must-revalidate', cache_control)
        self.assertIn('private', cache_control)
        css = Path(finders.find('css/app.css')).read_text()
        self.assertIn(
            '.note-request-page {\n'
            '  flex: 1 1 auto;\n'
            '  min-height: 0;\n'
            '  overflow: hidden;',
            css,
        )
        self.assertIn(
            '.note-request-form-scroll {\n'
            '  min-height: 0;\n'
            '  overflow-y: auto;',
            css,
        )
        self.assertIn(
            '.note-request-actions {\n'
            '  position: static;',
            css,
        )
        self.assertIn(
            'width: 100%;\n'
            '  margin: 0;\n'
            '  padding: 0.65rem 1.3rem;',
            css,
        )
        self.assertIn(
            'grid-template-columns: minmax(14rem, 0.42fr) max-content;',
            css,
        )
        self.assertIn(
            'grid-template-columns: minmax(14rem, 26rem) '
            'minmax(16rem, 1fr);',
            css,
        )

    @patch('core.views.api_get')
    def test_acompanhamento_particular_exibe_status_do_periodo_e_acoes(
        self,
        api_get,
    ):
        payload = self.acompanhamento_particular_payload()
        payload['atendimentos'][0]['solicitacao']['local'] = None
        solicitacao_emitida = payload['atendimentos'][2]['solicitacao']
        solicitacao_emitida['procedimentos_atendimento'] = [
            {
                'codigo': '40304361',
                'descricao': 'ECOCARDIOGRAMA DE ACOMPANHAMENTO',
                'grupo': 'EXAMES CARDIOLÓGICOS',
                'quantidade': '1',
                'valor_total': '385.50',
                'realizado_em': '2026-07-29T08:30:00',
                'prestador': 'DRA. TESTE',
            },
        ]
        solicitacao_emitida['valor_total_procedimentos'] = '385.50'
        self.configurar_api_acompanhamento(api_get, payload)

        response = self.client.get(
            '/requisicao/acompanhamento-particular/'
            '?data_referencia=2026-07-15'
            '&data_selecionada=2026-07-29'
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<h1>Acompanhamento Particular</h1>')
        self.assertContains(
            response,
            'Painel de emissão Particular e Prontorede',
        )
        self.assertContains(
            response,
            'name="selecionar_dia_resultado" value="1"',
        )
        self.assertContains(response, '07/2026')
        self.assertContains(response, '29/07/2026')
        self.assertNotContains(
            response,
            'Visão operacional dos atendimentos do convênio Particular',
        )
        self.assertNotContains(response, 'Exibir hoje')
        self.assertContains(response, 'Mapa diário do período')
        self.assertNotContains(response, 'Posição da emissão')
        self.assertContains(
            response,
            'Fila de atendimentos Particular e Prontorede',
        )
        self.assertContains(response, 'particular-patient-bubble')
        self.assertContains(response, 'particular-patient-bubble--cor-')
        self.assertContains(response, '>M</span>')
        self.assertContains(response, '>J</span>')
        self.assertContains(response, '>A</span>')
        dia_selecionado = next(
            dia
            for dia in response.context['dias_grade']
            if dia['selecionado']
        )
        self.assertEqual(
            len({
                paciente['cor']
                for paciente in dia_selecionado['pacientes']
            }),
            3,
        )
        self.assertContains(
            response,
            'workflow-request-card--acompanhamento',
        )
        self.assertContains(response, 'Confirmar validação')
        self.assertContains(response, 'Recusar dados')
        self.assertContains(response, 'CNPJ emissor *')
        self.assertContains(response, 'Paciente e atendimento')
        self.assertContains(response, 'Dados da solicitação')
        self.assertContains(
            response,
            'Procedimentos e exames realizados',
            count=3,
        )
        self.assertContains(
            response,
            '<section class="workflow-attendance-items">',
            count=3,
        )
        self.assertContains(response, 'ECOCARDIOGRAMA DE ACOMPANHAMENTO')
        self.assertContains(response, 'EXAMES CARDIOLÓGICOS')
        self.assertContains(response, 'DRA. TESTE')
        self.assertContains(response, 'Atendimento')
        self.assertContains(response, '<small>Valor</small>')
        self.assertNotContains(
            response,
            '<small>Valor da nota</small>',
        )
        self.assertContains(response, 'MARIA DA SILVA')
        self.assertContains(response, 'JOÃO EM PROCESSAMENTO')
        self.assertContains(response, 'ANA COM NOTA')
        self.assertContains(response, 'Aguardando validação')
        self.assertContains(response, 'Em processamento')
        self.assertContains(response, 'NFS-e emitida')
        self.assertContains(response, 'Empresa emissora')
        self.assertContains(
            response,
            'workflow-detail-sections--emitted',
        )
        self.assertContains(response, 'Paciente e contato')
        self.assertContains(response, 'Atendimento e solicitação')
        self.assertContains(response, 'Emissão da NFS-e')
        self.assertContains(response, '08.711.085/0001-28')
        self.assertContains(
            response,
            'PRONTOCARDIO SERVICOS MEDICOS HOSPITALARES LTDA',
        )
        self.assertContains(response, '#25')
        self.assertContains(response, '98765')
        self.assertContains(response, 'PROTOCOLO-98765')
        self.assertContains(response, 'R$ 715,50')
        self.assertNotContains(response, '<small>CPF</small>')
        self.assertNotContains(
            response,
            '<small>Solicitada em</small>',
        )
        self.assertNotContains(
            response,
            '<small>Data do atendimento</small>',
        )
        self.assertContains(
            response,
            '<small>Número da NFS-e</small>',
        )
        self.assertContains(
            response,
            '/requisicao/emissao-nfse/itens/42/pdf/?download=false',
        )
        self.assertContains(
            response,
            'window.setTimeout(() => window.location.reload(), 10000)',
        )
        html = response.content.decode()
        cabecalho_inicio = html.index('class="workflow-request-header"')
        cabecalho_fim = html.index('</div>', cabecalho_inicio)
        cabecalho_html = html[cabecalho_inicio:cabecalho_fim]
        paciente_inicio = html.index('<h3>Paciente e atendimento</h3>')
        paciente_fim = html.index('</section>', paciente_inicio)
        dados_inicio = html.index('<h3>Dados da solicitação</h3>')
        dados_fim = html.index('</section>', dados_inicio)
        emitido_inicio = html.index('<h3>Atendimento e solicitação</h3>')
        emitido_fim = html.index('</section>', emitido_inicio)
        paciente_html = html[paciente_inicio:paciente_fim]
        dados_html = html[dados_inicio:dados_fim]
        emitido_html = html[emitido_inicio:emitido_fim]
        self.assertIn('<small>Convênio</small>', cabecalho_html)
        self.assertIn('<strong title="SEM">SEM</strong>', cabecalho_html)
        self.assertLess(
            cabecalho_html.index('<small>Convênio</small>'),
            cabecalho_html.index('<small>Local</small>'),
        )
        self.assertIn('<small>Convênio</small>', paciente_html)
        self.assertIn('<small>CEP</small>', paciente_html)
        self.assertIn('<small>Telefone / Celular</small>', paciente_html)
        self.assertIn('<small>E-mail</small>', paciente_html)
        self.assertIn('<small>Endereço</small>', paciente_html)
        self.assertNotIn('<small>Procedimento</small>', paciente_html)
        self.assertNotIn('<small>Procedimento</small>', emitido_html)
        self.assertIn('<small>Solicitada por</small>', dados_html)
        self.assertIn('CNPJ emissor *', dados_html)
        self.assertLess(paciente_inicio, dados_inicio)
        self.assertLess(
            html.index('CNPJ emissor *'),
            html.index('Procedimentos e exames realizados'),
        )
        self.assertLess(
            html.index('Procedimentos e exames realizados'),
            html.index('>Confirmar validação</button>'),
        )
        self.assertLess(
            html.index('Procedimentos e exames realizados'),
            html.index('>Recusar dados</button>'),
        )
        self.assertIn(
            'workflow-request-detail--address is-wide',
            html,
        )
        self.assertNotIn(
            'workflow-request-detail--procedure is-wide',
            html,
        )
        self.assertEqual(
            response.context['anterior_url'],
            '?data_referencia=2026-06-30'
            '&data_selecionada=2026-06-29',
        )
        self.assertEqual(
            response.context['proxima_url'],
            '?data_referencia=2026-08-01'
            '&data_selecionada=2026-08-29',
        )
        self.assertCountEqual(
            api_get.call_args_list,
            [
                call(
                    '/app_glosas/requisicoes/'
                    'acompanhamento-particular',
                    {
                        'data_inicio': '2026-07-01',
                        'data_fim': '2026-07-31',
                        'codigo_atendimento': '',
                        'nome_paciente': '',
                        'tipo_atendimento': '',
                        'convenio': '',
                        'status': '',
                        'limit': 1,
                        'offset': 0,
                    },
                ),
                call(
                    '/app_glosas/requisicoes/'
                    'acompanhamento-particular',
                    {
                        'data_inicio': '2026-07-29',
                        'data_fim': '2026-07-29',
                        'codigo_atendimento': '',
                        'nome_paciente': '',
                        'tipo_atendimento': '',
                        'convenio': '',
                        'status': '',
                        'limit': 10,
                        'offset': 0,
                    },
                ),
                call(
                    '/app_glosas/requisicoes/empresas-emissoras',
                    None,
                ),
            ],
        )

    @patch('core.views.api_get')
    def test_acompanhamento_solicitar_nota_preserva_dia_para_retorno(
        self,
        api_get,
    ):
        payload = self.acompanhamento_particular_payload()
        atendimento = payload['atendimentos'][0]
        atendimento['status'] = 'SEM_SOLICITACAO'
        atendimento['solicitacao_id'] = None
        atendimento['workflow_status'] = None
        atendimento['solicitacao'] = None
        payload['atendimentos'] = [atendimento]
        payload['total'] = 1
        self.configurar_api_acompanhamento(api_get, payload)

        response = self.client.get(
            '/requisicao/acompanhamento-particular/'
            '?data_referencia=2026-07-15'
            '&data_selecionada=2026-07-29'
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Sem solicitação')
        self.assertContains(response, '>Solicitar nota</a>')
        html = response.content.decode()
        acao_fim = html.index('>Solicitar nota</a>')
        href_inicio = html.rfind('href="', 0, acao_fim) + len('href="')
        href_fim = html.index('"', href_inicio)
        solicitar_nota_url = html[href_inicio:href_fim]
        self.assertIn(
            '/requisicao/solicitacao-nota/'
            '?codigo_atendimento=123456',
            solicitar_nota_url,
        )
        self.assertIn('retorno=', solicitar_nota_url)
        self.assertIn('data_referencia%3D2026-07-15', solicitar_nota_url)
        self.assertIn('data_selecionada%3D2026-07-29', solicitar_nota_url)

    @patch('core.views.api_get')
    def test_acompanhamento_particular_atualiza_resumo_pelo_dia_selecionado(
        self,
        api_get,
    ):
        payload_mes = self.acompanhamento_particular_payload()
        payload_mes['total_periodo'] = 10
        payload_mes['valor_total_periodo'] = '2250.75'
        payload_mes['resumo_status'] = [
            {
                'status': 'SEM_SOLICITACAO',
                'quantidade': 4,
                'valor_total': '650.25',
            },
            {
                'status': 'PENDENTE_VALIDACAO',
                'quantidade': 1,
                'valor_total': '75.00',
            },
            {
                'status': 'VALIDADA',
                'quantidade': 3,
                'valor_total': '725.50',
            },
            {
                'status': 'EMITIDA',
                'quantidade': 2,
                'valor_total': '800.00',
            },
        ]
        payload_dia = self.acompanhamento_particular_payload()
        payload_dia['total_periodo'] = 3
        payload_dia['valor_total_periodo'] = '715.50'

        def responder(path, params=None):
            if path.endswith('/empresas-emissoras'):
                return self.empresas_payload()
            if params['data_inicio'] == '2026-07-01':
                return payload_mes
            return payload_dia

        api_get.side_effect = responder

        response = self.client.get(
            '/requisicao/acompanhamento-particular/',
            {
                'data_referencia': '2026-07-15',
                'data_selecionada': '2026-07-29',
            },
        )

        self.assertEqual(response.status_code, 200)
        cards = {
            card['label']: card for card in response.context['resumo_cards']
        }
        self.assertEqual(
            cards['Total Particular + Prontorede'],
            {
                'label': 'Total Particular + Prontorede',
                'quantidade': 3,
                'detalhe': 'Valor total: R$ 715,50',
                'classe': 'total',
            },
        )
        self.assertEqual(
            cards['Sem solicitação']['quantidade'],
            0,
        )
        self.assertEqual(
            cards['Sem solicitação']['detalhe'],
            'Valor total: R$ 0,00',
        )
        self.assertEqual(
            cards['Aguardando validação'],
            {
                'label': 'Aguardando validação',
                'quantidade': 1,
                'detalhe': 'Valor total: R$ 385,50',
                'classe': 'pendente',
            },
        )
        self.assertEqual(
            cards['Atendimentos validados']['quantidade'],
            0,
        )
        self.assertEqual(
            cards['Atendimentos validados']['detalhe'],
            'Valor total: R$ 0,00',
        )
        self.assertEqual(
            cards['Com nota emitida']['quantidade'],
            1,
        )
        self.assertEqual(
            cards['Com nota emitida']['detalhe'],
            'Valor total: R$ 210,00',
        )
        self.assertEqual(
            cards['Emissões com erro'],
            {
                'label': 'Emissões com erro',
                'quantidade': 0,
                'detalhe': 'Valor total: R$ 0,00',
                'classe': 'erro',
            },
        )
        self.assertEqual(
            sum(
                card['quantidade']
                for label, card in cards.items()
                if label != 'Total Particular + Prontorede'
            ),
            cards['Total Particular + Prontorede']['quantidade'],
        )
        self.assertEqual(
            response.context['emissao_dia'],
            {
                'percentual': 33.3,
                'angulo': 60.0,
                'valor_emitido': 'R$ 210,00',
                'valor_nao_emitido': 'R$ 505,50',
                'quantidade_emitida': 1,
                'quantidade_nao_emitida': 2,
            },
        )
        self.assertContains(response, 'Total Particular + Prontorede')
        self.assertContains(response, 'Sem solicitação')
        self.assertContains(response, 'Aguardando validação')
        self.assertContains(response, 'Atendimentos validados')
        self.assertContains(response, 'Emissões com erro')
        self.assertContains(response, 'Com nota emitida')
        self.assertContains(response, 'Resumo do dia 29/07/2026')
        self.assertContains(response, 'Emissão de NFS-e')
        self.assertContains(response, 'Progresso por quantidade no dia')
        self.assertNotContains(response, 'Progresso financeiro do mês')
        self.assertContains(response, 'Emitido em NFS-e')
        self.assertContains(response, 'Não emitido em NFS-e')
        self.assertContains(response, '<span>NFS-e emitidas</span>')
        self.assertContains(response, '<strong>1 atendimento</strong>')
        self.assertContains(response, '<span>Valor: R$ 210,00</span>')
        self.assertContains(response, '<strong>2 atendimentos</strong>')
        self.assertContains(response, '<span>Valor: R$ 505,50</span>')
        self.assertNotContains(response, '<span>emitido</span>')
        self.assertNotContains(response, 'Faturamento NFS-e')
        self.assertNotContains(response, 'Faturado em NFS-e')
        self.assertNotContains(response, 'Não faturado')
        self.assertContains(
            response,
            'class="particular-billing-gauge-progress"',
        )
        self.assertContains(response, 'stroke-dasharray: 33.3 100;')
        self.assertContains(response, 'rotate(60.0 60 58)')
        html = response.content.decode()
        self.assertLess(
            html.index('Atendimentos validados'),
            html.index('Emissões com erro'),
        )
        css = Path(finders.find('css/app.css')).read_text()
        self.assertIn(
            '.particular-month-overview\n'
            '  .particular-summary-card--erro {\n'
            '  grid-row: 3;\n'
            '  grid-column: 2;',
            css,
        )
        self.assertContains(
            response,
            'Selecione um dia para atualizar os indicadores e a fila.',
        )
        self.assertNotContains(
            response,
            'Selecione um dia para atualizar a fila.',
        )

    @patch('core.views.api_get')
    def test_acompanhamento_particular_encaminha_filtros(
        self,
        api_get,
    ):
        payload = self.acompanhamento_particular_payload()
        payload['atendimentos'] = [payload['atendimentos'][1]]
        payload['total'] = 1
        self.configurar_api_acompanhamento(api_get, payload)

        response = self.client.get(
            '/requisicao/acompanhamento-particular/',
            {
                'data_referencia': '2026-07-15',
                'data_selecionada': '2026-07-29',
                'codigo_atendimento': '123457',
                'nome_paciente': 'João',
                'tipo_atendimento': 'Urgência',
                'convenio': 'PRONTOREDE',
                'status': 'PROCESSANDO',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'JOÃO EM PROCESSAMENTO')
        self.assertContains(
            response,
            '<option value="PROCESSANDO" selected>',
        )
        self.assertContains(
            response,
            '<option value="PRONTOREDE" selected>Prontorede</option>',
        )
        hoje = date.today().isoformat()
        self.assertEqual(
            response.context['limpar_url'],
            f'?data_referencia={hoje}&data_selecionada={hoje}',
        )
        self.assertNotIn(
            'codigo_atendimento',
            response.context['limpar_url'],
        )
        self.assertNotIn(
            'nome_paciente',
            response.context['limpar_url'],
        )
        self.assertNotIn(
            'tipo_atendimento',
            response.context['limpar_url'],
        )
        self.assertNotIn(
            'convenio',
            response.context['limpar_url'],
        )
        self.assertNotIn(
            'status',
            response.context['limpar_url'],
        )
        self.assertContains(
            response,
            f'href="?data_referencia={hoje}&amp;'
            f'data_selecionada={hoje}">Limpar</a>',
        )
        api_get.assert_has_calls(
            [
                call(
                    '/app_glosas/requisicoes/'
                    'acompanhamento-particular',
                    {
                        'data_inicio': '2026-07-01',
                        'data_fim': '2026-07-31',
                        'codigo_atendimento': 123457,
                        'nome_paciente': 'João',
                        'tipo_atendimento': 'Urgência',
                        'convenio': 'PRONTOREDE',
                        'status': 'PROCESSANDO',
                        'limit': 1,
                        'offset': 0,
                    },
                ),
                call(
                    '/app_glosas/requisicoes/'
                    'acompanhamento-particular',
                    {
                        'data_inicio': '2026-07-29',
                        'data_fim': '2026-07-29',
                        'codigo_atendimento': 123457,
                        'nome_paciente': 'João',
                        'tipo_atendimento': 'Urgência',
                        'convenio': 'PRONTOREDE',
                        'status': 'PROCESSANDO',
                        'limit': 10,
                        'offset': 0,
                    },
                ),
            ],
            any_order=True,
        )

    @patch('core.views.api_get')
    def test_filtro_por_atendimento_seleciona_dia_com_resultado(
        self,
        api_get,
    ):
        payload_calendario = self.acompanhamento_particular_payload()
        payload_calendario['resumo_diario'] = [{
            'data': '2026-07-30',
            'total': 1,
            'emitidas': 0,
            'pendentes': 1,
            'valor_total': '1155.00',
            'resumo_status': [],
            'pacientes': [{
                'nome': 'MANOEL ARRUDA CAVALCANTE',
                'inicial': 'M',
                'status': 'ERRO_EMISSAO',
            }],
            'pacientes_restantes': 0,
        }]
        payload_dia_vazio = {
            **payload_calendario,
            'atendimentos': [],
            'total': 0,
            'limit': 10,
            'offset': 0,
        }

        def responder(path, params=None):
            if path.endswith('/empresas-emissoras'):
                return self.empresas_payload()
            if params['data_inicio'] == '2026-07-01':
                return payload_calendario
            return payload_dia_vazio

        api_get.side_effect = responder

        response = self.client.get(
            '/requisicao/acompanhamento-particular/',
            {
                'codigo_atendimento': '340080',
                'data_referencia': '2026-07-31',
                'data_selecionada': '2026-07-31',
                'selecionar_dia_resultado': '1',
            },
        )

        self.assertRedirects(
            response,
            '/requisicao/acompanhamento-particular/'
            '?codigo_atendimento=340080'
            '&data_referencia=2026-07-31'
            '&data_selecionada=2026-07-30',
            fetch_redirect_response=False,
        )

    @patch('core.views.api_get')
    def test_filtro_por_erro_seleciona_dia_com_status(
        self,
        api_get,
    ):
        payload_calendario = self.acompanhamento_particular_payload()
        payload_calendario['resumo_diario'] = [{
            'data': '2026-07-30',
            'total': 40,
            'emitidas': 0,
            'pendentes': 40,
            'valor_total': '42000.00',
            'resumo_status': [{
                'status': 'ERRO_EMISSAO',
                'quantidade': 1,
                'valor_total': '1155.00',
            }],
            'pacientes': [],
            'pacientes_restantes': 40,
        }]
        payload_dia_vazio = {
            **payload_calendario,
            'atendimentos': [],
            'total': 0,
            'limit': 10,
            'offset': 0,
        }

        def responder(path, params=None):
            if path.endswith('/empresas-emissoras'):
                return self.empresas_payload()
            if params['data_inicio'] == '2026-07-01':
                return payload_calendario
            return payload_dia_vazio

        api_get.side_effect = responder

        response = self.client.get(
            '/requisicao/acompanhamento-particular/',
            {
                'status': 'ERRO_EMISSAO',
                'data_referencia': '2026-07-31',
                'data_selecionada': '2026-07-31',
                'selecionar_dia_resultado': '1',
            },
        )

        self.assertRedirects(
            response,
            '/requisicao/acompanhamento-particular/'
            '?status=ERRO_EMISSAO'
            '&data_referencia=2026-07-31'
            '&data_selecionada=2026-07-30',
            fetch_redirect_response=False,
        )
        self.assertNotIn(
            'selecionar_dia_resultado',
            response['Location'],
        )

    @patch('core.views.api_get')
    def test_acompanhamento_particular_monta_visao_mensal(
        self,
        api_get,
    ):
        payload = self.acompanhamento_particular_payload()
        payload['resumo_diario'][0]['pacientes_restantes'] = 2
        self.configurar_api_acompanhamento(api_get, payload)

        response = self.client.get(
            '/requisicao/acompanhamento-particular/',
            {
                'data_referencia': '2026-07-15',
                'data_selecionada': '2026-07-29',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'aria-label="Mês anterior"')
        self.assertContains(response, 'aria-label="Próximo mês"')
        self.assertContains(response, 'rel="prev"')
        self.assertContains(response, 'rel="next"')
        self.assertContains(response, '07/2026')
        self.assertContains(response, '>Dom</span>')
        self.assertContains(response, '>Sáb</span>')
        self.assertContains(
            response,
            'class="particular-calendar-day-total"',
        )
        self.assertContains(response, '>Atendimentos</span>')
        self.assertContains(response, 'aria-label="3 atendimentos"')
        self.assertContains(response, '>+2</span>')
        self.assertContains(response, 'aria-current="date"')

    @patch('core.views.api_get')
    def test_acompanhamento_particular_consulta_mes_e_dia_em_paralelo(
        self,
        api_get,
    ):
        payload = self.acompanhamento_particular_payload()
        barreira = Barrier(3)

        def responder(path, _params=None):
            barreira.wait(timeout=1)
            if path == '/app_glosas/requisicoes/empresas-emissoras':
                return self.empresas_payload()
            return payload

        api_get.side_effect = responder

        response = self.client.get(
            '/requisicao/acompanhamento-particular/',
            {
                'data_referencia': '2026-07-15',
                'data_selecionada': '2026-07-29',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '07/2026')

    @patch('core.views.api_get')
    def test_acompanhamento_particular_reutiliza_resumo_ao_trocar_dia(
        self,
        api_get,
    ):
        self.configurar_api_acompanhamento(api_get)
        url = '/requisicao/acompanhamento-particular/'

        primeira_resposta = self.client.get(
            url,
            {
                'data_referencia': '2026-07-15',
                'data_selecionada': '2026-07-29',
            },
        )
        self.assertEqual(primeira_resposta.status_code, 200)

        api_get.reset_mock()
        segunda_resposta = self.client.get(
            url,
            {
                'data_referencia': '2026-07-15',
                'data_selecionada': '2026-07-30',
            },
        )

        self.assertEqual(segunda_resposta.status_code, 200)
        self.assertEqual(api_get.call_count, 2)
        self.assertNotIn(
            call(
                '/app_glosas/requisicoes/'
                'acompanhamento-particular',
                {
                    'data_inicio': '2026-07-01',
                    'data_fim': '2026-07-31',
                    'codigo_atendimento': '',
                    'nome_paciente': '',
                    'tipo_atendimento': '',
                    'convenio': '',
                    'status': '',
                    'limit': 1,
                    'offset': 0,
                },
            ),
            api_get.call_args_list,
        )
        self.assertIn(
            call(
                '/app_glosas/requisicoes/'
                'acompanhamento-particular',
                {
                    'data_inicio': '2026-07-30',
                    'data_fim': '2026-07-30',
                    'codigo_atendimento': '',
                    'nome_paciente': '',
                    'tipo_atendimento': '',
                    'convenio': '',
                    'status': '',
                    'limit': 10,
                    'offset': 0,
                },
            ),
            api_get.call_args_list,
        )

    def test_acompanhamento_particular_ajusta_cards_sem_scroll_horizontal(
        self,
    ):
        css = Path(finders.find('css/app.css')).read_text()

        self.assertIn('margin-left: -0.42rem;', css)
        self.assertIn('.particular-patient-bubble--cor-8 {', css)
        self.assertIn(
            '.particular-dashboard-page--daily\n'
            '  .particular-calendar-day-count {\n'
            '  position: absolute;\n'
            '  top: 46%;\n'
            '  left: 50%;',
            css,
        )
        self.assertIn(
            '.particular-dashboard-page--daily\n'
            '  .particular-calendar-day-count > span {\n'
            '  color: #718791;\n'
            '  font-size: 0.58rem;\n'
            '  font-weight: 850;',
            css,
        )
        self.assertIn(
            '.particular-calendar-day.is-selected {\n'
            '  border: 3px solid #e38a16;\n'
            '  background: #fff8ed;',
            css,
        )
        self.assertIn(
            '.particular-dashboard-page--daily '
            '.particular-calendar-day {\n'
            '  position: relative;\n'
            '  display: block;\n'
            '  min-height: 5.6rem;\n'
            '  padding: 0.42rem;\n'
            '  border-width: 2px;',
            css,
        )
        self.assertIn(
            '.particular-patient-bubbles {\n'
            '  position: absolute;\n'
            '  bottom: 0.3rem;\n'
            '  left: 0.4rem;',
            css,
        )
        self.assertIn(
            '.particular-dashboard-page--daily '
            '.particular-queue-scroll {\n'
            '  overflow-x: hidden;\n'
            '  overflow-y: auto;',
            css,
        )
        self.assertIn(
            '.particular-dashboard-page--daily '
            '.workflow-request-card {\n'
            '  width: 100%;\n'
            '  min-width: 0;',
            css,
        )
        self.assertIn(
            '.particular-dashboard-page--daily\n'
            '  .particular-queue-panel .results-heading {\n'
            '  display: grid;\n'
            '  width: 100%;\n'
            '  grid-template-columns: minmax(0, 1fr) auto;',
            css,
        )
        self.assertIn(
            '.particular-dashboard-page--daily\n'
            '  .particular-queue-panel .collapse-actions {\n'
            '  align-items: center;\n'
            '  flex-direction: row;\n'
            '  justify-self: end;',
            css,
        )
        self.assertIn(
            '"chevron attendance patient type convenio location value status";',
            css,
        )
        self.assertIn(
            '.particular-dashboard-page--daily '
            '.workflow-request-field--convenio {\n'
            '  grid-area: convenio;',
            css,
        )
        self.assertIn(
            '.particular-dashboard-page--daily '
            '.workflow-request-header strong {\n'
            '  overflow: hidden;\n'
            '  font-size: 0.56rem;',
            css,
        )
        self.assertIn(
            '  text-overflow: ellipsis;\n'
            '  white-space: nowrap;',
            css,
        )
        self.assertIn(
            '.particular-dashboard-page--daily '
            '.workflow-request-details {\n'
            '  grid-template-columns: repeat(7, minmax(0, 1fr));',
            css,
        )
        self.assertIn(
            '.particular-dashboard-page--daily\n'
            '  .workflow-particular-patient-details {\n'
            '  grid-template-columns: repeat(4, minmax(0, 1fr));',
            css,
        )
        self.assertIn(
            '.particular-dashboard-page--daily\n'
            '  .workflow-particular-request-details {\n'
            '  grid-template-columns: repeat(3, minmax(0, 1fr));',
            css,
        )
        self.assertIn(
            '.particular-dashboard-page--daily\n'
            '  .workflow-detail-sections--emitted '
            '.workflow-request-details {\n'
            '  grid-template-columns: repeat(6, minmax(0, 1fr));',
            css,
        )
        self.assertIn(
            '.particular-dashboard-page--daily\n'
            '  .workflow-request-details > span.is-wide {\n'
            '  grid-column: span 1;',
            css,
        )
        self.assertIn(
            '.particular-dashboard-page--daily\n'
            '  .workflow-attendance-items-table table {\n'
            '  min-width: 0;\n'
            '  table-layout: fixed;',
            css,
        )
        self.assertNotIn(
            '.particular-dashboard-page--daily '
            '.workflow-request-list {\n'
            '  min-width: 68rem;',
            css,
        )

    @patch('core.views.api_get')
    def test_acompanhamento_particular_usa_paginacao_padrao_com_filtros(
        self,
        api_get,
    ):
        payload = self.acompanhamento_particular_payload()
        payload['atendimentos'] = [payload['atendimentos'][1]]
        payload['total'] = 21
        payload['limit'] = 10
        payload['offset'] = 10
        self.configurar_api_acompanhamento(api_get, payload)

        response = self.client.get(
            '/requisicao/acompanhamento-particular/',
            {
                'data_referencia': '2026-07-15',
                'data_selecionada': '2026-07-29',
                'codigo_atendimento': '123457',
                'nome_paciente': 'João',
                'tipo_atendimento': 'Urgência',
                'convenio': 'PRONTOREDE',
                'status': 'PROCESSANDO',
                'page': '2',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="particular-page-select"')
        self.assertContains(
            response,
            'results-toolbar workflow-request-toolbar '
            'particular-queue-toolbar',
        )
        self.assertContains(response, 'Página 2 de 3')
        css = Path(finders.find('css/app.css')).read_text()
        self.assertIn(
            '.particular-dashboard-page--daily '
            '.particular-queue-toolbar {\n'
            '  align-items: center;\n'
            '  flex-direction: row;\n'
            '  justify-content: flex-end;',
            css,
        )
        self.assertIn(
            '.particular-dashboard-page--daily\n'
            '  .particular-queue-toolbar .pagination-control {\n'
            '  align-items: center;\n'
            '  flex-direction: row;\n'
            '  margin-left: auto;',
            css,
        )
        pagination = response.context['pagination']
        query = (
            'codigo_atendimento=123457&nome_paciente=Jo%C3%A3o'
            '&tipo_atendimento=Urg%C3%AAncia&convenio=PRONTOREDE'
            '&status=PROCESSANDO'
            '&data_referencia=2026-07-15'
            '&data_selecionada=2026-07-29'
        )
        self.assertEqual(
            pagination['previous_url'],
            f'?{query}&page=1',
        )
        self.assertEqual(
            pagination['next_url'],
            f'?{query}&page=3',
        )
        self.assertIn(
            call(
                '/app_glosas/requisicoes/acompanhamento-particular',
                {
                    'data_inicio': '2026-07-29',
                    'data_fim': '2026-07-29',
                    'codigo_atendimento': 123457,
                    'nome_paciente': 'João',
                    'tipo_atendimento': 'Urgência',
                    'convenio': 'PRONTOREDE',
                    'status': 'PROCESSANDO',
                    'limit': 10,
                    'offset': 10,
                },
            ),
            api_get.call_args_list,
        )

    @patch('core.views.api_post')
    @patch('core.views.api_put')
    def test_acompanhamento_particular_valida_sem_perder_selecao_do_dia(
        self,
        api_put,
        api_post,
    ):
        url = (
            '/requisicao/acompanhamento-particular/'
            '?data_referencia=2026-07-15'
            '&data_selecionada=2026-07-29'
        )

        response = self.client.post(
            url,
            {
                'solicitacao_id': '17',
                'decisao': 'VALIDADA',
                'empresa_emissora_id': '3',
            },
        )

        self.assertRedirects(
            response,
            url,
            fetch_redirect_response=False,
        )
        api_put.assert_called_once_with(
            '/app_glosas/requisicoes/solicitacoes-nota/'
            '17/empresa-emissora',
            {'empresa_emissora_id': 3},
        )
        api_post.assert_called_once_with(
            '/app_glosas/requisicoes/solicitacoes-nota/17/validacao',
            {
                'decisao': 'VALIDADA',
                'motivo_recusa': None,
            },
        )

    @patch('core.views.api_post')
    def test_acompanhamento_particular_recusa_sem_perder_selecao_do_dia(
        self,
        api_post,
    ):
        url = (
            '/requisicao/acompanhamento-particular/'
            '?data_referencia=2026-07-15'
            '&data_selecionada=2026-07-29'
        )

        response = self.client.post(
            url,
            {
                'solicitacao_id': '17',
                'decisao': 'RECUSADA',
                'motivo_recusa': 'CPF precisa ser corrigido.',
            },
        )

        self.assertRedirects(
            response,
            url,
            fetch_redirect_response=False,
        )
        api_post.assert_called_once_with(
            '/app_glosas/requisicoes/solicitacoes-nota/17/validacao',
            {
                'decisao': 'RECUSADA',
                'motivo_recusa': 'CPF precisa ser corrigido.',
            },
        )

    @patch('core.views.api_get')
    def test_acompanhamento_mantem_validadas_e_recusadas_na_fila(
        self,
        api_get,
    ):
        payload = self.acompanhamento_particular_payload()
        payload['atendimentos'][0]['status'] = 'VALIDADA'
        payload['atendimentos'][0]['workflow_status'] = 'VALIDADA'
        payload['atendimentos'][0]['solicitacao']['status'] = 'VALIDADA'
        payload['atendimentos'][1]['status'] = 'RECUSADA'
        payload['atendimentos'][1]['workflow_status'] = 'RECUSADA'
        payload['atendimentos'][1]['solicitacao']['status'] = 'RECUSADA'
        self.configurar_api_acompanhamento(api_get, payload)

        response = self.client.get(
            '/requisicao/acompanhamento-particular/',
            {
                'data_referencia': '2026-07-15',
                'data_selecionada': '2026-07-29',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'MARIA DA SILVA')
        self.assertContains(response, 'JOÃO EM PROCESSAMENTO')
        self.assertContains(response, 'Validada para emissão')
        self.assertContains(response, 'Solicitação recusada')
        self.assertNotContains(response, 'Confirmar validação')
        self.assertNotContains(response, 'Recusar dados')

    def test_consulta_atendimento_exibe_erros_amigaveis(self):
        response = self.client.get('/requisicao/solicitacao-nota/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Number.isSafeInteger(numericCode)')
        self.assertContains(response, 'response.json().catch(() => null)')
        self.assertContains(
            response,
            'Não foi possível consultar o atendimento agora.',
        )
        self.assertContains(
            response,
            'Sua sessão expirou. Atualize a página e entre novamente.',
        )
        self.assertContains(
            response,
            'Atendimento não encontrado. Confira o código informado.',
        )
        self.assertNotContains(response, 'setStatus(error.message')

    def test_campos_monetarios_livres_usam_mascara_compartilhada(self):
        templates_dir = Path(__file__).resolve().parent.parent / 'templates'
        fields = (
            ('solicitacao_nota.html', 'name="valor_nota"'),
            ('solicitacoes_nota.html', 'name="valor_nota"'),
            ('recebimentos.html', 'name="valor_recebido"'),
            ('remessas.html', 'name="valor_total"'),
            ('recursos.html', 'name="valor_recursado"'),
            ('recursos.html', 'name="valor_acatado"'),
            (
                'conciliacoes_sem_recebimento.html',
                'name="valor_glosado_{{ remessa.cd_remessa }}"',
            ),
            (
                'conciliacoes_sem_recebimento.html',
                'name="valor_recebido_{{ remessa.cd_remessa }}"',
            ),
        )

        for template_name, field_name in fields:
            with self.subTest(
                template=template_name,
                field=field_name,
            ):
                template = (templates_dir / template_name).read_text()
                field_position = template.index(field_name)
                input_start = template.rfind('<input', 0, field_position)
                input_end = template.index('>', field_position)
                input_html = template[input_start : input_end + 1]

                self.assertIn('data-money-input', input_html)
                self.assertIn('inputmode="numeric"', input_html)

        base_template = (templates_dir / 'base.html').read_text()
        self.assertIn("const selector = '[data-money-input]';", base_template)
        self.assertIn(".replace(/\\D/g, '')", base_template)
        self.assertIn(
            "document.addEventListener('formdata'",
            base_template,
        )

    @patch('core.views.api_get')
    def test_workflow_e_emissao_usam_a_mesma_nova_navbar(self, api_get):
        api_get.return_value = self.lista_payload()

        for path in (
            '/requisicao/workflow-solicitacoes/',
            '/requisicao/emissao-nfse/',
        ):
            with self.subTest(path=path):
                response = self.client.get(path)

                self.assertEqual(response.status_code, 200)
                html = response.content.decode()
                financeiro_inicio = html.index(
                    '<span class="nav-label">Financeiro</span>'
                )
                solicitacao_inicio = html.index(
                    '<span class="nav-label">Solicitação</span>'
                )
                administrativo_inicio = html.index(
                    '<span class="nav-label">Administrativo</span>'
                )
                menu_financeiro = html[
                    financeiro_inicio:solicitacao_inicio
                ]
                menu_solicitacao = html[
                    solicitacao_inicio:administrativo_inicio
                ]

                self.assertIn(
                    '<span class="nav-label">Solicitações Notas</span>',
                    menu_financeiro,
                )
                self.assertIn(
                    '<span class="nav-label">Follow-Up Solicitações</span>',
                    menu_financeiro,
                )
                self.assertIn(
                    '<span class="nav-label">Emissão NFS-e</span>',
                    menu_financeiro,
                )
                self.assertNotIn(
                    '<span class="nav-label">Solicitar Nota</span>',
                    menu_financeiro,
                )
                self.assertNotIn(
                    '<span class="nav-label">Solicitações cadastradas</span>',
                    menu_financeiro,
                )
                self.assertNotIn(
                    '<span class="nav-label">Solicitações Recusas</span>',
                    menu_financeiro,
                )
                self.assertIn(
                    '<span class="nav-label">Solicitar Nota</span>',
                    menu_solicitacao,
                )
                self.assertIn(
                    '<span class="nav-label">Solicitações cadastradas</span>',
                    menu_solicitacao,
                )
                self.assertIn(
                    '<span class="nav-label">Solicitações Recusas</span>',
                    menu_solicitacao,
                )

    @patch('core.views.api_get')
    def test_consulta_atendimento_para_preenchimento_automatico(
        self,
        api_get,
    ):
        api_get.return_value = self.atendimento_payload()

        response = self.client.get(
            '/requisicao/solicitacao-nota/atendimentos/123456/'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), self.atendimento_payload())
        api_get.assert_has_calls(
            [
                call('/app_glosas/requisicoes/atendimentos/123456'),
                call(
                    '/app_glosas/requisicoes/atendimentos/'
                    '123456/solicitacoes'
                ),
            ]
        )

    @patch('core.views.api_get')
    def test_consulta_reutiliza_cache_por_codigo_de_atendimento(
        self,
        api_get,
    ):
        api_get.return_value = self.atendimento_payload()

        primeira = self.client.get(
            '/requisicao/solicitacao-nota/atendimentos/123456/'
        )
        segunda = self.client.get(
            '/requisicao/solicitacao-nota/atendimentos/123456/'
        )

        self.assertEqual(primeira.status_code, 200)
        self.assertEqual(segunda.status_code, 200)
        self.assertEqual(segunda.json(), self.atendimento_payload())
        self.assertEqual(
            api_get.call_args_list.count(
                call('/app_glosas/requisicoes/atendimentos/123456')
            ),
            1,
        )
        self.assertEqual(
            api_get.call_args_list.count(
                call(
                    '/app_glosas/requisicoes/atendimentos/'
                    '123456/solicitacoes'
                )
            ),
            2,
        )

    @patch('core.views.api_get')
    def test_formulario_e_ajax_compartilham_cache_do_atendimento(
        self,
        api_get,
    ):
        atendimento = self.atendimento_payload()
        atendimento['procedimentos_atendimento'].append(
            {
                'codigo': '10101039',
                'descricao': 'CONSULTA EM PRONTO SOCORRO',
                'grupo': 'HONORÁRIOS MÉDICOS',
                'convenio': 'ISSEC',
                'convenio_elegivel_nfse': False,
                'quantidade': '1',
                'valor_total': '210.00',
                'realizado_em': '2026-07-23T09:45:00',
                'prestador': 'DR. CONVÊNIO',
            }
        )
        atendimento['valor_total_procedimentos'] = '595.50'
        api_get.return_value = atendimento

        ajax = self.client.get(
            '/requisicao/solicitacao-nota/atendimentos/123456/'
        )
        pagina = self.client.get(
            '/requisicao/solicitacao-nota/?codigo_atendimento=123456'
        )

        self.assertEqual(ajax.status_code, 200)
        self.assertEqual(pagina.status_code, 200)
        self.assertContains(pagina, 'MARIA DA SILVA')
        self.assertContains(pagina, 'CONVÊNIO TESTE')
        self.assertContains(pagina, 'name="valor_nota"')
        self.assertContains(pagina, 'Procedimentos e exames realizados')
        self.assertContains(pagina, 'ECOCARDIOGRAMA TRANSTORÁCICO')
        self.assertContains(pagina, 'CONSULTA EM PRONTO SOCORRO')
        self.assertContains(pagina, 'EXAMES CARDIOLÓGICOS')
        self.assertContains(pagina, '<th>Convênio</th>', html=True)
        self.assertContains(
            pagina,
            'note-request-convenio--elegivel',
        )
        self.assertContains(pagina, 'PARTICULAR')
        self.assertContains(pagina, 'note-request-convenio--outro')
        self.assertContains(pagina, 'ISSEC')
        self.assertContains(pagina, 'R$ 385,50')
        self.assertContains(pagina, 'R$ 595,50')
        self.assertContains(pagina, 'Total geral')
        self.assertIn(
            'value="R$ 385,50"',
            pagina.content.decode(),
        )
        self.assertContains(
            pagina,
            'O valor da nota soma somente os procedimentos dos convênios '
            'Particular e Prontorede.',
        )
        self.assertContains(pagina, '23/07/2026 10:30')
        self.assertContains(pagina, 'DR. TESTE')
        self.assertNotContains(
            pagina,
            'Informações recuperadas da view HPC_V_PACIENTES.',
        )
        html = pagina.content.decode()
        cabecalho_tabela_inicio = html.index('<th>Código</th>')
        cabecalho_tabela_fim = html.index('</tr>', cabecalho_tabela_inicio)
        cabecalho_tabela = html[
            cabecalho_tabela_inicio:cabecalho_tabela_fim
        ]
        self.assertLess(
            cabecalho_tabela.index('<th>Convênio</th>'),
            cabecalho_tabela.index('<th>Quantidade</th>'),
        )
        procedimento_inicio = html.index('id="procedimento"')
        procedimento_fim = html.index('</textarea>', procedimento_inicio)
        procedimento_textarea = html[
            procedimento_inicio:procedimento_fim
        ]
        self.assertIn(
            'ECOCARDIOGRAMA TRANSTORÁCICO',
            procedimento_textarea,
        )
        self.assertNotIn('40304361', procedimento_textarea)
        self.assertNotIn(
            '10101039 - CONSULTA EM PRONTO SOCORRO',
            procedimento_textarea,
        )
        self.assertIn(
            'eligibleAttendanceTotal(payload)',
            html,
        )
        self.assertIn(
            'const items = eligibleAttendanceItems(payload);',
            html,
        )
        css = Path(finders.find('css/app.css')).read_text()
        self.assertIn(
            '.note-request-convenio--elegivel {\n'
            '  background: #eaf9f1;\n'
            '  color: #176c4b;',
            css,
        )
        self.assertIn(
            '.note-request-convenio--outro {\n'
            '  background: #fff1ef;\n'
            '  color: #a33127;',
            css,
        )
        codigo_posicao = html.index('id="codigo-atendimento"')
        codigo_input = html[codigo_posicao - 100 : codigo_posicao + 300]
        self.assertIn('type="text"', codigo_input)
        self.assertNotIn('type="number"', codigo_input)
        self.assertLess(
            html.index('name="local"'),
            html.index('name="valor_nota"'),
        )
        self.assertLess(
            html.index('name="valor_nota"'),
            html.index('name="procedimento"'),
        )
        self.assertLess(
            html.index('name="procedimento"'),
            html.index('class="note-request-actions"'),
        )
        self.assertEqual(
            api_get.call_args_list.count(
                call('/app_glosas/requisicoes/atendimentos/123456')
            ),
            1,
        )
        self.assertEqual(
            api_get.call_args_list.count(
                call(
                    '/app_glosas/requisicoes/atendimentos/'
                    '123456/solicitacoes'
                )
            ),
            2,
        )

    @patch('core.views.api_get')
    def test_solicitar_nota_exibe_historico_emitido_e_link_do_pdf(
        self,
        api_get,
    ):
        atendimento = self.atendimento_payload()
        historico = {
            'solicitacoes': [
                {
                    'id': 17,
                    'local': 'Clinica 1',
                    'procedimento': (
                        '40304361 - ECOCARDIOGRAMA TRANSTORÁCICO'
                    ),
                    'valor_nota': '385.50',
                    'cadastrado_por': 'Beatriz Financeiro',
                    'status': 'EMITIDA',
                    'ativo': True,
                    'data_criacao': '2026-07-25T14:30:00',
                    'validado_em': '2026-07-25T15:00:00',
                    'emissao_id': 42,
                    'status_emissao': 'EMITIDA',
                    'numero_nfse': '98765',
                    'arquivo_disponivel': True,
                }
            ],
            'total': 1,
        }
        api_get.side_effect = lambda path, params=None: (
            historico
            if path.endswith('/solicitacoes')
            else atendimento
        )

        response = self.client.get(
            '/requisicao/solicitacao-nota/?codigo_atendimento=123456'
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'Solicitações anteriores deste atendimento',
        )
        self.assertContains(response, 'Solicitação #17')
        self.assertContains(response, 'Solicitada por')
        self.assertContains(response, 'Beatriz Financeiro')
        self.assertContains(response, 'NFS-e emitida')
        self.assertContains(response, '98765')
        self.assertContains(response, 'Visualizar NFS-e')
        self.assertContains(
            response,
            '/requisicao/emissao-nfse/itens/42/pdf/?download=false',
        )

    @patch('core.views.api_post')
    def test_cadastra_solicitacao_e_redireciona(self, api_post):
        api_post.return_value = {
            **self.atendimento_payload(),
            'id': 1,
            'local': 'Clinica 1',
            'procedimento': 'Consulta cardiológica',
            'valor_nota': '60.75',
            'cadastrado_por': 'Amoras',
        }

        response = self.client.post(
            '/requisicao/solicitacao-nota/',
            {
                'codigo_atendimento': '123456',
                'local': 'Clinica 1',
                'procedimento': 'Consulta cardiológica',
                'valor_nota': 'R$ 60,75',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'Solicitação de nota cadastrada com sucesso.',
        )
        api_post.assert_called_once_with(
            '/app_glosas/requisicoes/solicitacoes-nota',
            {
                'codigo_atendimento': 123456,
                'local': 'Clinica 1',
                'procedimento': 'Consulta cardiológica',
                'valor_nota': '60.75',
            },
        )

    @patch('core.views.api_post')
    def test_cadastro_originado_no_acompanhamento_retorna_para_card(
        self,
        api_post,
    ):
        retorno = (
            '/requisicao/acompanhamento-particular/'
            '?data_referencia=2026-07-15'
            '&data_selecionada=2026-07-29'
            '&convenio=PARTICULAR'
        )
        session = self.client.session
        session[ACOMPANHAMENTO_PARTICULAR_CALENDARIO_SESSION_KEY] = {
            'key': 'resumo-anterior',
            'payload': {'total_periodo': 1},
        }
        session.save()
        atendimento_path = (
            f'{REQUISICOES_NOTA_PATH}/atendimentos/123456'
        )
        atendimento_cache_key = build_api_cache_key(
            ATENDIMENTO_NOTA_CACHE_NAMESPACE,
            atendimento_path,
        )
        cache.set(
            atendimento_cache_key,
            {'codigo_atendimento': 123456},
            300,
        )

        response = self.client.post(
            '/requisicao/solicitacao-nota/',
            {
                'codigo_atendimento': '123456',
                'local': 'Clinica 1',
                'procedimento': 'Consulta cardiológica',
                'valor_nota': 'R$ 60,75',
                'retorno': retorno,
            },
        )

        self.assertRedirects(
            response,
            retorno,
            fetch_redirect_response=False,
        )
        self.assertNotIn(
            ACOMPANHAMENTO_PARTICULAR_CALENDARIO_SESSION_KEY,
            self.client.session,
        )
        self.assertIsNone(cache.get(atendimento_cache_key))
        api_post.assert_called_once_with(
            '/app_glosas/requisicoes/solicitacoes-nota',
            {
                'codigo_atendimento': 123456,
                'local': 'Clinica 1',
                'procedimento': 'Consulta cardiológica',
                'valor_nota': '60.75',
            },
        )

    @patch('core.views.get_cached_atendimento_nota')
    @patch('core.views.api_post')
    def test_cadastro_exige_valor_informado_pelo_usuario(
        self,
        api_post,
        get_cached_atendimento,
    ):
        get_cached_atendimento.return_value = self.atendimento_payload()

        response = self.client.post(
            '/requisicao/solicitacao-nota/',
            {
                'codigo_atendimento': '123456',
                'local': 'Clinica 1',
                'procedimento': 'Consulta cardiológica',
                'valor_nota': '',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'Informe um valor da nota maior que zero.',
        )
        api_post.assert_not_called()

    @patch('core.views.api_get')
    def test_lista_exibe_expansao_e_paginacao_no_padrao_triagem(
        self,
        api_get,
    ):
        solicitacao = {
            **self.atendimento_payload(),
            'id': 7,
            'local': 'Clinica 1',
            'procedimento': 'Consulta cardiológica',
            'valor_nota': '60.75',
            'usuario_id': 4,
            'cadastrado_por': 'Amoras',
            'status': 'EMITIDA',
            'data_criacao': '2026-07-23T14:30:00',
            'emissao_id': 42,
            'numero_nfse': '5333',
            'protocolo': 'PROTO-5333',
            'arquivo_disponivel': True,
        }
        api_get.return_value = self.lista_payload(
            [solicitacao],
            total=12,
            offset=10,
            resumo_status=[
                {
                    'status': 'PENDENTE_VALIDACAO',
                    'quantidade': 4,
                    'valor_total': '240.00',
                },
                {
                    'status': 'EMITIDA',
                    'quantidade': 8,
                    'valor_total': '486.00',
                },
            ],
        )

        response = self.client.get(
            '/requisicao/solicitacoes-cadastradas/?page=2'
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '>Expandir</button>')
        self.assertContains(response, '>Colapsar todos</button>')
        self.assertContains(
            response,
            'class="results-list note-request-records"',
        )
        self.assertContains(response, 'Pagina</label>')
        self.assertContains(response, 'de 2</span>')
        self.assertContains(response, 'Atendimento')
        self.assertContains(response, '123456')
        self.assertContains(response, 'Consulta cardiológica')
        self.assertContains(response, 'R$ 60,75')
        self.assertContains(response, 'Amoras')
        self.assertContains(response, 'NFS-e emitida')
        self.assertContains(response, 'Resumo das solicitações por status')
        self.assertContains(response, 'R$ 240,00')
        self.assertContains(response, 'R$ 486,00')
        self.assertContains(response, 'Paciente e atendimento')
        self.assertContains(response, 'Dados da solicitação')
        self.assertContains(response, '5333')
        self.assertContains(response, 'PROTO-5333')
        self.assertContains(response, 'Visualizar NFS-e')
        self.assertContains(response, 'Baixar PDF')
        self.assertContains(
            response,
            '/requisicao/emissao-nfse/itens/42/pdf/?download=false',
        )
        self.assertNotContains(response, '>Editar solicitação</button>')
        self.assertNotContains(response, '>Inativar solicitação</button>')
        self.assertNotContains(response, '<small>Código paciente</small>')
        self.assertNotContains(response, '<small>Código convênio</small>')
        self.assertNotContains(
            response,
            '<span class="panel-title">Solicitações cadastradas</span>',
        )
        api_get.assert_any_call(
            '/app_glosas/requisicoes/solicitacoes-nota',
            {'limit': 10, 'offset': 10},
        )
        api_get.assert_any_call('/app_glosas/convenios', None)

    @patch('core.views.api_get')
    def test_lista_filtra_e_preserva_criterios_na_paginacao(
        self,
        api_get,
    ):
        solicitacao = {
            **self.atendimento_payload(),
            'id': 7,
            'local': 'Clinica 2',
            'procedimento': 'Consulta cardiológica',
            'valor_nota': '60.75',
            'usuario_id': 4,
            'cadastrado_por': 'Amoras',
            'status': 'PENDENTE_VALIDACAO',
            'data_criacao': '2026-07-23T14:30:00',
        }
        api_get.return_value = self.lista_payload(
            [solicitacao],
            total=11,
            resumo_status=[
                {
                    'status': 'PENDENTE_VALIDACAO',
                    'quantidade': 11,
                    'valor_total': '668.25',
                },
            ],
        )

        response = self.client.get(
            '/requisicao/solicitacoes-cadastradas/',
            {
                'codigo_atendimento': '123456',
                'nome_paciente': 'Maria',
                'convenio': 'Teste',
                'local': 'Clinica 2',
                'status': 'PENDENTE_VALIDACAO',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="codigo_atendimento"')
        self.assertContains(response, 'type="search"')
        self.assertContains(response, 'pattern="[0-9]*"')
        self.assertNotContains(response, 'type="number"')
        self.assertContains(response, 'value="123456"')
        self.assertContains(response, 'name="nome_paciente"')
        self.assertContains(response, 'value="Maria"')
        self.assertContains(
            response,
            '<select name="convenio" class="form-select">',
        )
        self.assertContains(
            response,
            '<option value="Teste" selected>Teste</option>',
        )
        self.assertContains(response, 'R$ 668,25')
        self.assertContains(response, '>Editar solicitação</button>')
        self.assertContains(response, '>Inativar solicitação</button>')
        self.assertContains(response, 'name="action" value="editar"')
        self.assertContains(response, 'name="action" value="inativar"')
        self.assertContains(
            response,
            'name="status" value="PENDENTE_VALIDACAO"',
        )
        api_get.assert_any_call(
            '/app_glosas/requisicoes/solicitacoes-nota',
            {
                'codigo_atendimento': '123456',
                'nome_paciente': 'Maria',
                'convenio': 'Teste',
                'local': 'Clinica 2',
                'status': 'PENDENTE_VALIDACAO',
                'limit': 10,
                'offset': 0,
            },
        )
        api_get.assert_any_call('/app_glosas/convenios', None)

    @patch('core.views.api_get')
    def test_lista_bloqueia_edicao_e_inativacao_de_validada(
        self,
        api_get,
    ):
        solicitacao = {
            **self.atendimento_payload(),
            'id': 7,
            'local': 'Clinica 1',
            'procedimento': 'Consulta cardiológica',
            'valor_nota': '60.75',
            'usuario_id': 4,
            'cadastrado_por': 'Amoras',
            'status': 'VALIDADA',
            'data_criacao': '2026-07-23T14:30:00',
        }
        api_get.return_value = self.lista_payload(
            [solicitacao],
            total=1,
        )

        response = self.client.get(
            '/requisicao/solicitacoes-cadastradas/'
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '>Editar solicitação</button>')
        self.assertNotContains(response, '>Inativar solicitação</button>')
        self.assertContains(
            response,
            'Edição e inativação indisponíveis após a validação.',
        )

    @patch('core.views.api_patch')
    def test_lista_edita_solicitacao_e_preserva_filtros(
        self,
        api_patch,
    ):
        response = self.client.post(
            (
                '/requisicao/solicitacoes-cadastradas/'
                '?status=RECUSADA&page=2'
            ),
            {
                'action': 'editar',
                'solicitacao_id': '7',
                'local': 'Emergencia',
                'valor_nota': 'R$ 99,90',
                'procedimento': 'Procedimento corrigido',
            },
        )

        self.assertRedirects(
            response,
            (
                '/requisicao/solicitacoes-cadastradas/'
                '?status=RECUSADA&page=2'
            ),
            fetch_redirect_response=False,
        )
        api_patch.assert_called_once_with(
            '/app_glosas/requisicoes/solicitacoes-nota/7',
            {
                'local': 'Emergencia',
                'procedimento': 'Procedimento corrigido',
                'valor_nota': '99.90',
            },
        )

    @patch('core.views.api_delete')
    def test_lista_inativa_solicitacao(self, api_delete):
        response = self.client.post(
            '/requisicao/solicitacoes-cadastradas/',
            {
                'action': 'inativar',
                'solicitacao_id': '7',
            },
        )

        self.assertRedirects(
            response,
            '/requisicao/solicitacoes-cadastradas/',
            fetch_redirect_response=False,
        )
        api_delete.assert_called_once_with(
            '/app_glosas/requisicoes/solicitacoes-nota/7'
        )

    @patch('core.views.api_get')
    def test_workflow_lista_pendentes_com_acoes_de_validacao(
        self,
        api_get,
    ):
        workflow = self.workflow_payload()
        workflow['procedimentos_atendimento'] = [
            {
                'codigo': '40304361',
                'descricao': 'ECOCARDIOGRAMA TRANSTORÁCICO',
                'grupo': 'EXAMES CARDIOLÓGICOS',
                'convenio': 'PARTICULAR',
                'convenio_elegivel_nfse': True,
                'quantidade': '1',
                'valor_total': '385.50',
                'realizado_em': '2026-07-23T10:30:00',
                'prestador': 'DR. TESTE',
            },
            {
                'codigo': '10101012',
                'descricao': 'CONSULTA EM CARDIOLOGIA',
                'grupo': 'PROCEDIMENTOS',
                'convenio': 'ISSEC',
                'convenio_elegivel_nfse': False,
                'quantidade': '1',
                'valor_total': '210.00',
                'realizado_em': '2026-07-23T09:45:00',
                'prestador': None,
            },
        ]
        workflow['valor_total_procedimentos'] = '595.50'
        workflow['solicitacoes_anteriores'] = [
            {
                'id': 3,
                'local': 'Clinica 2',
                'procedimento': 'Consulta anterior',
                'valor_nota': '210.00',
                'cadastrado_por': 'Beatriz Financeiro',
                'status': 'VALIDADA',
                'ativo': True,
                'data_criacao': '2026-07-22T08:30:00',
                'validado_em': '2026-07-22T09:00:00',
                'emissao_id': None,
                'status_emissao': None,
                'numero_nfse': None,
                'arquivo_disponivel': False,
            },
            {
                'id': 2,
                'local': 'Clinica 1',
                'procedimento': 'PROCEDIMENTO RECUSADO OCULTO',
                'valor_nota': '180.00',
                'cadastrado_por': 'Carlos Auditoria',
                'motivo': 'CPF divergente.',
                'status': 'RECUSADA',
                'ativo': True,
                'data_criacao': '2026-07-21T08:30:00',
                'validado_em': '2026-07-21T09:00:00',
                'emissao_id': None,
                'status_emissao': None,
                'numero_nfse': None,
                'arquivo_disponivel': False,
            },
            {
                'id': 1,
                'local': 'Clinica 1',
                'procedimento': 'PROCEDIMENTO INATIVO OCULTO',
                'valor_nota': '160.00',
                'cadastrado_por': 'Daniela Financeiro',
                'motivo': (
                    'Solicitação inativada no status '
                    'PENDENTE_VALIDACAO.'
                ),
                'status': 'PENDENTE_VALIDACAO',
                'ativo': False,
                'data_criacao': '2026-07-20T08:30:00',
                'validado_em': None,
                'emissao_id': None,
                'status_emissao': None,
                'numero_nfse': None,
                'arquivo_disponivel': False,
            },
        ]
        api_get.side_effect = lambda path, params=None: (
            self.empresas_payload()
            if path.endswith('/empresas-emissoras')
            else self.lista_payload([workflow], total=1)
        )

        response = self.client.get(
            '/requisicao/workflow-solicitacoes/'
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<h1>Follow-Up Solicitações</h1>')
        self.assertNotContains(response, 'Workflow Solicitações')
        self.assertContains(
            response,
            'class="results-list workflow-request-list"',
        )
        self.assertContains(response, 'Confirmar validação')
        self.assertContains(response, 'Recusar dados')
        self.assertContains(response, 'Consulta cardiológica')
        self.assertContains(response, 'R$ 60,75')
        self.assertContains(response, 'Amoras')
        self.assertContains(response, 'Procedimentos e exames realizados')
        self.assertContains(response, 'ECOCARDIOGRAMA TRANSTORÁCICO')
        self.assertContains(response, 'EXAMES CARDIOLÓGICOS')
        self.assertContains(response, '<th>Convênio</th>')
        self.assertContains(response, 'PARTICULAR')
        self.assertContains(response, 'ISSEC')
        self.assertContains(
            response,
            'workflow-procedure-convenio--elegivel',
        )
        self.assertContains(response, 'workflow-procedure-convenio--outro')
        self.assertContains(response, 'R$ 385,50')
        self.assertContains(response, 'R$ 210,00')
        self.assertContains(response, 'R$ 595,50')
        self.assertContains(response, 'Total geral')
        self.assertContains(
            response,
            'Solicitações anteriores deste atendimento',
        )
        self.assertContains(response, 'Solicitação #3')
        self.assertContains(response, 'Solicitada por')
        self.assertContains(response, 'Beatriz Financeiro')
        self.assertContains(response, 'Consulta anterior')
        self.assertContains(response, 'CPF divergente.')
        self.assertContains(
            response,
            'Solicitação inativada no status PENDENTE_VALIDACAO.',
        )
        self.assertNotContains(response, 'PROCEDIMENTO RECUSADO OCULTO')
        self.assertNotContains(response, 'PROCEDIMENTO INATIVO OCULTO')
        self.assertContains(response, 'Validada')
        self.assertContains(
            response,
            'workflow-previous-request-status--validada',
        )
        self.assertContains(response, '23/07/2026 10:30:00')
        self.assertContains(response, 'DR. TESTE')
        self.assertContains(response, '2 itens')
        self.assertContains(response, 'CNPJ emissor *')
        self.assertContains(response, '05.613.278/0001-58')
        html = response.content.decode()
        cabecalho_inicio = html.index('class="workflow-request-header"')
        cabecalho_fim = html.index('</div>', cabecalho_inicio)
        cabecalho = html[cabecalho_inicio:cabecalho_fim]
        self.assertIn('<small>Tipo</small>', cabecalho)
        self.assertNotIn('<small>Tipo atendimento</small>', cabecalho)
        self.assertIn('<small>Valor</small>', cabecalho)
        self.assertNotIn('<small>Valor da nota</small>', cabecalho)
        self.assertLess(
            cabecalho.index('<small>Tipo</small>'),
            cabecalho.index('<small>Convênio</small>'),
        )
        self.assertLess(
            cabecalho.index('<small>Convênio</small>'),
            cabecalho.index('<small>Local</small>'),
        )
        self.assertIn('CONVÊNIO TESTE', cabecalho)
        self.assertLess(
            html.index('CNPJ emissor *'),
            html.index('Procedimentos e exames realizados'),
        )
        self.assertGreater(
            html.index('>Confirmar validação</button>'),
            html.index('Procedimentos e exames realizados'),
        )
        self.assertGreater(
            html.index('>Recusar dados</button>'),
            html.index('Procedimentos e exames realizados'),
        )
        self.assertIn(
            'form="workflow-approval-form-7"',
            html,
        )
        self.assertIn(
            '<td colspan="5"><strong>Total geral</strong></td>',
            html,
        )
        tabela_inicio = html.index('<th>Código</th>')
        tabela_fim = html.index('</tr>', tabela_inicio)
        cabecalho_tabela = html[tabela_inicio:tabela_fim]
        self.assertLess(
            cabecalho_tabela.index('<th>Convênio</th>'),
            cabecalho_tabela.index('<th>Quantidade</th>'),
        )
        self.assertIn('x-show="!recusar"', html)
        self.assertIn('@click="recusar = true"', html)
        self.assertContains(response, 'Confirmar recusa')
        self.assertNotContains(response, '<small>Código paciente</small>')
        self.assertNotContains(response, '<small>Código convênio</small>')
        self.assertContains(response, '<small>Convênio</small>')
        css = Path(finders.find('css/app.css')).read_text()
        self.assertIn(
            '.workflow-approval-form {\n'
            '  display: flex;\n'
            '  gap: 0.55rem;\n'
            '  align-items: flex-end;\n'
            '  margin-right: auto !important;',
            css,
        )
        self.assertIn(
            '.workflow-attendance-total-row td:first-child {\n'
            '  text-align: left;',
            css,
        )
        self.assertIn(
            '.workflow-procedure-convenio--elegivel {\n'
            '  background: #eaf9f1;\n'
            '  color: #176c4b;',
            css,
        )
        self.assertIn(
            '.workflow-procedure-convenio--outro {\n'
            '  background: #fff1ef;\n'
            '  color: #a33127;',
            css,
        )
        self.assertIn(
            '.workflow-request-card--validacao,\n'
            '.workflow-request-card--emissao {\n'
            '  min-width: 0;',
            css,
        )
        self.assertIn(
            '.workflow-request-card--validacao '
            '.workflow-request-header {\n'
            '  grid-template-columns:',
            css,
        )
        self.assertIn(
            '.workflow-request-card--validacao '
            '.workflow-request-field,\n'
            '.workflow-request-card--validacao '
            '.workflow-request-field small,\n'
            '.workflow-request-card--emissao '
            '.workflow-request-field,\n'
            '.workflow-request-card--emissao '
            '.workflow-request-field small {\n'
            '  overflow: hidden;\n'
            '  text-overflow: ellipsis;\n'
            '  white-space: nowrap;',
            css,
        )
        api_get.assert_any_call(
            '/app_glosas/requisicoes/solicitacoes-nota/workflow',
            {
                'status': 'PENDENTE_VALIDACAO',
                'limit': 10,
                'offset': 0,
            },
        )

    @patch('core.views.api_get')
    def test_workflow_encaminha_filtros_e_preserva_paginacao(
        self,
        api_get,
    ):
        def responder(path, params=None):
            if path.endswith('/empresas-emissoras'):
                return self.empresas_payload()
            if path.endswith('/convenios'):
                return {
                    'convenios': [
                        {'nm_convenio': 'CONVÊNIO TESTE'},
                    ],
                }
            return self.lista_payload(
                [self.workflow_payload()],
                total=12,
                offset=10,
            )

        api_get.side_effect = responder
        response = self.client.get(
            '/requisicao/workflow-solicitacoes/',
            {
                'codigo_atendimento': '123456',
                'nome_paciente': 'Maria',
                'cpf': '12345678901',
                'convenio': 'CONVÊNIO TESTE',
                'tipo_atendimento': 'Ambulatório',
                'local': 'Clinica 1',
                'data_inicio': '2026-07-01',
                'data_fim': '2026-07-31',
                'page': '2',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="123456"')
        self.assertContains(response, 'value="Maria"')
        self.assertContains(response, 'value="12345678901"')
        self.assertContains(
            response,
            '<option value="CONVÊNIO TESTE" selected>',
        )
        self.assertContains(
            response,
            '<option value="Ambulatório" selected>',
        )
        self.assertContains(
            response,
            '<option value="Clinica 1" selected>',
        )
        self.assertContains(response, '<label>Data inicial</label>')
        self.assertContains(response, '<label>Data final</label>')
        self.assertContains(
            response,
            'class="workflow-request-date-filters"',
        )
        self.assertContains(
            response,
            'name="data_inicio"\n          value="2026-07-01"',
        )
        self.assertContains(
            response,
            'name="data_fim"\n          value="2026-07-31"',
        )
        css = Path(finders.find('css/app.css')).read_text()
        self.assertIn(
            '.workflow-request-date-filters {\n'
            '  display: grid;\n'
            '  grid-template-columns: repeat(2, minmax(0, 1fr));\n'
            '  grid-column: 1 / span 2;',
            css,
        )
        self.assertContains(
            response,
            'href="/requisicao/workflow-solicitacoes/"',
        )
        api_get.assert_any_call(
            '/app_glosas/requisicoes/solicitacoes-nota/workflow',
            {
                'status': 'PENDENTE_VALIDACAO',
                'limit': 10,
                'offset': 10,
                'codigo_atendimento': '123456',
                'nome_paciente': 'Maria',
                'cpf': '12345678901',
                'convenio': 'CONVÊNIO TESTE',
                'tipo_atendimento': 'Ambulatório',
                'local': 'Clinica 1',
                'data_inicio': '2026-07-01',
                'data_fim': '2026-07-31',
            },
        )
        self.assertEqual(
            response.context['pagination']['query'],
            {
                'codigo_atendimento': '123456',
                'nome_paciente': 'Maria',
                'cpf': '12345678901',
                'convenio': 'CONVÊNIO TESTE',
                'tipo_atendimento': 'Ambulatório',
                'local': 'Clinica 1',
                'data_inicio': '2026-07-01',
                'data_fim': '2026-07-31',
            },
        )

    def test_cards_expansiveis_preservam_altura_natural_da_triagem(self):
        css = Path(finders.find('css/app.css')).read_text()

        self.assertIn(
            '.results-list {\n'
            '  flex: 1 1 auto;\n'
            '  min-height: 0;\n'
            '  overflow-y: auto;',
            css,
        )
        self.assertIn(
            '.note-request-records {\n'
            '  display: block;',
            css,
        )
        self.assertIn(
            '.note-request-record + .note-request-record {\n'
            '  margin-top: 0.62rem;',
            css,
        )
        self.assertIn(
            '.workflow-request-list {\n'
            '  display: block;',
            css,
        )
        self.assertIn(
            '.workflow-request-card + .workflow-request-card {\n'
            '  margin-top: 0.65rem;',
            css,
        )

    @patch('core.views.api_post')
    @patch('core.views.api_put')
    def test_workflow_confirma_validacao(self, api_put, api_post):
        api_post.return_value = {
            **self.workflow_payload('VALIDADA'),
            'validacao': 'VALIDADA',
        }

        response = self.client.post(
            '/requisicao/workflow-solicitacoes/',
            {
                'solicitacao_id': '7',
                'decisao': 'VALIDADA',
                'empresa_emissora_id': '3',
            },
        )

        self.assertEqual(response.status_code, 302)
        api_put.assert_called_once_with(
            '/app_glosas/requisicoes/solicitacoes-nota/'
            '7/empresa-emissora',
            {'empresa_emissora_id': 3},
        )
        api_post.assert_called_once_with(
            '/app_glosas/requisicoes/solicitacoes-nota/7/validacao',
            {
                'decisao': 'VALIDADA',
                'motivo_recusa': None,
            },
        )

    @patch('core.views.api_get')
    def test_solicitacoes_recusas_exibe_recusa_e_inativacao(self, api_get):
        recusada = {
            **self.workflow_payload('RECUSADA'),
            'validacao': 'RECUSADA',
            'motivo_recusa': 'CPF divergente.',
            'validado_por_id': 4,
            'validado_por': 'Amoras',
            'validado_em': '2026-07-23T15:00:00',
        }
        inativada = {
            **self.workflow_payload('PENDENTE_VALIDACAO'),
            'id': 8,
            'ativo': False,
            'procedimento': 'Solicitação cancelada',
            'inativado_por_id': 5,
            'inativado_por': 'Gestor Operação',
            'inativado_em': '2026-07-23T16:30:00',
        }
        api_get.return_value = self.lista_payload(
            [recusada, inativada],
            total=2,
        )

        response = self.client.get(
            '/requisicao/solicitacoes-recusas/'
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'Solicitações recusadas ou inativadas',
        )
        self.assertContains(response, 'CPF divergente.')
        self.assertContains(response, 'Amoras')
        self.assertContains(
            response,
            'workflow-request-status--recusa',
        )
        self.assertContains(
            response,
            'workflow-request-status--inativo',
        )
        self.assertContains(response, 'Solicitação cancelada')
        self.assertContains(response, 'Inativado por')
        self.assertContains(response, 'Gestor Operação')
        self.assertContains(response, 'Inativado em')
        self.assertContains(response, '23/07/2026 16:30')
        self.assertNotContains(
            response,
            '<small>Código paciente</small>',
        )
        self.assertNotContains(
            response,
            '<small>Código convênio</small>',
        )
        self.assertContains(response, '<small>Convênio</small>')
        api_get.assert_called_once_with(
            '/app_glosas/requisicoes/solicitacoes-nota/workflow',
            {
                'status': 'RECUSADA',
                'limit': 10,
                'offset': 0,
                'incluir_inativas': 'true',
            },
        )

    @patch('core.views.api_get')
    def test_emissao_filtra_solicitacoes_e_exibe_acoes(self, api_get):
        api_get.side_effect = lambda path, params=None: (
            self.empresas_payload()
            if path.endswith('/empresas-emissoras')
            else self.lista_payload(
                [self.workflow_payload('VALIDADA')],
                total=1,
            )
        )

        response = self.client.get(
            '/requisicao/emissao-nfse/'
            '?nome_paciente=Maria&cpf=123&'
            'tipo_atendimento=Ambulat%C3%B3rio&local=Clinica+1&'
            'cnpj_emissor=05613278000158'
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="workflow-emission-page"')
        self.assertContains(
            response,
            'class="results-list workflow-request-list"',
        )
        self.assertContains(response, 'Emitir selecionadas')
        self.assertContains(response, 'Emitir esta NFS-e')
        self.assertContains(response, 'Reverter para recusa')
        self.assertContains(response, 'Confirmar reversão')
        html = response.content.decode()
        cabecalho_inicio = html.index('class="workflow-request-header"')
        cabecalho_fim = html.index('</div>', cabecalho_inicio)
        cabecalho = html[cabecalho_inicio:cabecalho_fim]
        self.assertIn('<small>Tipo</small>', cabecalho)
        self.assertNotIn('<small>Tipo atendimento</small>', cabecalho)
        self.assertIn('<small>Valor</small>', cabecalho)
        self.assertNotIn('<small>Valor da nota</small>', cabecalho)
        self.assertLess(
            cabecalho.index('<small>Tipo</small>'),
            cabecalho.index('<small>Convênio</small>'),
        )
        self.assertLess(
            cabecalho.index('<small>Convênio</small>'),
            cabecalho.index('<small>Local</small>'),
        )
        self.assertIn('CONVÊNIO TESTE', cabecalho)
        reverter_inicio = html.index('>Reverter para recusa</button>')
        reverter_botao = html[reverter_inicio - 180 : reverter_inicio]
        self.assertIn('x-show="!recusar"', reverter_botao)
        self.assertIn('@click="recusar = true"', reverter_botao)
        self.assertContains(
            response,
            'name="form_action" value="recusar"',
        )
        self.assertContains(response, 'R$ 60,75')
        self.assertContains(response, 'Validada por')
        self.assertContains(response, 'Amoras')
        self.assertContains(response, 'form="batch-emission-form"')
        self.assertContains(response, 'Empresa emissora')
        self.assertContains(response, '05.613.278/0001-58')
        self.assertNotContains(response, '<small>Código paciente</small>')
        self.assertNotContains(response, '<small>Código convênio</small>')
        css = Path(finders.find('css/app.css')).read_text()
        self.assertIn(
            '.workflow-request-card--validacao,\n'
            '.workflow-request-card--emissao {\n'
            '  min-width: 0;',
            css,
        )
        self.assertIn(
            '.workflow-request-card--emissao '
            '.workflow-request-header {\n'
            '  grid-template-columns:',
            css,
        )
        self.assertIn(
            '.workflow-request-card--validacao '
            '.workflow-request-field,\n'
            '.workflow-request-card--validacao '
            '.workflow-request-field small,\n'
            '.workflow-request-card--emissao '
            '.workflow-request-field,\n'
            '.workflow-request-card--emissao '
            '.workflow-request-field small {\n'
            '  overflow: hidden;\n'
            '  text-overflow: ellipsis;\n'
            '  white-space: nowrap;',
            css,
        )
        api_get.assert_any_call(
            '/app_glosas/requisicoes/emissoes-nfse',
            {
                'limit': 10,
                'offset': 0,
                'nome_paciente': 'Maria',
                'cpf': '123',
                'tipo_atendimento': 'Ambulatório',
                'local': 'Clinica 1',
                'cnpj_emissor': '05613278000158',
            },
        )

    @patch('core.views.api_get')
    def test_emissao_mantem_cards_e_exibe_resultado_real(self, api_get):
        validada = {
            **self.workflow_payload('VALIDADA'),
            'emissao_id': None,
            'lote_id': None,
            'status_emissao': None,
            'numero_nfse': None,
            'protocolo': None,
            'erro_emissao': None,
            'emissao_criada_em': None,
            'emissao_atualizada_em': None,
            'arquivo_disponivel': False,
        }
        processando = {
            **self.workflow_payload('EMISSAO_SOLICITADA'),
            'id': 8,
            'validacao': 'VALIDADA',
            'emissao_id': 41,
            'lote_id': 12,
            'status_emissao': 'PROCESSANDO',
            'numero_nfse': None,
            'protocolo': None,
            'erro_emissao': None,
            'emissao_criada_em': '2026-07-23T16:00:00',
            'emissao_atualizada_em': '2026-07-23T16:01:00',
            'arquivo_disponivel': False,
        }
        emitida = {
            **self.workflow_payload('EMITIDA'),
            'id': 9,
            'validacao': 'VALIDADA',
            'emissao_id': 42,
            'lote_id': 13,
            'status_emissao': 'EMITIDA',
            'numero_nfse': '5333',
            'protocolo': 'PROTO-5333',
            'erro_emissao': None,
            'emissao_criada_em': '2026-07-23T16:02:00',
            'emissao_atualizada_em': '2026-07-23T16:04:00',
            'arquivo_disponivel': True,
        }
        erro = {
            **self.workflow_payload('ERRO_EMISSAO'),
            'id': 10,
            'validacao': 'VALIDADA',
            'emissao_id': 43,
            'lote_id': 14,
            'status_emissao': 'ERRO',
            'numero_nfse': None,
            'protocolo': None,
            'erro_emissao': 'Falha de comunicação com o portal.',
            'emissao_criada_em': '2026-07-23T16:05:00',
            'emissao_atualizada_em': '2026-07-23T16:06:00',
            'arquivo_disponivel': False,
        }
        api_get.return_value = self.lista_payload(
            [validada, processando, emitida, erro],
            total=4,
        )

        response = self.client.get('/requisicao/emissao-nfse/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="solicitacao_ids"', count=2)
        self.assertContains(response, 'Emitir esta NFS-e', count=1)
        self.assertContains(response, 'Tentar emitir novamente', count=1)
        self.assertContains(response, 'Emissão solicitada')
        self.assertContains(response, 'Em processamento')
        self.assertContains(response, 'NFS-e emitida')
        self.assertContains(response, '5333')
        self.assertContains(response, 'PROTO-5333')
        self.assertContains(response, 'Falha de comunicação com o portal.')
        self.assertContains(
            response,
            '/requisicao/emissao-nfse/itens/42/pdf/?download=false',
        )
        self.assertContains(
            response,
            '/requisicao/emissao-nfse/itens/42/pdf/?download=true',
        )
        self.assertContains(response, 'Visualizar NFS-e')
        self.assertContains(response, 'Baixar PDF')
        self.assertContains(response, 'workflow-emission-spinner', count=4)
        self.assertContains(
            response,
            'window.setTimeout(() => window.location.reload(), 5000)',
        )

    @patch('core.views.api_get')
    def test_cadastro_empresas_exibe_dados_iniciais_e_estado(self, api_get):
        api_get.return_value = self.empresas_payload()

        response = self.client.get('/administrativo/empresas-emissoras/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<h1>Empresas (Emissão NFS-e)</h1>')
        self.assertContains(response, '05.613.278/0001-58')
        self.assertContains(response, '08.711.085/0001-28')
        self.assertContains(response, 'Ativa', count=2)
        self.assertContains(
            response,
            'class="admin-table issuer-admin-table"',
        )
        self.assertContains(response, '>Editar</a>', count=2)
        self.assertContains(
            response,
            'name="form_action" value="inativar"',
            count=2,
        )
        self.assertContains(response, 'Sistema')
        css = Path(finders.find('css/app.css')).read_text()
        self.assertIn(
            '.issuer-admin-table th:first-child,\n'
            '.issuer-admin-table td:first-child {\n'
            '  width: 190px;\n'
            '  padding-right: 18px;\n'
            '  text-align: left;',
            css,
        )
        self.assertIn(
            '.issuer-admin-table th:nth-child(2),\n'
            '.issuer-admin-table td:nth-child(2) {\n'
            '  padding-left: 18px;\n'
            '  overflow-wrap: anywhere;',
            css,
        )
        api_get.assert_called_once_with(
            '/app_glosas/requisicoes/empresas-emissoras',
            {'incluir_inativas': 'true'},
        )

    @patch('core.views.api_post')
    def test_emissao_em_lote_encaminha_ids_ao_airflow(self, api_post):
        api_post.return_value = {
            'lote_id': 12,
            'message': 'DAG acionada.',
        }

        response = self.client.post(
            '/requisicao/emissao-nfse/',
            {'solicitacao_ids': ['7', '8']},
        )

        self.assertEqual(response.status_code, 302)
        api_post.assert_called_once_with(
            '/app_glosas/requisicoes/emissoes-nfse',
            {'solicitacao_ids': [7, 8]},
        )

    @patch('core.views.api_post')
    def test_emissao_reverte_validada_para_recusa(self, api_post):
        api_post.return_value = {
            **self.workflow_payload('RECUSADA'),
            'motivo_recusa': 'Convênio divergente.',
        }

        response = self.client.post(
            '/requisicao/emissao-nfse/?nome_paciente=Maria',
            {
                'form_action': 'recusar',
                'solicitacao_id': '7',
                'motivo_recusa': 'Convênio divergente.',
            },
        )

        self.assertRedirects(
            response,
            '/requisicao/emissao-nfse/?nome_paciente=Maria',
            fetch_redirect_response=False,
        )
        api_post.assert_called_once_with(
            '/app_glosas/requisicoes/solicitacoes-nota/7/validacao',
            {
                'decisao': 'RECUSADA',
                'motivo_recusa': 'Convênio divergente.',
            },
        )

    @patch('core.views.api_get_stream')
    def test_proxy_pdf_encaminha_modo_e_entrega_stream(
        self,
        api_get_stream,
    ):
        for download, disposition in (
            ('false', 'inline; filename="nfse-42.pdf"'),
            ('true', 'attachment; filename="nfse-42.pdf"'),
        ):
            with self.subTest(download=download):
                upstream = Mock()
                upstream.headers = {
                    'Content-Type': 'application/pdf',
                    'Content-Disposition': disposition,
                    'Content-Length': '17',
                }
                upstream.iter_content.return_value = [
                    b'%PDF-1.4',
                    b' conteudo',
                ]
                api_get_stream.return_value = upstream

                response = self.client.get(
                    '/requisicao/emissao-nfse/itens/42/pdf/'
                    f'?download={download}'
                )

                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.streaming)
                self.assertEqual(
                    response['Content-Type'],
                    'application/pdf',
                )
                self.assertEqual(
                    response['Content-Disposition'],
                    disposition,
                )
                self.assertEqual(
                    b''.join(response.streaming_content),
                    b'%PDF-1.4 conteudo',
                )
                api_get_stream.assert_called_once_with(
                    '/app_glosas/requisicoes/'
                    'emissoes-nfse/itens/42/pdf',
                    {'download': download},
                )
                upstream.close.assert_called_once_with()
                api_get_stream.reset_mock()

    @patch('core.views.api_get_stream')
    def test_proxy_pdf_preserva_erro_da_api(self, api_get_stream):
        api_get_stream.side_effect = ApiError(
            '{"detail":"PDF da NFS-e não encontrado."}',
            404,
        )

        response = self.client.get(
            '/requisicao/emissao-nfse/itens/42/pdf/'
        )

        self.assertEqual(response.status_code, 404)
        self.assertContains(
            response,
            'PDF da NFS-e não encontrado.',
            status_code=404,
        )

    @patch('core.views.api_get')
    def test_acompanhamento_exibe_nfse_emitida_diretamente_no_iss(
        self,
        api_get,
    ):
        payload = self.acompanhamento_particular_payload()
        atendimento = {
            'codigo_atendimento': 123459,
            'codigo_paciente': 792,
            'codigo_convenio': 3,
            'nome_paciente': 'CLARA NOTA EXTERNA',
            'nr_cpf': '12345678901',
            'convenio': 'PARTICULAR',
            'tipo_atendimento': 'Ambulatório',
            'data_atendimento': '2026-07-29T08:00:00',
            'data_alta': None,
            'valor_conta': '385.50',
            'quantidade_lancamentos': 1,
            'status': 'EMITIDA_DIRETAMENTE_ISS',
            'solicitacao_id': None,
            'workflow_status': None,
            'emissao_id': None,
            'emissao_status': None,
            'cnpj_emissor': '59932105000121',
            'razao_social_emissor': 'CLINICA PRONTOCARDIO LTDA',
            'numero_nfse': '54321',
            'codigo_verificacao_nfse': '123456789',
            'valor_nfse': '385.50',
            'nfse_externa_row_hash': 'nfse-externa-iss',
            'arquivo_disponivel': True,
            'emissao_atualizada_em': '2026-07-29T10:15:00',
            'solicitacao': None,
        }
        payload['atendimentos'] = [atendimento]
        payload['total'] = 1
        payload['total_periodo'] = 1
        payload['valor_total_periodo'] = '385.50'
        payload['resumo_status'] = [{
            'status': 'EMITIDA_DIRETAMENTE_ISS',
            'quantidade': 1,
            'valor_total': '385.50',
        }]
        payload['resumo_diario'] = [{
            'data': '2026-07-29',
            'total': 1,
            'emitidas': 1,
            'pendentes': 0,
            'valor_total': '385.50',
            'resumo_status': payload['resumo_status'],
            'pacientes': [{
                'nome': 'CLARA NOTA EXTERNA',
                'inicial': 'C',
                'status': 'EMITIDA_DIRETAMENTE_ISS',
            }],
            'pacientes_restantes': 0,
        }]
        self.configurar_api_acompanhamento(api_get, payload)

        response = self.client.get(
            '/requisicao/acompanhamento-particular/'
            '?data_referencia=2026-07-15&data_selecionada=2026-07-29'
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Emitida diretamente no ISS')
        self.assertContains(
            response,
            'NFS-e emitida diretamente no ISS Fortaleza',
        )
        self.assertContains(response, '12345678901')
        self.assertContains(response, '54321')
        self.assertContains(response, '123456789')
        self.assertContains(response, 'R$ 385,50')
        self.assertContains(
            response,
            '/requisicao/nfse-externas/nfse-externa-iss/pdf/'
            '?download=false',
        )
        self.assertContains(response, 'Baixar PDF do ISS')
        self.assertNotContains(response, '>Solicitar nota</a>')

    @patch('core.views.api_get_stream')
    def test_proxy_pdf_nfse_externa_encaminha_hash_e_entrega_stream(
        self,
        api_get_stream,
    ):
        upstream = Mock()
        upstream.headers = {
            'Content-Type': 'application/pdf',
            'Content-Disposition': 'inline; filename="nfse-iss.pdf"',
            'Content-Length': '17',
        }
        upstream.iter_content.return_value = [
            b'%PDF-1.7',
            b' externa',
        ]
        api_get_stream.return_value = upstream

        response = self.client.get(
            '/requisicao/nfse-externas/nfse-externa-iss/pdf/'
            '?download=false'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            b''.join(response.streaming_content),
            b'%PDF-1.7 externa',
        )
        api_get_stream.assert_called_once_with(
            '/app_glosas/requisicoes/'
            'nfse-externas/nfse-externa-iss/pdf',
            {'download': 'false'},
        )
        upstream.close.assert_called_once_with()
