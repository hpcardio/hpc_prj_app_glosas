import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BASE_DIR.parent


def load_dotenv_file(path):
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def env_bool(name, default=False):
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


load_dotenv_file(PROJECT_DIR / ".env")
load_dotenv_file(BASE_DIR / ".env")

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-only-change-me")
DEBUG = os.getenv("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "core.middleware.ApiSessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "glosas_frontend.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": [
            "django.template.context_processors.debug",
            "django.template.context_processors.request",
            "django.contrib.auth.context_processors.auth",
            "django.contrib.messages.context_processors.messages",
        ]},
    }
]
WSGI_APPLICATION = "glosas_frontend.wsgi.application"

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "frontend.sqlite3"}}

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")
API_CONTA_ATENDIMENTO_PATH = os.getenv("API_CONTA_ATENDIMENTO_PATH", "/app_glosas/")
API_REGISTRO_GLOSA_PATH = os.getenv("API_REGISTRO_GLOSA_PATH", "/app_glosas/glosas")
API_TISS_PATH = os.getenv("API_TISS_PATH", "/app_glosas/tiss")
API_TIMEOUT = float(os.getenv("API_TIMEOUT", "60"))
DASHBOARD_CACHE_SECONDS = int(os.getenv("DASHBOARD_CACHE_SECONDS", "45"))
APP_FILTER_CACHE_SECONDS = int(os.getenv("APP_FILTER_CACHE_SECONDS", str(DASHBOARD_CACHE_SECONDS)))

EMAIL_HOST = os.getenv("SMTP_HOST", "")
EMAIL_PORT = int(os.getenv("SMTP_PORT", "587"))
EMAIL_HOST_USER = os.getenv("SMTP_USER") or os.getenv("SMTP_USERNAME", "")
EMAIL_HOST_PASSWORD = os.getenv("SMTP_PASSWORD", "")
DEFAULT_FROM_EMAIL = os.getenv(
    "SMTP_FROM",
    os.getenv("SMTP_FROM_EMAIL", EMAIL_HOST_USER or "webmaster@localhost"),
)
EMAIL_USE_SSL = env_bool("SMTP_USE_SSL", EMAIL_PORT == 465)
EMAIL_USE_TLS = False if EMAIL_USE_SSL else env_bool("SMTP_USE_TLS", True)

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "0") == "1"
SESSION_COOKIE_AGE = int(os.getenv("SESSION_COOKIE_AGE", str(8 * 60 * 60)))

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "glosas-frontend-cache",
    }
}
