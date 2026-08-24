from contextvars import ContextVar

import requests
from django.conf import settings


_request_api_token = ContextVar("request_api_token", default=None)


class ApiError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def set_request_api_token(token: str | None):
    return _request_api_token.set(token)


def reset_request_api_token(context_token):
    _request_api_token.reset(context_token)


def api_headers(token: str | None = None) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    access_token = token or _request_api_token.get()
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    return headers


def api_request(
    method: str,
    path: str,
    token: str | None = None,
    timeout: int | float | None = None,
    **kwargs,
):
    try:
        response = requests.request(
            method,
            f"{settings.API_BASE_URL}{path}",
            headers=api_headers(token),
            timeout=timeout or settings.API_TIMEOUT,
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
        message = response.text
        response.close()
        raise ApiError(message, response.status_code)
    return response


def api_get(
    path: str,
    params: dict | None = None,
    token: str | None = None,
    timeout: int | float | None = None,
):
    response = api_request(
        "GET",
        path,
        token=token,
        timeout=timeout,
        params={k: v for k, v in (params or {}).items() if v},
    )
    try:
        return response.json()
    except ValueError as exc:
        raise ApiError("API retornou uma resposta invalida para JSON.") from exc


def api_get_stream(
    path: str,
    params: dict | None = None,
    token: str | None = None,
):
    return api_request(
        "GET",
        path,
        token=token,
        params={k: v for k, v in (params or {}).items() if v is not None},
        stream=True,
    )


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


def api_authenticate(email: str, password: str):
    response = api_request(
        "POST",
        "/autenticacao/token",
        data={"username": email, "password": password},
    )
    try:
        return response.json()
    except ValueError as exc:
        raise ApiError("API retornou uma resposta invalida para JSON.") from exc
