FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

COPY pyproject.toml ./
# Install runtime dependencies first for layer caching.
RUN python -c "import tomllib;print('\n'.join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))" > /tmp/req.txt \
 && pip install -r /tmp/req.txt

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
COPY fixtures ./fixtures
RUN useradd --create-home --uid 10001 appuser && chown -R appuser /app
USER appuser

ENV PORT=8000 APP_ENV=production
EXPOSE 8000
# Single worker: voice sessions are stateful in-process (see docs/DEPLOYMENT.md).
CMD ["sh", "-c", "exec uvicorn app.main:_app_factory --factory --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*' --timeout-graceful-shutdown 20 --ws-max-size 1048576"]
