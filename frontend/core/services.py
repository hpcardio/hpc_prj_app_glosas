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


def api_get(path: str, params: dict | None = None):
    response = requests.get(
        f"{settings.API_BASE_URL}{path}",
        params={k: v for k, v in (params or {}).items() if v},
        headers=api_headers(),
        timeout=10,
    )
    if response.status_code >= 400:
        raise ApiError(response.text, response.status_code)
    return response.json()


def api_post(path: str, data: dict):
    response = requests.post(f"{settings.API_BASE_URL}{path}", json=data, headers=api_headers(), timeout=10)
    if response.status_code >= 400:
        raise ApiError(response.text, response.status_code)
    return response.json()


def api_patch(path: str, data: dict):
    response = requests.patch(f"{settings.API_BASE_URL}{path}", json=data, headers=api_headers(), timeout=10)
    if response.status_code >= 400:
        raise ApiError(response.text, response.status_code)
    return response.json()


def api_delete(path: str):
    response = requests.delete(f"{settings.API_BASE_URL}{path}", headers=api_headers(), timeout=10)
    if response.status_code >= 400:
        raise ApiError(response.text, response.status_code)

