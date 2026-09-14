from uuid import uuid4

import pytest

from app.investigation.registry import InvestigationToolRegistry, ToolRegistryError


def registry() -> InvestigationToolRegistry:
    dependency = object()
    return InvestigationToolRegistry(
        uuid4(),
        dependency,  # type: ignore[arg-type]
        dependency,  # type: ignore[arg-type]
        dependency,  # type: ignore[arg-type]
        dependency,  # type: ignore[arg-type]
        dependency,  # type: ignore[arg-type]
        dependency,  # type: ignore[arg-type]
        dependency,  # type: ignore[arg-type]
    )


def test_registry_exposes_only_strict_allowlisted_functions() -> None:
    definitions = registry().definitions

    assert {item["name"] for item in definitions} == {
        "get_incident",
        "get_incident_events",
        "get_application_logs",
        "get_database_blocking",
        "get_database_connections",
        "get_application_pool_state",
        "get_recent_deployments",
        "get_runbook",
    }
    assert all(item["strict"] is True for item in definitions)
    assert all(
        item["parameters"]["additionalProperties"] is False for item in definitions
    )
    assert not any(
        "remediat" in item["name"]
        or "terminate" in item["name"]
        or "rollback" in item["name"]
        for item in definitions
    )


@pytest.mark.asyncio
async def test_registry_rejects_unknown_capability_and_arguments() -> None:
    tools = registry()
    with pytest.raises(ToolRegistryError):
        await tools.execute("run_shell", "{}")
    with pytest.raises(ToolRegistryError):
        await tools.execute("get_incident", '{"path":"../../.env"}')
    with pytest.raises(ToolRegistryError):
        await tools.execute("get_incident", "not-json")
