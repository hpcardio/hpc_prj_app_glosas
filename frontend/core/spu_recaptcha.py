from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import requests
from django.conf import settings


STATUS_ASSET = "spu-recaptcha-status.json"


class SpuNovncUnavailable(RuntimeError):
    """Raised when the internal SPU desktop cannot be reached."""


def get_spu_recaptcha_status() -> dict[str, Any]:
    try:
        response = requests.get(
            f"{settings.SPU_NOVNC_INTERNAL_URL}/{STATUS_ASSET}",
            timeout=settings.SPU_NOVNC_TIMEOUT,
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise SpuNovncUnavailable(
            "O desktop do SPU não está disponível."
        ) from exc

    if not isinstance(payload, dict):
        raise SpuNovncUnavailable(
            "O desktop do SPU retornou um estado inválido."
        )

    active = payload.get("active") is True
    expires_at = _parse_datetime(payload.get("expires_at"))
    if expires_at is not None and expires_at <= datetime.now(timezone.utc):
        active = False

    return {
        "active": active,
        "challenge_id": str(payload.get("challenge_id") or ""),
        "dag_id": str(payload.get("dag_id") or ""),
        "task_id": str(payload.get("task_id") or ""),
        "started_at": payload.get("started_at"),
        "expires_at": payload.get("expires_at"),
    }


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)

