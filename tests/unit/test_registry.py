from pathlib import Path

from jarvis_pd.registry import load_registries


def test_registries_are_declarative_and_valid() -> None:
    agents, tools = load_registries(Path("config"))
    assert agents.agents[0].authority == "PROPOSAL_ONLY"
    assert not agents.agents[0].enabled
    assert not tools.execution_enabled
    assert tools.tools == []
