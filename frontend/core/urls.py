from django.urls import path

from . import views

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("acesso-negado/", views.access_denied, name="access_denied"),
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
