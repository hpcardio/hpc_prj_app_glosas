"""Current authorization shared by the HTTP and WebSocket desktop proxies."""
from functools import wraps
from django.http import HttpResponse
from .services import ApiError, api_get

def profile_can_use_spu(user):
    """Qualquer usuário ativo pode resolver o captcha das automações."""
    return isinstance(user, dict) and user.get('ativo') is True


def token_can_use_spu(token):
    if not token:
        return False
    try:
        return profile_can_use_spu(api_get('/usuarios/me', token=token, timeout=5))
    except ApiError:
        return False


def require_spu_permission(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not token_can_use_spu(request.session.get('api_access_token')):
            response = HttpResponse('Acesso ao desktop SPU não autorizado.', status=403)
            response['Cache-Control'] = 'no-store, private'
            return response
        return view(request, *args, **kwargs)
    return wrapped
