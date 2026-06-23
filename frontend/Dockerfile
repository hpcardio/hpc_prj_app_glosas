FROM python:3.12-slim-bookworm

ENV DJANGO_SETTINGS_MODULE=glosas_frontend.settings \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN addgroup --system django \
    && adduser --system --ingroup django django \
    && mkdir -p /app /data

COPY requirements.txt .
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

COPY . .

RUN chmod +x /app/docker-entrypoint.sh \
    && DJANGO_DEBUG=0 \
       DJANGO_SECRET_KEY=build-time-only \
       DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1 \
       python manage.py collectstatic --noinput \
    && chown -R django:django /app /data

USER django

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/login/' % os.getenv('PORT', '8000'), timeout=5).read()"

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["gunicorn"]

