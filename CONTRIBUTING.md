# Contributing

## Prerequisites

Use Python 3.12 and either Docker Compose or a local PostgreSQL 17 instance.

```bash
python -m venv .venv
source .venv/bin/activate  # PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]" -c requirements.lock
```

Copy `.env.example` to `.env` only for local configuration. Never commit `.env`, credentials, provider responses, unrestricted logs, or production data.

## Validation

Before a pull request:

```bash
ruff check .
ruff format --check .
pytest
alembic upgrade head
docker build -t incident-investigation-agent:local .
```

The migration command requires the configured disposable/local PostgreSQL database. Ordinary tests use fakes and must not call OpenAI.

## Safety rules

Changes must preserve these boundaries:

- no arbitrary SQL, shell, filesystem, PID, or deployment-version input;
- diagnostic tools remain allowlisted, read-only, typed, bounded, and incident-scoped;
- evidence and runbook references remain exact and application-owned;
- remediation stays outside the model tool registry;
- application policy, explicit approval, TOCTOU revalidation, and recovery verification remain mandatory;
- production remains one Uvicorn worker unless the lab receives a dedicated coordinator;
- internal routes remain disabled in production.

New capabilities require explicit design review and tests; do not broaden permissions merely to simplify an evaluation.

## Provider and mutating checks

`scripts/live_evaluations.py` is optional, requires an OpenAI credential, incurs cost, and starts controlled local incidents. It is not an ordinary CI command.

`scripts/live_remediations.py` additionally approves and executes the three allowlisted actions against the local synthetic lab. Run it only with explicit authorization. It must never run in ordinary CI or against production.

## Migrations

Production schema changes use Alembic. Add a reviewed migration, verify upgrade against a disposable database, and never add an automatic destructive downgrade. The baseline is intentionally irreversible.

## Pull requests

Keep changes scoped. Explain the problem, trust-boundary impact, tests, migration implications, and expected failure behavior. Include regression coverage and update documentation when contracts change. Never include generated caches, logs, local database data, screenshots with private information, or secrets.
