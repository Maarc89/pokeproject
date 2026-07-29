# syntax=docker/dockerfile:1

# ---- build stage --------------------------------------------------------
# Dependencies are installed into a virtualenv that gets copied into the
# runtime image, so nothing needed only for building ships to production.
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# psycopg2-binary ships prebuilt wheels, so no compiler toolchain is needed --
# this used to pull in build-essential and libpq-dev (~200 MB).
COPY requirements.txt ./
RUN pip install -r requirements.txt


# ---- runtime stage ------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# Runs as an unprivileged user; a compromised app process should not be root.
RUN useradd --create-home --uid 10001 appuser

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --chown=appuser:appuser . .

# Static files are baked in at build time so containers start fast and every
# replica serves byte-identical assets. The placeholder settings satisfy the
# production guards in settings.py; they are not used at runtime.
RUN DJANGO_DEBUG=0 \
    DJANGO_SECRET_KEY=build-time-only-not-used-at-runtime \
    POSTGRES_PASSWORD=build-time-only \
    python manage.py collectstatic --noinput \
    && chown -R appuser:appuser /app/staticfiles

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz/', timeout=4).status == 200 else 1)"

# Migrations deliberately do NOT run here. With more than one replica every
# container would race to migrate on boot; docker-compose.yml runs them once
# in a dedicated service that web waits on.
CMD ["gunicorn", "pokeproject.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "3", \
     "--threads", "2", \
     "--timeout", "60", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
