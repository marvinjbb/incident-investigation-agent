# Incident Investigation Agent

A production-style portfolio system for evidence-based investigation of application and PostgreSQL incidents. The finished system will gather logs, run restricted database diagnostics, inspect deployments, consult runbooks, construct a timeline, and produce a supported root-cause assessment. Any future corrective action will require explicit human approval.

Phase 1 establishes the FastAPI application and PostgreSQL incident-lab environment. It does **not** contain an AI agent, LLM integration, automated investigation, or remediation.

## Planned incident scenarios

- Blocked PostgreSQL queries
- Database connection exhaustion
- A failing application deployment

## Current architecture

```text
Client
  -> FastAPI
       -> GET /health       (process liveness)
       -> GET /health/db    (short PostgreSQL connectivity probe)
             -> PostgreSQL incident lab
```

Docker Compose runs the API and PostgreSQL on one private Compose network. The API uses the service name `db`, never container-local `localhost`, to reach PostgreSQL. Compose waits for PostgreSQL's health check before starting the API, while `/health/db` safely reports later connectivity failures as HTTP 503.

## Configuration

Copy `.env.example` to `.env` for local overrides. The example contains development-only defaults, not production credentials. `.env` is ignored by Git.

| Variable | Purpose | Development default |
| --- | --- | --- |
| `POSTGRES_HOST` | PostgreSQL hostname | `localhost` outside Compose; Compose supplies `db` |
| `POSTGRES_PORT` | PostgreSQL port | `5432` |
| `POSTGRES_DB` | Incident-lab database | `incident_lab` |
| `POSTGRES_USER` | Incident-lab user | `incident_app` |
| `POSTGRES_PASSWORD` | Local development password | `incident_lab_dev` |
| `DATABASE_CONNECT_TIMEOUT_SECONDS` | Health-probe connection timeout | `3` |
| `API_PORT` | Optional host-side Compose port | `8000` |

Use real secret management and a strong runtime password outside this local lab.

## Run with Docker Compose

```bash
docker compose up --build
```

Then verify:

```bash
curl http://localhost:8000/health
curl http://localhost:8000/health/db
```

Stop the services without deleting database data:

```bash
docker compose down
```

## Run the API directly

Python 3.12 and a reachable PostgreSQL instance are required.

```bash
python -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
uvicorn app.main:app --reload
```

## Run tests and static checks

```bash
pytest
ruff check .
ruff format --check .
```

The automated database-health tests replace the connectivity probe. They are deterministic and do not require Docker or an external database.

## Safety boundary

This project will remain evidence-first and human-controlled. Diagnostic tools added in later phases will be allow-listed and read-only. The system will not take corrective action without explicit human approval.
