# Incident Investigation Agent

A production-style portfolio system for evidence-based investigation of application and PostgreSQL incidents. The finished system will gather logs, run restricted database diagnostics, inspect deployments, consult runbooks, construct a timeline, and produce a supported root-cause assessment. Any future corrective action will require explicit human approval.

Phase 4 adds a bounded AI incident investigator. The model chooses among the existing restricted tools, reviews returned evidence, and produces a strictly validated, persisted report. Recommendations remain advisory: there is no remediation execution path.

## Phase 4 architecture

```text
Demo incident --> Application + PostgreSQL --> Technical evidence
                                                   |
                                                   v
                                      Restricted diagnostic tools
                                      |-- incident events
                                      |-- rotating application logs
                                      |-- PostgreSQL locks/connections
                                      |-- application pool state
                                      |-- deployment history
                                      `-- allowlisted runbooks
                                                   |
                                                   v
                                         Evidence collector
                                                   |
                                                   v
                                         Structured evidence API
                                                   |
                                                   v
                                      Bounded AI investigator
                                                   |
                                                   v
                                      Validated persisted report
```

```text
Controlled incident --> restricted tools <--> AI investigator
                                            |
                                            v
                              evidence-backed validated report
                                            |
                                            v
                               recommendations only; no action

Future: recommendation --> human approval --> controlled remediation
```

## Agentic investigation workflow

The application sends an incident identifier and initially exposes only the
incident metadata and event tools to the OpenAI Responses API. After that minimal
context is returned, the full set of eight strict diagnostic definitions becomes
available. The model must choose tools that confirm or disprove its current
hypothesis rather than enumerating every capability. It receives each bounded
result as explicitly untrusted evidence data and may request more tools only when
needed. Application code rejects unknown tools and arguments and never exposes a
SQL, filesystem, shell, or remediation capability.

The explicit loop allows at most six model calls and ten total tool calls. Each
provider call has a 45-second timeout and a 3,000-token output ceiling. Those
defaults are configurable through backend environment variables. Budget
exhaustion terminates safely. The safe activity trace records only tool name,
timestamp, status, and result count; prompts, evidence contents, provider bodies,
and hidden reasoning are not logged or persisted.

Final reports separate observed evidence from AI interpretation. They contain an
executive summary, cited timeline, qualitative primary hypothesis, alternatives,
key evidence, recommendations with approval flags, uncertainties, and runbook
references. The application attaches the exact evidence snapshot and rejects any
unknown evidence or runbook citation. AI conclusions are stored in the separate
`investigations` table, never in raw incident/evidence records.

Retrieved logs and runbooks are treated as untrusted data. Provider instructions
explicitly require ignoring embedded instructions, and tool output carries a data
boundary marker. Even a successful prompt injection cannot create a capability:
the registry recognizes only fixed, argument-free diagnostic functions.

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

## Restricted diagnostics and evidence

The `app/tools` package is the sole diagnostic boundary intended for a future
investigator. Tools accept typed, narrow inputs and return application-owned
`EvidenceItem` records. Each record has an application-generated ID, source,
type, optional timestamp and incident ID, factual summary, sanitized structured
details, and a logical source reference. The collector combines records but does
not rank evidence, infer a cause, create a timeline, or recommend remediation.

The PostgreSQL tools execute fixed read-only queries against system views. There
is no SQL, identifier, table, or command supplied by a client. The log tool reads
only the configured application JSONL path, applies equality filters, skips
malformed records, redacts secret-bearing fields, and caps results. The runbook
tool maps the three scenario enum values to three fixed filenames; it never
accepts a path.

Application logs are emitted as structured JSON to both stdout and a rotating
JSONL file. The file defaults to 1 MB with two backups and is mounted in a named
Docker volume. This preserves stdout operations while making bounded log evidence
programmatically available.

### Evidence sources

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

Application logs remain available through stdout:

```bash
docker compose logs api
```

They include event type and bounded context such as incident ID, scenario, request path, deployment version, and exception type. Passwords, connection strings, tokens, prompts, and stack traces are not logged.

Three concise runbooks live in `runbooks/`: `blocked-query.md`,
`connection-exhaustion.md`, and `bad-deployment.md`. They describe generic
symptoms, checks, evidence, likely causes, safe actions, approval boundaries, and
escalation conditions. They do not contain incident-specific conclusions.

## API

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Process liveness |
| `GET` | `/health/db` | PostgreSQL connectivity |
| `POST` | `/demo/incidents/blocked-query` | Start the row-lock scenario |
| `POST` | `/demo/incidents/connection-exhaustion` | Saturate the workload pool |
| `POST` | `/demo/incidents/bad-deployment` | Activate `v2-bad` |
| `GET` | `/demo/incidents/{incident_id}` | Read incident state and timeline |
| `GET` | `/demo/incidents/{incident_id}/evidence` | Collect bounded, sanitized source evidence |
| `POST` | `/demo/incidents/{incident_id}/investigate` | Run a bounded AI investigation |
| `GET` | `/demo/investigations/{investigation_id}` | Retrieve a persisted investigation |
| `GET` | `/demo/incidents/{incident_id}/investigations` | List up to 20 incident investigations |
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
| `DIAGNOSTIC_RESULT_LIMIT` | Per-tool result ceiling | `25` |
| `EVIDENCE_RESULT_LIMIT` | Evidence bundle ceiling | `100` |
| `LOG_PATH` | Application-controlled JSONL path | `logs/application.jsonl` |
| `LOG_MAX_BYTES` | Rotating JSONL file size | `1000000` |
| `LOG_BACKUP_COUNT` | Retained JSONL backups | `2` |
| `OPENAI_API_KEY` | OpenAI credential; backend runtime only | none |
| `OPENAI_MODEL` | Responses API model | `gpt-5.6-luna` |
| `INVESTIGATION_MAX_ITERATIONS` | Model-call ceiling | `6` |
| `INVESTIGATION_MAX_TOOL_CALLS` | Diagnostic-call ceiling | `10` |
| `INVESTIGATION_TIMEOUT_SECONDS` | Per-provider-call timeout | `45` |
| `INVESTIGATION_MAX_OUTPUT_TOKENS` | Per-call output ceiling | `3000` |
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
curl http://localhost:8000/demo/incidents/INCIDENT_ID/evidence
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

The automated suite uses dependency replacement and in-memory fakes; it does not require Docker or arbitrary sleep timing. It covers fixed diagnostic queries, bounds, pool/server distinction, log redaction, runbook allowlisting, evidence collection, and the evidence API. Real PostgreSQL behavior is verified separately through the Compose lab by collecting evidence while each incident is active.

Offline tests never call OpenAI. Optional live evaluations require the ignored
`.env` to contain `OPENAI_API_KEY`, a running Compose stack, and available API
credits:

```bash
python scripts/live_evaluations.py
```

The deterministic evaluator checks scenario-specific conclusions, citation
membership, report structure, tool/iteration budgets, and absence of executed
remediation claims.

## Current limitations

- Incident coordination is process-local and intended for one Uvicorn worker.
- The lab has no authentication or public rate limiting yet.
- Logs are local rotating JSONL plus stdout, not a centralized log platform.
- Schema changes use one idempotent SQL file; a migration framework is not justified yet.
- Investigation coordination remains synchronous and process-local.
- No remediation or human-approval execution capability exists in Phase 4.
