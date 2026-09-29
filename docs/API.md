# Public API

Base URL: `https://api.marvinjb.dev/api/demo`

The production contract is a session-scoped facade. It exposes curated workflow state, not raw diagnostics, unrestricted logs, prompts, or general infrastructure controls.

## Workflow

```text
create incident → read incident → investigate → read investigation
→ request proposal → approve → execute → inspect verified result
```

The first successful incident request sets an opaque HttpOnly, `SameSite=Strict` cookie scoped to `/api/demo`; production also marks it `Secure`. Browser clients must retain it. Every later resource is checked against that session; unknown and cross-session identifiers receive the same safe `404`.

## Routes

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/incidents` | Start one fixed controlled scenario. |
| `GET` | `/incidents/{incident_id}` | Read owned incident state and curated activity. |
| `POST` | `/incidents/{incident_id}/investigate` | Run one bounded investigation. |
| `GET` | `/investigations/{investigation_id}` | Retrieve an owned investigation. |
| `POST` | `/investigations/{investigation_id}/remediation` | Ask application policy to create a proposal. |
| `POST` | `/remediations/{proposal_id}/approve` | Record explicit human approval. |
| `POST` | `/remediations/{proposal_id}/execute` | Execute and verify an approved proposal. |

Health endpoints are `GET /health/live` and `GET /health/ready` at the API host root.

## Create an incident

```http
POST /api/demo/incidents
Content-Type: application/json

{"scenario":"blocked_query"}
```

Allowed scenarios are `blocked_query`, `connection_exhaustion`, and `bad_deployment`. Callers cannot supply a duration, command, SQL statement, PID, or deployment version.

Representative `202` response:

```json
{
  "incident_id": "00000000-0000-0000-0000-000000000000",
  "scenario": "blocked_query",
  "status": "active",
  "started_at": "2026-01-01T12:00:00Z",
  "ended_at": null,
  "activity": [
    {"event": "Incident started", "occurred_at": "2026-01-01T12:00:00Z"}
  ]
}
```

## Investigate

```http
POST /api/demo/incidents/{incident_id}/investigate
Cookie: incident_demo_session=...
```

A completed response contains the investigation ID and a curated report: executive summary, cited timeline and hypotheses, cited public evidence summaries, recommendations, uncertainties, safe tool activity, model name, call counts, and duration. Evidence details, unrestricted logs, prompts, provider bodies, and hidden reasoning are not returned.

Investigations can take tens of seconds. One running investigation is allowed per incident.

## Remediation proposal and approval

```http
POST /api/demo/investigations/{investigation_id}/remediation
POST /api/demo/remediations/{proposal_id}/approve
POST /api/demo/remediations/{proposal_id}/execute
```

The proposal action is selected by application policy, never by the request. A representative proposal contains:

```json
{
  "proposal_id": "00000000-0000-0000-0000-000000000000",
  "incident_id": "00000000-0000-0000-0000-000000000000",
  "action_type": "terminate_demo_blocker",
  "status": "pending_approval",
  "summary": "Terminate the verified demo-owned blocking session",
  "expires_at": "2026-01-01T12:05:00Z",
  "verification_result": null,
  "activity": []
}
```

Proposals expire after the configured TTL. Approval does not execute. The public facade exposes explicit approval; declining to approve leaves the executor unauthorized. The internal development API also supports an explicit rejection transition, but it is not part of the public route surface above. Execution revalidates ownership and live technical conditions, then records scenario-specific verification. Calling execute again after success returns the existing result; failed actions are not automatically retried.

Successful execution means more than an HTTP response from the executor. The application verifies that the incident is resolved and the workload is healthy, plus the relevant scenario condition: no blocking relationship, available pool capacity, or healthy `v1` active.

## Limits

The default rolling window is 600 seconds:

- three incident creations globally;
- two investigations per session;
- six remediation operations per session.

A rejected request returns `429` and `Retry-After`. Only one synthetic incident may be active globally.

## Errors and request IDs

Safe errors use:

```json
{
  "detail": {
    "code": "rate_limited",
    "message": "The public demo is temporarily at capacity",
    "request_id": "00000000-0000-0000-0000-000000000000"
  }
}
```

Important statuses:

| Status | Meaning |
| --- | --- |
| `202` | Incident accepted. |
| `401` | Session missing or expired. |
| `404` | Unknown or non-owned resource; ownership is not disclosed. |
| `409` | Conflicting active incident/investigation or invalid proposal state. |
| `410` | Proposal expired before execution. |
| `422` | Invalid structured request. |
| `429` | Rate limit reached; inspect `Retry-After`. |
| `503` | Investigation/provider temporarily unavailable. |

Every application response includes `X-Request-ID`. A valid caller-provided UUID may be propagated; invalid values are replaced.

Responses also set `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, and `X-Frame-Options: DENY`. Production adds `Strict-Transport-Security: max-age=31536000; includeSubDomains`.

## Internal API boundary

Development exposes `/demo/*` routes for the lab, raw evidence, diagnostics, and controlled verification. These routes return `404` when production sets `ENVIRONMENT=production` and `EXPOSE_INTERNAL_ROUTES=false`.

Only `/api/demo/*`, `/health/live`, and `/health/ready` are routed through production Nginx. Swagger, ReDoc, and `/openapi.json` are intentionally disabled in production.
