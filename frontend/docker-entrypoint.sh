#!/bin/sh
set -eu

if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
    python manage.py migrate --noinput
fi

if [ "$#" -eq 0 ] || [ "$1" = "gunicorn" ]; then
    set -- gunicorn glosas_frontend.asgi:application \
        --bind "0.0.0.0:${PORT:-8000}" \
        --worker-class uvicorn.workers.UvicornWorker \
        --workers "${GUNICORN_WORKERS:-3}" \
        --timeout "${GUNICORN_TIMEOUT:-120}" \
        --access-logfile - \
        --error-logfile -
fi

exec "$@"
