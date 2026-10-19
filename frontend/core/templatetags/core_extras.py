from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django import template


register = template.Library()


@register.simple_tag(takes_context=True)
def querystring(context, **kwargs):
    query = context["request"].GET.copy()
    for key, value in kwargs.items():
        if value in (None, ""):
            query.pop(key, None)
        else:
            query[key] = value
    return query.urlencode()


@register.filter
def api_date(value):
    if not value:
        return "-"
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y")
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")

    text = str(value).strip()
    if not text:
        return "-"

    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).strftime("%d/%m/%Y")
    except ValueError:
        pass

    if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-":
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
        except ValueError:
            return text[:10]

    return text


@register.filter
def api_datetime(value):
    if not value:
        return "-"
    if isinstance(value, datetime):
        parsed = value
    else:
        normalized = str(value).strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return str(value)
    return parsed.strftime("%d/%m/%Y %H:%M")


@register.filter
def br_currency(value):
    if value in (None, ""):
        value = 0

    text = str(value).strip()
    if "," in text:
        text = text.replace(".", "").replace(",", ".")

    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        return value

    formatted = f"{number:,.2f}"
    return formatted.replace(",", "_").replace(".", ",").replace("_", ".")


@register.filter
def motivo_com_descricao(value, description):
    """Display a TISS code and description without changing its stored code."""
    code = str(value or "").strip()
    if not code or " - " in code:
        return code
    if isinstance(description, (list, tuple)):
        description = next((row.get("termo", "") for row in description
                            if str(row.get("codigo_termo", "")).strip() == code), "")
    description = str(description or "").strip()
    return f"{code} - {description}" if description else code
