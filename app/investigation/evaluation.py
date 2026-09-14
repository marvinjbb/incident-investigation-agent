from dataclasses import dataclass

from app.evidence import EvidenceItem, EvidenceType
from app.investigation.models import InvestigationReport
from app.models import ScenarioType

ALL_DIAGNOSTIC_TOOLS = {
    "get_incident",
    "get_incident_events",
    "get_application_logs",
    "get_database_blocking",
    "get_database_connections",
    "get_application_pool_state",
    "get_recent_deployments",
    "get_runbook",
}


@dataclass(frozen=True)
class EvaluationResult:
    passed: bool
    checks: dict[str, bool]


def evaluate_report(
    scenario: ScenarioType,
    report: InvestigationReport,
    evidence: dict[str, EvidenceItem],
    maximum_iterations: int,
    maximum_tool_calls: int,
) -> EvaluationResult:
    cited = report.cited_evidence_ids()
    cited_types = {evidence[item].evidence_type for item in cited if item in evidence}
    conclusion = (
        f"{report.primary_hypothesis.cause} {report.primary_hypothesis.explanation}"
    ).lower()
    selected_tools = [activity.tool for activity in report.activity_trace]
    checks = {
        "citations_exist": cited.issubset(evidence),
        "tool_budget": report.metrics.tool_calls <= maximum_tool_calls,
        "iteration_budget": report.metrics.model_calls <= maximum_iterations,
        "no_executed_remediation": all(
            " executed " not in f" {action.action.lower()} "
            for action in report.recommended_actions
        ),
        "selective_tool_use": set(selected_tools) != ALL_DIAGNOSTIC_TOOLS,
        "no_repeated_tools": len(selected_tools) == len(set(selected_tools)),
    }
    if scenario is ScenarioType.BLOCKED_QUERY:
        checks.update(
            {
                "identifies_blocking": "block" in conclusion or "lock" in conclusion,
                "cites_blocking": EvidenceType.BLOCKED_SESSION in cited_types,
                "selected_blocking_diagnostic": (
                    "get_database_blocking" in selected_tools
                ),
                "avoids_unmotivated_pool_or_deployment": not {
                    "get_application_pool_state",
                    "get_recent_deployments",
                }.intersection(selected_tools),
            }
        )
    elif scenario is ScenarioType.CONNECTION_EXHAUSTION:
        checks.update(
            {
                "identifies_pool": "pool" in conclusion,
                "cites_pool": EvidenceType.POOL_STATE in cited_types,
                "cites_server_capacity": (
                    EvidenceType.CONNECTION_UTILIZATION in cited_types
                ),
                "does_not_claim_server_exhaustion": not (
                    "postgresql" in conclusion
                    and "max_connections reached" in conclusion
                ),
                "selected_pool_diagnostics": {
                    "get_application_pool_state",
                    "get_database_connections",
                }.issubset(selected_tools),
                "avoids_unmotivated_blocking_or_deployment": not {
                    "get_database_blocking",
                    "get_recent_deployments",
                }.intersection(selected_tools),
                "does_not_blame_unrelated_deployment": not (
                    "v2-bad" in conclusion or "deployment" in conclusion
                ),
            }
        )
    elif scenario is ScenarioType.BAD_DEPLOYMENT:
        checks.update(
            {
                "identifies_bad_version": "v2-bad" in conclusion,
                "identifies_schema_error": (
                    "undefinedcolumn" in conclusion
                    or "undefined column" in conclusion
                    or "schema" in conclusion
                ),
                "cites_deployment": EvidenceType.DEPLOYMENT_CHANGE in cited_types,
                "cites_application_error": EvidenceType.APPLICATION_ERROR
                in cited_types,
                "selected_deployment_diagnostic": (
                    "get_recent_deployments" in selected_tools
                ),
                "avoids_unmotivated_blocking_or_pool": not {
                    "get_database_blocking",
                    "get_application_pool_state",
                }.intersection(selected_tools),
            }
        )
    return EvaluationResult(passed=all(checks.values()), checks=checks)
