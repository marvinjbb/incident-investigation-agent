# Production deployment contract

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
image. Validate configuration before rollout, then run:

```text
docker compose -f docker-compose.yml -f docker-compose.production.yml run --rm migrate
docker compose -f docker-compose.yml -f docker-compose.production.yml up -d api
```

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
liveness fail.

## Route exposure

Only `/api/demo/*`, `/health/live`, and `/health/ready` should be routed publicly.
Nginx should deny `/demo/*`, `/docs`, `/redoc`, and `/openapi.json` in production as
defense in depth. The application independently returns 404 for `/demo/*` when the
production environment disables internal routes.

No remote infrastructure is created or modified by this repository.
