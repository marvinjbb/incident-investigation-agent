# Evaluation

## Philosophy

The evaluation target is not “did the model answer?” A successful investigation must diagnose the controlled failure, choose relevant diagnostics, cite only observed evidence, remain within budgets, stop before remediation, and support a proposal that can pass human approval and recovery verification.

## Evaluation tiers

| Tier | Command | OpenAI credential | Mutates lab | Ordinary CI |
| --- | --- | --- | --- | --- |
| Offline tests | `pytest` | No | No production/provider activity | Yes |
| Static checks | `ruff check .`, `ruff format --check .` | No | No | Yes |
| Migration/container validation | CI/Compose commands | No | Disposable CI database only | Yes |
| Live model evaluation | `python scripts/live_evaluations.py` | Yes | Starts/recovers synthetic local incidents | No |
| Remediation verification | `python scripts/live_remediations.py` | Yes | **Yes: executes allowlisted local-lab remediation** | **Never** |

The optional scripts target the internal development API at `http://localhost:8000`. They must not be pointed at production or run automatically. Live calls incur provider cost.

## Offline coverage

The suite uses fakes and dependency replacement for deterministic behavior. It covers incident lifecycles and coordination; genuine scenario setup; fixed diagnostics; log sanitization; runbook allowlisting; evidence IDs; strict tools and provider behavior; reference repair; prompt-injection boundaries; persistence; remediation policy, approval, idempotency, TOCTOU checks and verification; public sessions, limits and security headers; production configuration; and Host-aware health checks.

## Deterministic agent evaluator

`app/investigation/evaluation.py` checks that citations exist, tool/model calls stay bounded, recommendations do not claim execution, the model does not call every tool, and tools are not repeated. Scenario-specific checks require the correct evidence and relevant tools while rejecting unsupported subsystem sweeps.

## Scenario matrix

| Scenario | Expected diagnosis/evidence | Selection discipline | Proposal and recovery |
| --- | --- | --- | --- |
| Blocked query | Identify a lock/blocking relationship and cite PostgreSQL blocking evidence. | Select blocking diagnostics; avoid unmotivated pool/deployment tools. | `terminate_demo_blocker`; approval; blocker removed and workload healthy. |
| Connection-pool exhaustion | Identify application-local saturation, cite pool state and PostgreSQL capacity, and avoid server-wide exhaustion claims. | Select pool/server-connection diagnostics; avoid unmotivated lock/deployment tools. | `release_demo_pool_pressure`; approval; pool and workload recover. |
| Bad deployment | Identify supported `v2-bad` incompatibility and cite deployment plus genuine `UndefinedColumn` evidence. | Select deployment evidence; avoid unmotivated lock/pool tools. | `rollback_demo_deployment`; approval; `v1` and workload recover. |

## Grounding and remediation criteria

A report fails if an evidence ID is absent from its catalog or a runbook reference was not retrieved. There is no fuzzy matching. One bounded correction may select valid identifiers, but exact validation remains.

The report cannot execute an action. Application policy must recognize an eligible recommendation and supporting evidence. A proposal must be owned, unexpired, approved, and atomically claimed. The executor rechecks live technical preconditions. Success is recorded only after scenario-specific recovery checks pass.

## Verified results

Repository tests and the verified production record in [DEPLOYMENT.md](DEPLOYMENT.md) support qualitative passes:

- **Blocked query:** selective blocking diagnostics, valid citations, approved controlled termination, verified recovery.
- **Connection-pool exhaustion:** correct pool/server distinction, valid evidence, approved pressure release, verified recovery.
- **Bad deployment:** genuine `UndefinedColumn`, supported `v2-bad` correlation, valid deployment/runbook evidence, approved rollback, verified recovery.

No performance benchmark or universal accuracy claim is made.

## Limitations

- Three controlled scenarios do not measure general incident-response accuracy.
- Provider behavior can vary; validators turn unacceptable variation into failure.
- The evaluator uses explicit scenario terms/evidence types, not a broad semantic judge.
- Public sessions are anonymous rather than enterprise identities.
- Remediation evaluation covers only the three synthetic actions.
- Provider-backed evaluation requires manual authorization, credentials, cost, and a running local lab.
