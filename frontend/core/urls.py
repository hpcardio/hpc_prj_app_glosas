from django.urls import path

from . import views, petrobras, openfinance, contas_receber

urlpatterns = [
    path("follow-up-glosas/petrobras/<path:endpoint>", petrobras.bridge, name="petrobras_bridge"),
    path('financeiro/saldos-extratos/', openfinance.screen, name='openfinance'),
    path('financeiro/saldos-extratos/titulares/<uuid:owner_id>/conectar/', openfinance.connect_token, name='openfinance_connect'),
    path('financeiro/saldos-extratos/titulares/<uuid:owner_id>/vincular/', openfinance.attach, name='openfinance_attach'),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path(
        "automacao/spu/recaptcha/status/",
        views.spu_recaptcha_status,
        name="spu_recaptcha_status",
    ),
    path(
        "automacao/spu/recaptcha/tela/",
        views.spu_recaptcha_viewer,
        name="spu_recaptcha_viewer",
    ),
    path(
        "automacao/spu/vnc/<path:asset_path>",
        views.spu_novnc_asset,
        name="spu_novnc_asset",
    ),
    path("acesso-negado/", views.access_denied, name="access_denied"),
    path("primeiro-acesso/", views.primeiro_acesso, name="primeiro_acesso"),
    path("esqueci-senha/", views.forgot_password, name="forgot_password"),
    path("redefinir-senha/", views.reset_password, name="reset_password"),
    path(
        "autenticacao/redefinir-senha/",
        views.reset_password,
        name="reset_password_auth",
    ),
    path("", views.dashboard, name="dashboard"),
    path("administrativo/prazos-recurso/", views.prazos_recurso_convenio, name="prazos_recurso_convenio"),
    path(
        "administrativo/empresas-emissoras/",
        views.empresas_emissoras,
        name="empresas_emissoras",
    ),
    path(
        "administrativo/acessos/",
        views.user_access_management,
        name="user_access_management",
    ),
    path("follow-up-glosas/", views.follow_up_glosas, name="follow_up_glosas"),
    path(
        "follow-up-glosas/recurso-pdf/",
        views.follow_up_glosas_recurso_pdf,
        name="follow_up_glosas_recurso_pdf",
    ),
    path(
        "associacoes-remessas-ipm/",
        views.associacoes_remessas_ipm,
        name="associacoes_remessas_ipm",
    ),
    path("conta-atendimento/", views.conta_atendimento, name="conta_atendimento"),
    path(
        "conta-atendimento/recurso-pdf/",
        views.conta_atendimento_recurso_pdf,
        name="conta_atendimento_recurso_pdf",
    ),
    path("acompanhamento/", views.acompanhamento, name="acompanhamento"),
    path(
        "requisicao/solicitacao-nota/",
        views.solicitacao_nota,
        name="solicitacao_nota",
    ),
    path(
        "requisicao/solicitacao-nota/atendimentos/"
        "<int:codigo_atendimento>/",
        views.consultar_atendimento_nota,
        name="consultar_atendimento_nota",
    ),
    path(
        "requisicao/solicitacoes-cadastradas/",
        views.solicitacoes_nota,
        name="solicitacoes_nota",
    ),
    path(
        "requisicao/workflow-solicitacoes/",
        views.workflow_solicitacoes,
        name="workflow_solicitacoes",
    ),
    path(
        "requisicao/solicitacoes-recusas/",
        views.solicitacoes_recusas,
        name="solicitacoes_recusas",
    ),
    path(
        "requisicao/emissao-nfse/",
        views.emissao_nfse,
        name="emissao_nfse",
    ),
    path(
        "requisicao/acompanhamento-particular/",
        views.acompanhamento_particular,
        name="acompanhamento_particular",
    ),
    path(
        "requisicao/emissao-nfse/itens/<int:emissao_id>/pdf/",
        views.emissao_nfse_pdf,
        name="emissao_nfse_pdf",
    ),
    path(
        "requisicao/nfse-externas/<str:row_hash>/pdf/",
        views.nfse_externa_pdf,
        name="nfse_externa_pdf",
    ),
    path(
        "requisicao/cadastrar-nota/",
        views.solicitacao_nota,
        name="cadastrar_nota",
    ),
    path(
        "requisicao/cadastrar-nota/atendimentos/"
        "<int:codigo_atendimento>/",
        views.consultar_atendimento_nota,
        name="consultar_atendimento_nota_legacy",
    ),
    path("glosas/", views.glosas, name="glosas"),
    path("remessas/", views.remessas, name="remessas"),
    path("recursos/", views.recursos, name="recursos"),
    path("recebimentos/", views.recebimentos, name="recebimentos"),
    path(
        "financeiro/conciliacao-fiscal-faturamento/",
        views.conciliacao_faturamento,
        name="conciliacao_faturamento",
    ),
    path(
        "financeiro/conciliacoes-sem-recebimento/",
        views.conciliacoes_sem_recebimento,
        name="conciliacoes_sem_recebimento",
    ),
    path(
        "financeiro/conciliacoes/",
        views.conciliacoes_financeiras,
        name="conciliacoes_financeiras",
    ),
    path(
        "financeiro/contas-a-pagar/operacao/",
        views.contas_pagar_operacao,
        name="contas_pagar_operacao",
    ),
    path(
        "financeiro/contas-a-pagar/acompanhamento/",
        views.contas_pagar_acompanhamento,
        name="contas_pagar_acompanhamento",
    ),
    path(
        "financeiro/contas-a-pagar/gestao/",
        views.contas_pagar_gestao,
        name="contas_pagar_gestao",
    ),
    path(
        "financeiro/contas-a-pagar/nfse-do-setor/",
        views.contas_pagar_nfse_setor,
        name="contas_pagar_nfse_setor",
    ),
    path("mv/login/", views.mv_login_iniciar, name="mv_login_iniciar"),
    path("mv/login/retorno/", views.mv_login_retorno, name="mv_login_retorno"),
    path(
        "financeiro/contas-a-pagar/conferencia/",
        views.contas_pagar_conferencia,
        name="contas_pagar_conferencia",
    ),
    path(
        "financeiro/contas-a-pagar/extratos/",
        views.contas_pagar_extratos,
        name="contas_pagar_extratos",
    ),
    path(
        "financeiro/contas-a-pagar/extratos/<int:extrato_id>/arquivo/",
        views.contas_pagar_extrato_arquivo,
        name="contas_pagar_extrato_arquivo",
    ),
    path(
        "financeiro/contas-a-pagar/nfse/<str:cnpj>/<str:numero>/",
        views.contas_pagar_nfse_ver,
        name="contas_pagar_nfse_ver",
    ),
    path(
        "financeiro/contas-a-pagar/nfse/<str:cnpj>/<str:numero>/pdf/",
        views.contas_pagar_nfse_pdf,
        name="contas_pagar_nfse_pdf",
    ),
    path(
        "financeiro/contas-a-pagar/nfse/<str:cnpj>/<str:numero>/xml/",
        views.contas_pagar_nfse_xml,
        name="contas_pagar_nfse_xml",
    ),
    path(
        "financeiro/contas-a-pagar/boletos/<int:boleto_id>/pdf/",
        views.contas_pagar_boleto_pdf,
        name="contas_pagar_boleto_pdf",
    ),
    path(
        "financeiro/contas-a-pagar/setores-dos-fornecedores/",
        views.contas_pagar_nfse_regras,
        name="contas_pagar_nfse_regras",
    ),
    path(
        "financeiro/contas-a-receber/fluxo/",
        contas_receber.fluxo,
        name="contas_receber_fluxo",
    ),
    path(
        "administrativo/fornecedores-criticos/",
        views.fornecedores_criticos,
        name="fornecedores_criticos",
    ),
    path(
        "financeiro/conciliacao-fiscal-faturamento/remessas/<str:nfse_row_hash>/",
        views.conciliacao_faturamento_remessas,
        name="conciliacao_faturamento_remessas",
    ),
    path(
        "financeiro/conciliacao-fiscal-faturamento/"
        "remessas/<int:cd_remessa>/notas/",
        views.conciliacao_faturamento_notas,
        name="conciliacao_faturamento_notas",
    ),
    path(
        "financeiro/conciliacao-fiscal-faturamento/lancamentos-extrato/",
        views.conciliacao_faturamento_lancamentos,
        name="conciliacao_faturamento_lancamentos",
    ),
    path("conciliacao/", views.conciliacao, name="conciliacao"),
]
