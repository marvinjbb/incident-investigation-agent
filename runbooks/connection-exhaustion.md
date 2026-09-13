# Database Connection Exhaustion

## Symptoms
- Requests fail while waiting for an application pool connection.
- Pool availability reaches zero or waiting requests increase.

## Diagnostic checks
1. Compare application pool size, checked-out connections, availability, and timeout.
2. Compare those values with PostgreSQL session count and `max_connections`.
3. Review request errors for pool timeout evidence.

## Evidence to look for
- All configured application connections checked out.
- `PoolTimeout` application errors.
- PostgreSQL utilization materially below its server connection limit, if the pool is the bottleneck.

## Likely causes
- Connections held by slow or stalled application work.
- Pool size or timeout unsuitable for the bounded workload.
- A database-wide connection limit only when server utilization supports it.

## Safe recommended actions
- Stop the controlled load and confirm connections return to the pool.
- Inspect connection lifetime and transaction boundaries.

## Human approval required
- Raising PostgreSQL connection limits or production pool sizes.
- Restarting services or terminating sessions.

## Escalation conditions
- Connections do not return after the workload stops.
- PostgreSQL itself is near its configured limit or multiple applications are affected.
