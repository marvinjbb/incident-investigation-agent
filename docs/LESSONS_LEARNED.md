# Lessons learned

## 1. Calling every tool is not intelligent tool use

Early behavior overused diagnostics, increasing latency and cost while weakening coherence. The investigator now stages tool exposure, describes hypothesis-oriented use, and evaluates selection. The evaluator rejects the complete registry and unmotivated cross-subsystem sweeps.

## 2. LLM-generated references must be validated

Plausible citations are not grounding. Evidence IDs and runbook references are application-owned and checked as exact subsets of records retrieved during that investigation. One bounded repair turn may select a valid reference, but there is no fuzzy acceptance.

## 3. Safety timeouts must fit the whole workflow

An incident that expires before investigation and approval makes safe remediation impossible. Production uses the bounded 120-second maximum, allowing a decision while retaining automatic recovery.

## 4. Health checks must respect production host policy

Loopback checks initially received HTTP 400 because TrustedHostMiddleware correctly rejected the host. The fix was not weaker policy: Docker and deployment checks now send `Host: api.marvinjb.dev`.

## 5. A deployment scenario must create a genuine failure

Marking `v2-bad` active did not create error evidence. The lab now executes one fixed demo workload after activation, producing a genuine schema-incompatibility `UndefinedColumn` without arbitrary SQL or caller input.

## 6. Model recommendation is not execution authority

The executor is absent from the model registry. Application policy maps a supported recommendation, a human approves, the application revalidates ownership and live conditions, and the executor verifies recovery.

## 7. Process-local resources constrain scaling

The pool scenario owns real in-process connections. Multiple API workers could separate ownership from inspection or release. One worker is deliberate; future scale requires a dedicated incident-lab coordinator.

## 8. Deployment verification includes release identity

Healthy containers do not prove the intended release is current. The deployment helper advances the release pointer only after migrations, liveness, and readiness succeed.

## 9. Human approval does not eliminate stale-state risk

Approval can arrive after the incident, proposal, or supporting conditions have changed. Execution must recheck ownership, expiration, policy evidence, incident state, and live scenario preconditions, then atomically claim the proposal before acting.

## 10. Executor completion is not recovery

An allowlisted function returning without an exception does not prove the service recovered. The system separately verifies incident resolution, workload health, and the scenario-specific condition before recording success.
