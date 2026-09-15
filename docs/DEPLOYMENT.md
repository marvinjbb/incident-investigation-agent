# Production deployment contract

## Prerequisites

- Linux host with Docker Engine and the Compose plugin;
- existing TLS termination and Nginx virtual host for `api.marvinjb.dev`;
- an immutable release archive that excludes Git metadata, `.env`, caches, logs,
  tests, and credentials;
- a separately transferred root-owned runtime environment containing strong
  PostgreSQL and OpenAI credentials;
- free loopback port `8002`, adequate disk space, and no conflicting deployment
  directory;
- a reviewed Alembic migration path and retained previous release for rollback.

Deployment is intentionally manual and controlled. CI validates the code,
migration, and image but never accesses the VPS or production credentials.

## Topology

```text
Internet -> Cloudflare -> Nginx -> FastAPI container -> PostgreSQL
                                  -> OpenAI API (investigations only)
```

The expected API host is `api.marvinjb.dev`. Cloudflare and Nginx terminate/publicly
enforce HTTPS. Nginx forwards only to the private container port and should set
`Host`, `X-Forwarded-For`, and `X-Forwarded-Proto`. Uvicorn must be started with
`--proxy-headers` and `--forwarded-allow-ips` restricted to the Nginx/container
network address; never trust forwarded headers from every client.

Recommended proxy bounds:

- request body: 64 KiB (the public API accepts JSON only);
- investigation timeout: 60 seconds, slightly above the application timeout;
- connect timeout: 5 seconds;
- no buffering requirement because the current API does not stream;
- preserve `X-Request-ID` and return it to the browser.

## Startup

Provide all secrets through the runtime environment. Do not copy `.env` into an
image. The verified VPS layout is:

- releases: `/opt/incident-investigation-agent/releases/<release-id>`;
- active release pointer: `/opt/incident-investigation-agent/current`;
- runtime environment: `/etc/incident-investigation-agent/environment`, owned by
  `root:root` with mode `0600`;
- Compose project and private network: `incident-agent` and
  `incident-agent_default`;
- API binding: `127.0.0.1:8002` to container port `8000`;
- PostgreSQL: private container port only, with no host binding;
- persistent volumes: `incident-agent_incident-postgres-data` and
  `incident-agent_incident-application-logs`.

Transfer a clean release archive and a separately staged root-only runtime
environment file. The archive must exclude `.git`, `.env`, `.venv`, caches, logs,
test artifacts, and credentials. Run the checked-in deployment helper on the VPS:

```text
scripts/deploy-production.sh RELEASE_ID ARCHIVE_PATH ENVIRONMENT_PATH
```

The helper validates both staged files and the release identifier, installs the
runtime environment as `0600`, validates Compose, builds the release, starts and
health-checks PostgreSQL, runs `alembic upgrade head`, and stops if migration
fails. It then starts only the Incident Agent API, performs Host-aware loopback
health checks, and advances `current` only after readiness succeeds. Never remove
the PostgreSQL or application-log volumes during a normal release or rollback.

The production overlay deliberately fails interpolation when database credentials,
the OpenAI key, or `TRUSTED_PROXY_IPS` are absent. The API is a non-root container
and remains a single Uvicorn worker because the pool-exhaustion simulation owns
genuine process-local checked-out connections. `UVICORN_WORKERS` must be `1`; the
application rejects any other production value and the Compose command also fixes
the worker count to one. This service does not claim horizontal or multi-worker
support. If that becomes necessary, add a dedicated incident-lab coordinator that
owns synthetic resources and accepts database-backed commands from stateless API
workers. Do not replace the real pool exhaustion with a fake shared counter.

Use `/health/live` for process liveness. Use `/health/ready` for readiness and
traffic admission because it verifies PostgreSQL. OpenAI availability does not make
liveness fail. Production loopback checks must send `Host: api.marvinjb.dev` because
the same TrustedHostMiddleware policy applies on `127.0.0.1:8002`; an omitted host
header correctly returns HTTP 400 and must not be interpreted as a startup failure.

The production demo uses the bounded 120-second incident lifetime and retains
automatic recovery. This allows an investigation to finish before the proposal,
approval, and execution steps while ensuring abandoned synthetic incidents recover.

## Route exposure

Only `/api/demo/*`, `/health/live`, and `/health/ready` should be routed publicly.
Nginx should deny `/demo/*`, `/docs`, `/redoc`, and `/openapi.json` in production as
defense in depth. The application independently returns 404 for `/demo/*` when the
production environment disables internal routes.

