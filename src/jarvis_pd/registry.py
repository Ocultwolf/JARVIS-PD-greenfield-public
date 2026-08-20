from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from jarvis_pd.contracts.registry import CONTRACTS


class AgentDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    runtime: str = Field(min_length=1)
    contract: str
    capabilities: list[str]
    authority: str
    enabled: bool


class AgentRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str
    agents: list[AgentDefinition]


class ToolRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str
    tools: list[dict[str, Any]]
    execution_enabled: bool


def load_registries(config_dir: Path) -> tuple[AgentRegistry, ToolRegistry]:
    agents = AgentRegistry.model_validate_json((config_dir / "agents.json").read_text())
    tools = ToolRegistry.model_validate_json((config_dir / "tools.json").read_text())
    unknown = {agent.contract for agent in agents.agents} - set(CONTRACTS)
    if unknown:
        raise ValueError(f"Unknown contracts: {sorted(unknown)}")
    if tools.execution_enabled:
        raise ValueError("Tool execution is forbidden in Phase 0")
    TypeAdapter(list[dict[str, Any]]).validate_python(tools.tools)
    return agents, tools
