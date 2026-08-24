from django.urls import reverse


SCREEN_GROUPS = (
    {
        "label": "Indicadores",
        "screens": (
            {
                "key": "indicadores",
                "label": "Indicadores",
                "route_name": "dashboard",
            },
        ),
    },
    {
        "label": "Núcleo Gestor de Glosas",
        "screens": (
            {
                "key": "follow_up_glosas",
                "label": "Follow-Up de Glosas",
                "route_name": "follow_up_glosas",
            },
            {
                "key": "recursos_processos",
                "label": "Recursos",
                "route_name": "recursos",
            },
            {
                "key": "triagem",
                "label": "Triagem",
                "route_name": "conta_atendimento",
            },
            {
                "key": "acompanhamento",
                "label": "Acompanhamento",
                "route_name": "acompanhamento",
            },
        ),
    },
    {
        "label": "Financeiro",
        "screens": (
            {
                "key": "conciliacao_manual",
                "label": "Conciliação Manual",
                "route_name": "conciliacao_faturamento",
            },
            {
                "key": "conciliacao_financeira",
                "label": "Conciliação Financeira",
                "route_name": "conciliacoes_sem_recebimento",
            },
            {
                "key": "consultar_conciliacoes",
                "label": "Consultar conciliações",
                "route_name": "conciliacoes_financeiras",
            },
            {
                "key": "follow_up_solicitacoes",
                "label": "Follow-Up Solicitações",
                "route_name": "workflow_solicitacoes",
            },
            {
                "key": "emissao_nfse",
                "label": "Emissão NFS-e",
                "route_name": "emissao_nfse",
            },
            {
                "key": "acompanhamento_particular",
                "label": "Acompanhamento Particular",
                "route_name": "acompanhamento_particular",
            },
        ),
    },
    {
        "label": "Solicitação",
        "screens": (
            {
                "key": "solicitar_nota",
                "label": "Solicitar Nota",
                "route_name": "solicitacao_nota",
            },
            {
                "key": "solicitacoes_cadastradas",
                "label": "Solicitações cadastradas",
                "route_name": "solicitacoes_nota",
            },
            {
                "key": "solicitacoes_recusas",
                "label": "Solicitações Recusas",
                "route_name": "solicitacoes_recusas",
            },
        ),
    },
    {
        "label": "Administrativo",
        "screens": (
            {
                "key": "configuracao_convenio",
                "label": "Configuração por Convênio",
                "route_name": "prazos_recurso_convenio",
            },
            {
                "key": "empresas_nfse",
                "label": "Empresas (Emissão NFS-e)",
                "route_name": "empresas_emissoras",
            },
        ),
    },
)

SCREEN_KEYS = tuple(
    screen["key"]
    for group in SCREEN_GROUPS
    for screen in group["screens"]
)

ROUTE_PERMISSIONS = {
    "dashboard": "indicadores",
    "follow_up_glosas": "follow_up_glosas",
    "follow_up_glosas_recurso_pdf": "follow_up_glosas",
    "associacoes_remessas_ipm": "follow_up_glosas",
    "glosas": "follow_up_glosas",
    "conta_atendimento": "triagem",
    "acompanhamento": "acompanhamento",
    "recursos": ("recursos_processos", "follow_up_glosas"),
    "conciliacao_faturamento": "conciliacao_manual",
    "conciliacao_faturamento_remessas": "conciliacao_manual",
    "conciliacao_faturamento_notas": "conciliacao_manual",
    "conciliacao_faturamento_lancamentos": "conciliacao_manual",
    "conciliacao": "conciliacao_manual",
    "remessas": "conciliacao_manual",
    "conciliacoes_sem_recebimento": "conciliacao_financeira",
    "recebimentos": "conciliacao_financeira",
    "conciliacoes_financeiras": "consultar_conciliacoes",
    "workflow_solicitacoes": "follow_up_solicitacoes",
    "emissao_nfse": "emissao_nfse",
    "emissao_nfse_pdf": (
        "emissao_nfse",
        "acompanhamento_particular",
        "solicitacoes_cadastradas",
        "solicitar_nota",
        "follow_up_solicitacoes",
    ),
    "acompanhamento_particular": "acompanhamento_particular",
    "solicitacao_nota": "solicitar_nota",
    "cadastrar_nota": "solicitar_nota",
    "consultar_atendimento_nota": "solicitar_nota",
    "consultar_atendimento_nota_legacy": "solicitar_nota",
    "solicitacoes_nota": "solicitacoes_cadastradas",
    "solicitacoes_recusas": "solicitacoes_recusas",
    "prazos_recurso_convenio": "configuracao_convenio",
    "empresas_emissoras": "empresas_nfse",
}


def is_ti(user):
    return user.get("perfil") == "ti"


def allowed_screen_keys(user):
    if is_ti(user):
        return set(SCREEN_KEYS)
    permissions = user.get("telas_permitidas")
    if permissions is None:
        return set(SCREEN_KEYS)
    return set(permissions).intersection(SCREEN_KEYS)


def can_access_screen(user, screen_key):
    return screen_key in allowed_screen_keys(user)


def can_access_route(user, route_name):
    required_screens = ROUTE_PERMISSIONS.get(route_name)
    if not required_screens:
        return True
    if isinstance(required_screens, str):
        required_screens = (required_screens,)
    return any(
        can_access_screen(user, screen_key)
        for screen_key in required_screens
    )


def first_allowed_url(user):
    allowed = allowed_screen_keys(user)
    for group in SCREEN_GROUPS:
        for screen in group["screens"]:
            if screen["key"] in allowed:
                return reverse(screen["route_name"])
    return reverse("access_denied")


def build_screen_groups(selected_keys=None, full_access=False):
    selected = (
        set(SCREEN_KEYS)
        if full_access or selected_keys is None
        else set(selected_keys)
    )
    return [
        {
            "label": group["label"],
            "screens": [
                {
                    **screen,
                    "checked": screen["key"] in selected,
                }
                for screen in group["screens"]
            ],
        }
        for group in SCREEN_GROUPS
    ]


def screen_access_context(request):
    user = request.session.get("api_user") or {}
    allowed = allowed_screen_keys(user)
    return {
        "screen_access": {
            screen_key: screen_key in allowed
            for screen_key in SCREEN_KEYS
        },
        "is_ti_user": is_ti(user),
    }
