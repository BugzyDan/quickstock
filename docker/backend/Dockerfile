# QuickStock JA - backend image
#
# Build context is the repository root. The canonical Django project lives in
# Website/BackEnd/quickstock (see render.yaml rootDir); there is no second copy
# of the application in this repository.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DJANGO_SETTINGS_MODULE=quickstock.settings

# mysqlclient needs a compiler and headers; psycopg2-binary is a wheel.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        default-libmysqlclient-dev \
        pkg-config \
        libpq5 \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY Website/BackEnd/quickstock/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY Website/BackEnd/quickstock/ ./
COPY docker/backend/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Collect static files at build time so the image is immutable at runtime.
# Placeholder values keep settings importable without production secrets.
RUN DJANGO_SECRET_KEY=build-only-placeholder-value-not-used-at-runtime \
    DJANGO_DEBUG=False \
    DJANGO_ALLOWED_HOSTS=localhost \
    DJANGO_CSRF_TRUSTED_ORIGINS=https://localhost \
    python manage.py collectstatic --noinput

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/media /app/staticfiles \
    && chown -R appuser:appuser /app/media /app/staticfiles

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/healthz/ || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["gunicorn", "quickstock.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "3", \
     "--threads", "2", \
     "--timeout", "60", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