The verified Nginx configuration adds only these Incident Agent route groups to the
existing `api.marvinjb.dev` server: `/api/demo/`, `/health/live`, and
`/health/ready`. They proxy to `127.0.0.1:8002`. Validate with `nginx -t` before
every reload. Do not modify the existing Extraction Agent or Research Agent
upstreams, routes, containers, ports, or data.

## Verified production checks

The Phase 7 deployment was verified through public HTTPS with all three controlled
scenarios:

- blocked query: PostgreSQL exposed the exact controlled blocker relationship;
  explicit approval executed only `terminate_demo_blocker`;
- connection-pool exhaustion: application pool saturation was distinguished from
  PostgreSQL server capacity; explicit approval executed only
  `release_demo_pool_pressure`;
- bad deployment: activating `v2-bad` exercised the fixed demo workload once and
  produced a genuine `UndefinedColumn` application error; explicit approval
  executed only `rollback_demo_deployment` and restored healthy `v1`.

Every report used exact application-owned evidence IDs and exact retrieved runbook
references. Each proposal remained session-owned, pending explicit approval, and
subject to expiration and execution-time precondition checks. Restart verification
preserved database state and left both existing agent deployments unchanged.

For rollback, retain prior immutable release directories and images. Repoint and
restart only the Incident Agent API after validating the previous release against
the current database schema. Do not reverse an Alembic migration automatically and
never delete persistent volumes as part of rollback.

## Post-deployment smoke checklist

1. Confirm the API and PostgreSQL containers are healthy.
2. Verify the API binds only to `127.0.0.1:8002`; PostgreSQL has no host port.
3. Call loopback liveness and readiness with `Host: api.marvinjb.dev`.
4. Call public HTTPS `/health/live` and `/health/ready`.
5. Confirm exact CORS behavior for `https://marvinjb.dev` and rejection of an
   unrelated origin.
6. Confirm invalid hosts fail and security/request-ID headers are present.
7. Confirm `/demo/*`, `/docs`, `/redoc`, and `/openapi.json` return 404.
8. Verify a fresh public session can own its resource and a second session cannot.
9. Inspect safe logs for startup/migration failures and credential-like output.
10. Confirm the `current` pointer identifies the release that passed readiness.

Do not run provider-backed investigations or remediation as an ordinary smoke
check. Those require explicit authorization.

## Rollback checklist

1. Stop before rollback if the previous release is incompatible with the current
   database schema.
2. Keep PostgreSQL and application-log volumes attached and never run
   `docker compose down --volumes`.
3. Validate the previous immutable release and production environment.
4. Repoint/restart only the Incident Agent API; do not restart other portfolio
   services.
5. Repeat Host-aware liveness/readiness and the public security smoke checks.
6. Confirm the active release pointer after verification.

Database migrations are not automatically downgraded. A failed or destructive
migration needs a separately reviewed recovery plan.

## Logs and troubleshooting

Use Compose to inspect the Incident Agent API, migration job, and PostgreSQL
containers. Application events are structured JSON on stdout and in the named
application-log volume. Search only safe fields such as request ID, incident ID,
event type, status, and error type; do not copy environment contents into logs.

Common failures:

- **Loopback health returns 400:** send `Host: api.marvinjb.dev`; do not weaken
  TrustedHostMiddleware.
- **Readiness returns 503:** inspect PostgreSQL health and migration completion
  before restarting the API.
- **Migration fails:** stop the release; do not advance `current`.
- **Production configuration fails:** verify required variables are present
  without printing their values and confirm `UVICORN_WORKERS=1`.
- **Port 8002 is occupied:** stop and resolve the conflict; do not publish the API
  on a broad interface.
- **Investigation is unavailable:** use request IDs and safe error categories;
  never log provider bodies, prompts, or credentials.

## Persistence, backup, and retention

PostgreSQL data and rotating application logs use the named volumes documented
above. Normal release and rollback operations preserve both volumes.

This portfolio deployment does **not** implement or claim an automated database
backup/restore system. Before any change with data-loss risk, an operator must
create and test an external PostgreSQL backup appropriate to the host. Formal
backup scheduling, off-host retention, restore drills, and recovery objectives
remain outside the current scope.

Release directories are retained to support controlled rollback, but there is no
automated retention policy. Review disk use and remove an old release only through
an explicit operator decision after confirming it is not `current` or a required
rollback target. Persistent volumes are never part of release cleanup.
