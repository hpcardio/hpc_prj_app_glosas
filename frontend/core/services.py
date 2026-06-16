import requests
from django.conf import settings


class ApiError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def api_headers() -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if settings.API_BEARER_TOKEN:
        headers["Authorization"] = f"Bearer {settings.API_BEARER_TOKEN}"
    return headers


def api_request(method: str, path: str, **kwargs):
    try:
        response = requests.request(
            method,
            f"{settings.API_BASE_URL}{path}",
            headers=api_headers(),
            timeout=settings.API_TIMEOUT,
            **kwargs,
        )
    except requests.Timeout as exc:
        raise ApiError(
            f"tempo limite excedido ao consultar a API em {settings.API_BASE_URL}{path}. "
            f"Tente novamente ou aumente API_TIMEOUT no .env."
        ) from exc
    except requests.ConnectionError as exc:
        raise ApiError(f"nao foi possivel conectar na API em {settings.API_BASE_URL}.") from exc
    except requests.RequestException as exc:
        raise ApiError(f"falha ao consultar a API: {exc}") from exc

    if response.status_code >= 400:
        raise ApiError(response.text, response.status_code)
    return response


def api_get(path: str, params: dict | None = None):
    response = api_request(
        "GET",
        path,
        params={k: v for k, v in (params or {}).items() if v},
    )
    try:
        return response.json()
    except ValueError as exc:
        raise ApiError("API retornou uma resposta invalida para JSON.") from exc


def api_post(path: str, data: dict):
    response = api_request("POST", path, json=data)
    try:
        return response.json()
    except ValueError as exc:
        raise ApiError("API retornou uma resposta invalida para JSON.") from exc


def api_put(path: str, data: dict):
    response = api_request("PUT", path, json=data)
    try:
        return response.json()
    except ValueError as exc:
        raise ApiError("API retornou uma resposta invalida para JSON.") from exc


def api_patch(path: str, data: dict):
    response = api_request("PATCH", path, json=data)
    try:
        return response.json()
    except ValueError as exc:
        raise ApiError("API retornou uma resposta invalida para JSON.") from exc


def api_delete(path: str):
    api_request("DELETE", path)
