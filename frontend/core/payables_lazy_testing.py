"""Test helper: fetch Contas a Pagar the way payables-lazy.js does in the browser."""
import re
from urllib.parse import parse_qsl, urlsplit


def lazy_get(client, url, params=None, details=False):
    """Entry page, then summary, matrix and (optionally) supplier details.

    Returns the summary response (with its context) carrying the combined HTML,
    so page-level assertions keep checking what the user ends up seeing.
    """
    parts = urlsplit(url)
    path = parts.path
    query = {**dict(parse_qsl(parts.query)), **(params or {})}
    entry = client.get(path, query)
    if entry.status_code != 200 or b"data-payables-summary" not in entry.content:
        return entry
    summary = client.get(path, {**query, "fragmento": "resumo"})
    if summary.status_code != 200:
        return summary
    html = [entry.content, summary.content]
    if b"data-payables-matrix" in summary.content:
        html.append(client.get(path, {**query, "fragmento": "matriz"}).content)
    if details:
        codes = re.findall(r'data-payables-detail="([^"]+)"', summary.content.decode())
        for code in dict.fromkeys(codes):
            html.append(client.get(path, {**query, "detalhes_fornecedor": code}).content)
    summary.content = b"".join(html)
    return summary
