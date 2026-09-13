# Incident Investigation Agent

A production-style portfolio system for evidence-based investigation of application and PostgreSQL incidents. The finished system will gather logs, run restricted database diagnostics, inspect deployments, consult runbooks, construct a timeline, and produce a supported root-cause assessment. Any future corrective action will require explicit human approval.

Phase 2 provides a controlled incident simulation lab. It creates genuine technical evidence for three bounded scenarios, but it does **not** yet contain an AI investigator, LLM integration, automated root-cause analysis, or remediation.

## Phase 2 architecture

```text
Demo client
   |
   v
Allowlisted FastAPI incident routes
   |
   +--> IncidentLab coordinator ----> machine-readable timeline events
   |          |
   |          +--> PostgreSQL row lock + blocked session
   |          +--> bounded application pool saturation
   |          +--> v2-bad deployment + incompatible query
   |
   +--> PostgreSQL incident/deployment metadata
   +--> structured JSON application logs
   +--> bounded application workload pool
```

Docker Compose runs the API and PostgreSQL on a private network. The API uses the Compose service name `db`, not container-local `localhost`. PostgreSQL health gates API startup, and the application applies the small idempotent schema at startup so existing development volumes receive schema updates safely.

## Incident scenarios

### Blocked PostgreSQL query

One transaction updates the single allowlisted row in `demo_lock_target` and holds its row lock. A second named PostgreSQL session attempts the same update and blocks. The lab confirms the blocking relationship through `pg_blocking_pids()` before recording `query_blocked`. Recovery rolls both transactions back, so the demo row is never permanently changed.

### Connection pool exhaustion

The workload pool has a fixed default maximum of three connections. The scenario checks out exactly those connections and holds them until recovery or timeout. `/demo/workload` then fails through a genuine `PoolTimeout`, while incident control metadata remains available through separate bounded connections. Recovery returns every connection to the pool.

### Failing application deployment

The lab records and activates `v2-bad`. While active, `/demo/workload` executes an intentionally incompatible, fixed query against `demo_workload`, producing PostgreSQL `UndefinedColumn` evidence. Recovery records and reactivates healthy `v1`. No SQL or version value comes from the caller.

## Safety boundaries

- Three explicit POST routes; no generic scenario or command endpoint
- No arbitrary SQL, shell commands, file paths, database names, or release names
- One active incident per API process
- Durations from 3 to 15 seconds; default 8 seconds
- Application pool fixed to a small configured maximum
- Incident and deployment history capped at 100 records each by default
- Docker JSON logs rotated at three 10 MB files per service
- Automatic cleanup plus an explicit recovery endpoint
- Transaction rollback for the blocked-query scenario
- Fixed healthy release restoration for the bad-deployment scenario
- Generic client errors with no credentials or stack traces
- Startup recovery marks interrupted incidents resolved and restores `v1`

This is a single-process portfolio lab. Multi-process coordination, authentication, and public rate limiting belong to a later deployment-hardening phase.

## Evidence sources

Incident and deployment metadata are stored in PostgreSQL. `incident_events` records non-AI source events including:

- `incident_started`
- `lock_acquired`
- `query_blocked`
- `pool_saturated`
- `request_failed`
- `deployment_started`
- `deployment_activated`
- `recovery_requested`
- `incident_recovered`

Application logs are JSON on stdout and are available through:

```bash
docker compose logs api
```

They include event type and bounded context such as incident ID, scenario, request path, deployment version, and exception type. Passwords, connection strings, tokens, prompts, and stack traces are not logged.

## API

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Process liveness |
| `GET` | `/health/db` | PostgreSQL connectivity |
| `POST` | `/demo/incidents/blocked-query` | Start the row-lock scenario |
| `POST` | `/demo/incidents/connection-exhaustion` | Saturate the workload pool |
| `POST` | `/demo/incidents/bad-deployment` | Activate `v2-bad` |
| `GET` | `/demo/incidents/{incident_id}` | Read incident state and timeline |
| `POST` | `/demo/incidents/{incident_id}/recover` | Request bounded recovery |
| `GET` | `/demo/workload` | Exercise the active release and pool |
| `GET` | `/demo/deployments` | Read deployment history |
| `GET` | `/demo/diagnostics/pool` | Read safe pool counts |

Incident start requests accept only an optional bounded duration:

```json
{"duration_seconds": 8}
```

## Configuration

Copy `.env.example` to `.env` for local overrides. The example contains development-only defaults, not production credentials. `.env` is ignored by Git.

| Variable | Purpose | Development default |
| --- | --- | --- |
| `POSTGRES_HOST` | PostgreSQL hostname | `localhost`; Compose supplies `db` |
| `POSTGRES_PORT` | PostgreSQL port | `5432` |
| `POSTGRES_DB` | Incident-lab database | `incident_lab` |
| `POSTGRES_USER` | Incident-lab user | `incident_app` |
| `POSTGRES_PASSWORD` | Local development password | `incident_lab_dev` |
| `DATABASE_CONNECT_TIMEOUT_SECONDS` | Connection timeout | `3` |
| `DATABASE_POOL_SIZE` | Workload pool maximum | `3` |
| `DATABASE_POOL_TIMEOUT_SECONDS` | Pool acquisition timeout | `1` |
| `INCIDENT_DEFAULT_DURATION_SECONDS` | Default incident duration | `8` |
| `INCIDENT_MAX_DURATION_SECONDS` | Hard duration ceiling | `15` |
| `INCIDENT_HISTORY_LIMIT` | Retained incident records | `100` |
| `DEPLOYMENT_HISTORY_LIMIT` | Retained deployment records | `100` |
| `API_PORT` | Optional host-side Compose port | `8000` |

Use real secret management and a strong runtime password outside this local lab.

## Run locally

```bash
docker compose up --build
```

Verify health and the normal workload:

```bash
curl http://localhost:8000/health
curl http://localhost:8000/health/db
curl http://localhost:8000/demo/workload
```

Trigger and observe a blocked-query incident:

```bash
curl -X POST http://localhost:8000/demo/incidents/blocked-query \
  -H "Content-Type: application/json" \
  -d '{"duration_seconds":8}'

curl http://localhost:8000/demo/incidents/INCIDENT_ID
curl -X POST http://localhost:8000/demo/incidents/INCIDENT_ID/recover
```

Use the other fixed start routes for `connection-exhaustion` and `bad-deployment`. During those incidents, call `/demo/workload`; it returns a bounded HTTP 503. After automatic or manual recovery, it returns healthy `v1` again.

Observe PostgreSQL blocking directly:

```bash
docker compose exec db psql -U incident_app -d incident_lab -c \
  "SELECT pid, application_name, pg_blocking_pids(pid) FROM pg_stat_activity WHERE cardinality(pg_blocking_pids(pid)) > 0;"
```

Stop services while preserving data:

```bash
docker compose down
```

## Direct Python development

```bash
python -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
uvicorn app.main:app --reload
```

## Tests and static checks

```bash
pytest
ruff check .
ruff format --check .
```

The automated suite uses dependency replacement and in-memory fakes; it does not require Docker or arbitrary sleep timing. Real PostgreSQL behavior is verified separately through the Compose lab.

## Current limitations

- Incident coordination is process-local and intended for one Uvicorn worker.
- The lab has no authentication or public rate limiting yet.
- Logs use Docker stdout rather than external aggregation.
- Schema changes use one idempotent SQL file; a migration framework is not justified yet.
- No AI investigator or remediation capability exists in Phase 2.
