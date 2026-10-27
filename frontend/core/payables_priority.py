"""Presentation helpers for accounts payable, without database access."""

from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from django.urls import reverse
from django.utils import timezone

LEVELS = [
    (
        5,
        "Extrema",
        "Risco imediato à assistência, segurança, regularidade legal ou receita: folha, férias, obrigações trabalhistas, PJs, INSS/FGTS, clínicas parceiras da rede, materiais e medicamentos insubstituíveis, empréstimos e mútuos.",
    ),
    (
        4,
        "Alta",
        "Risco relevante de interrupção, com negociação ou contingência temporária: materiais e OPME estratégicos, médicos essenciais, laboratório, diagnósticos, manutenção corretiva e acordos relevantes.",
    ),
    (
        3,
        "Moderada",
        "Atraso temporariamente administrável: serviços administrativos relevantes, apoio operacional, fornecedores substituíveis, manutenção eletiva e prestadores sem risco imediato de suspensão.",
    ),
    (
        2,
        "Baixa",
        "Impacto operacional limitado: serviços acessórios, materiais não essenciais, pequenas melhorias e despesas de baixa repercussão.",
    ),
    (
        1,
        "Não crítica",
        "Despesas postergáveis: fornecimento encerrado, contratos não essenciais finalizados e dívidas antigas sem ameaça concreta de cobrança judicial ou bloqueio operacional.",
    ),
]
AGES = [
    (5, ">120 dias"),
    (4, "91–120 dias"),
    (3, "61–90 dias"),
    (2, "31–60 dias"),
    (1, "1–30 dias"),
]


def priority_options():
    return [
        {"value": str(n), "nivel": n, "label": label, "criterio": criterion}
        for n, label, criterion in LEVELS
    ]


def extra_filters(query):
    return {
        key: value
        for key, value in (
            ("nivel_criticidade", query.get("nivel_criticidade", "todos")),
            ("faixa_atraso", query.get("faixa_atraso", "todos")),
            ("somente_vencidos", query.get("somente_vencidos", "false")),
        )
        if value not in ("todos", "false", "")
    }


def month_start(value):
    if not value:
        return timezone.localdate().replace(day=1).isoformat()
    parsed = date.fromisoformat(value + "-01" if len(value) == 7 else value)
    return parsed.replace(day=1).isoformat()


def decorate_suppliers(suppliers):
    labels = dict((n, label) for n, label, _ in LEVELS)
    ages = dict(AGES)
    for item in suppliers:
        level = item.get("nivel_criticidade")
        if item.get("sem_cadastro"):
            level = None
        elif level is None and not item.get("critico"):
            level = 1
        item["nivel_criticidade"] = level
        item["criticidade_label"] = (
            "Sem classificação" if item.get("sem_cadastro")
            else labels.get(level, "Crítico · revisar nível")
        )
        item["criticidade_pendente"] = level is None
        days = int(item.get("dias_atraso") or 0)
        age = next(
            (
                n
                for threshold, n in ((120, 5), (90, 4), (60, 3), (30, 2), (0, 1))
                if days > threshold
            ),
            0,
        )
        item["atraso_label"] = ages.get(age, "Sem atraso")
    return suppliers


def decorate_matrix(matrix, filters):
    for row in matrix.get("linhas", []):
        for cell in row["celulas"]:
            query = {
                **{key: value for key, value in filters.items() if key != "quadrante"},
                "nivel_criticidade": cell["nivel"],
                "faixa_atraso": cell["faixa"],
                "somente_vencidos": "true",
            }
            cell["url"] = reverse("contas_pagar_operacao") + "?" + urlencode(query)
    matrix["pending_url"] = (
        reverse("fornecedores_criticos") + "?nivel_criticidade=pendente"
    )
    return matrix


def trend_history(history):
    """Plot only recorded balances; rolling new-overdue values are not summed."""
    values = []
    for row in history:
        try:
            value = Decimal(str(row.get("valor_vencido") or 0))
            if not value.is_finite() or value < 0:
                continue
        except InvalidOperation:
            continue
        values.append((row, value))
    maximum = max((value for _, value in values), default=Decimal("0"))
    return [
        {**row, "largura": int(value / maximum * 100) if maximum else 0}
        for row, value in values
    ]
