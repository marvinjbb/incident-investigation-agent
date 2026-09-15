# Security policy

## Scope

This repository is a controlled portfolio demonstration, not a general infrastructure-administration product. The public lab operates only on synthetic incidents and demo-owned resources. Do not submit real production data, credentials, customer information, or sensitive logs.

## Security boundaries

- The model has no arbitrary SQL, shell, filesystem, PID, or deployment-version capability.
- Eight diagnostic tools are read-only, bounded, allowlisted, and bound to one incident.
- Evidence IDs and runbook references are validated against records retrieved during the investigation.
- Remediation is outside the model tool registry.
- Application policy selects one of three fixed demo actions.
- A session-owned, unexpired proposal requires explicit human approval.
- Execution revalidates ownership and live conditions and verifies recovery.
- Public sessions and rate limits are stored in PostgreSQL.
- Internal diagnostic routes and API documentation are disabled in production.
- Credentials are runtime environment values and must never be committed.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting feature for this repository, if available, or contact the repository owner privately through the contact method listed on the public GitHub profile. Do not open a public issue containing exploit details, credentials, personal data, or an active vulnerability before coordination.

Include the affected component, reproduction steps using synthetic data, impact, and any suggested mitigation. Do not test against unrelated systems or attempt to access another user's session.

## Supported status

This portfolio project has no published long-term support schedule. Security fixes apply to the latest revision on `main`. No open-source license has been selected.
