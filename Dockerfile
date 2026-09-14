FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml ./
COPY app ./app
COPY db ./db
COPY migrations ./migrations
COPY alembic.ini ./
COPY runbooks ./runbooks

RUN pip install --no-cache-dir . \
    && groupadd --system incident-agent \
    && useradd --system --gid incident-agent --home-dir /app incident-agent \
    && mkdir -p /app/logs \
    && chown -R incident-agent:incident-agent /app

USER incident-agent

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; request=urllib.request.Request('http://127.0.0.1:8000/health/live', headers={'Host':'api.marvinjb.dev'}); urllib.request.urlopen(request, timeout=3)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
