"""Run the three optional live-model evaluations against the local Compose API."""

import asyncio
import sys

import httpx

from app.investigation.evaluation import evaluate_report
from app.investigation.models import InvestigationReport
from app.models import ScenarioType

BASE_URL = "http://localhost:8000"


async def evaluate_scenario(client: httpx.AsyncClient, scenario: ScenarioType) -> None:
    route = scenario.value.replace("_", "-")
    start = await client.post(f"/demo/incidents/{route}", json={"duration_seconds": 15})
    start.raise_for_status()
    incident_id = start.json()["incident_id"]
    try:
        await asyncio.sleep(0.5)
        if scenario is not ScenarioType.BLOCKED_QUERY:
            workload = await client.get("/demo/workload")
            if workload.status_code != 503:
                raise RuntimeError(
                    "Scenario did not produce the expected workload failure"
                )
        response = await client.post(
            f"/demo/incidents/{incident_id}/investigate", timeout=180
        )
        response.raise_for_status()
        report = InvestigationReport.model_validate(response.json())
        evidence = {item.evidence_id: item for item in report.evidence_catalog}
        result = evaluate_report(scenario, report, evidence, 6, 10)
        print(
            f"{scenario.value}: passed={result.passed} "
            f"model={report.model} model_calls={report.metrics.model_calls} "
            f"tool_calls={report.metrics.tool_calls} "
            f"duration_ms={report.metrics.duration_ms}"
        )
        print(f"  cause={report.primary_hypothesis.cause}")
        print(f"  checks={result.checks}")
        if not result.passed:
            raise RuntimeError(f"Live evaluation failed for {scenario.value}")
    finally:
        await client.post(f"/demo/incidents/{incident_id}/recover")
        workload = await client.get("/demo/workload")
        workload.raise_for_status()


async def main() -> None:
    scenarios = [ScenarioType(sys.argv[1])] if len(sys.argv) > 1 else list(ScenarioType)
    async with httpx.AsyncClient(base_url=BASE_URL) as client:
        for scenario in scenarios:
            await evaluate_scenario(client, scenario)


if __name__ == "__main__":
    asyncio.run(main())
