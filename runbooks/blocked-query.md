# Blocked PostgreSQL Query

## Symptoms
- A request remains in a database wait state or exceeds its latency objective.
- PostgreSQL reports one session blocked by another session.

## Diagnostic checks
1. Confirm the incident timeline and affected request window.
2. Inspect `pg_stat_activity`, `pg_locks`, and `pg_blocking_pids()` through the approved diagnostic tool.
3. Compare blocked-query age with the blocking transaction age and application names.

## Evidence to look for
- A blocked PID mapped to one or more blocking PIDs.
- `wait_event_type` of `Lock` and a relevant wait event.
- A long-running or uncommitted blocking transaction.

## Likely causes
- An application transaction held open longer than intended.
- Concurrent updates targeted the same row or lock scope.

Do not infer database-wide exhaustion from a lock wait. Confirm the exact blocked
and blocking relationship first.

## Safe recommended actions
- Identify the owning workload and allow a known short transaction to complete.
- Reduce transaction scope and add regression coverage after recovery.

## Human approval required
- Canceling a production query or terminating a database session.
- Changing database isolation, schema, or application transaction behavior.

## Escalation conditions
- The blocker cannot be attributed to an approved application workload.
- Blocking affects multiple services, risks data loss, or returns after recovery.
