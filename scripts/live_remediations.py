"""Run approved end-to-end remediation evaluations against the local Compose API."""

import asyncio
import sys

import httpx

from app.investigation.evaluation import evaluate_report
from app.investigation.models import InvestigationReport
from app.models import ScenarioType
from app.remediation.models import RemediationProposal, RemediationStatus

BASE_URL = "http://localhost:8000"


async def evaluate_scenario(client: httpx.AsyncClient, scenario: ScenarioType) -> None:
    route = scenario.value.replace("_", "-")
    start = await client.post(f"/demo/incidents/{route}", json={"duration_seconds": 90})
    start.raise_for_status()
    incident_id = start.json()["incident_id"]
    try:
        await asyncio.sleep(0.5)
        if scenario is not ScenarioType.BLOCKED_QUERY:
            failed_workload = await client.get("/demo/workload")
            if failed_workload.status_code != 503:
                raise RuntimeError("Scenario did not produce expected workload failure")

        investigation_response = await client.post(
            f"/demo/incidents/{incident_id}/investigate", timeout=180
        )
        investigation_response.raise_for_status()
        report = InvestigationReport.model_validate(investigation_response.json())
        evidence = {item.evidence_id: item for item in report.evidence_catalog}
        evaluation = evaluate_report(scenario, report, evidence, 6, 10)
        if not evaluation.passed:
            raise RuntimeError("Investigation evaluation failed")

        proposal_response = await client.post(
            f"/demo/investigations/{report.investigation_id}/remediation-proposals"
        )
        proposal_response.raise_for_status()
        proposed = RemediationProposal.model_validate(proposal_response.json())
        if proposed.status is not RemediationStatus.PENDING_APPROVAL:
            raise RuntimeError("Proposal did not stop for human approval")

        approval_response = await client.post(
            f"/demo/remediations/{proposed.proposal_id}/approve"
        )
        approval_response.raise_for_status()
        approved = RemediationProposal.model_validate(approval_response.json())
        if approved.status is not RemediationStatus.APPROVED:
            raise RuntimeError("Approval was not recorded")

        execution_response = await client.post(
            f"/demo/remediations/{proposed.proposal_id}/execute", timeout=30
        )
        execution_response.raise_for_status()
        completed = RemediationProposal.model_validate(execution_response.json())
        workload = await client.get("/demo/workload")
        incident = await client.get(f"/demo/incidents/{incident_id}")
        passed = (
            completed.status is RemediationStatus.SUCCEEDED
            and all((completed.verification_result or {}).values())
            and workload.status_code == 200
            and incident.json()["status"] == "resolved"
        )
        print(
            f"{scenario.value}: passed={passed} action={completed.action_type.value} "
            f"model_calls={report.metrics.model_calls} "
            f"tool_calls={report.metrics.tool_calls} "
            f"verification={completed.verification_result}"
        )
        if not passed:
            raise RuntimeError("Remediation evaluation failed")
    finally:
        await client.post(f"/demo/incidents/{incident_id}/recover")


async def main() -> None:
    scenarios = [ScenarioType(sys.argv[1])] if len(sys.argv) > 1 else list(ScenarioType)
    async with httpx.AsyncClient(base_url=BASE_URL) as client:
        for scenario in scenarios:
            await evaluate_scenario(client, scenario)


if __name__ == "__main__":
    asyncio.run(main())
