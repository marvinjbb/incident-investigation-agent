# Failing Application Deployment

## Symptoms
- Application errors begin shortly after a deployment becomes active.
- The active version fails against the current database schema or contract.

## Diagnostic checks
1. Compare deployment activation timestamps with the first failure event.
2. Confirm the active version and inspect bounded application error metadata.
3. Validate the failing contract in a safe non-production environment.

## Evidence to look for
- A newly activated deployment immediately preceding failures.
- A version-specific database or application exception such as `UndefinedColumn`.
- Recovery after returning to the known-good version.

## Likely causes
- Application/database contract incompatibility.
- Missing migration or incorrect deployment ordering.

Do not infer causality from timing alone. Require version-specific error and
deployment evidence.

## Safe recommended actions
- Stop new rollout activity and preserve timestamps and error types.
- Prepare a rollback or forward fix using the approved release process.

## Human approval required
- Rolling back or promoting any production deployment.
- Applying schema changes or bypassing release controls.

## Escalation conditions
- Rollback is unsafe, data compatibility is uncertain, or multiple services fail.
- Errors continue on the known-good version.
