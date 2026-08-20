import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from jarvis_pd.contracts.user_node import UserNodeProposal

FIXTURES = Path(__file__).parents[1] / "fixtures" / "user_node"


@pytest.mark.parametrize("path", sorted((FIXTURES / "valid").glob("*.json")))
def test_valid_fixtures(path: Path) -> None:
    proposal = UserNodeProposal.model_validate_json(path.read_text())
    assert proposal.contract == "jarvis.user-node-output"


@pytest.mark.parametrize("path", sorted((FIXTURES / "invalid").glob("*.json")))
def test_invalid_fixtures(path: Path) -> None:
    with pytest.raises(ValidationError):
        UserNodeProposal.model_validate_json(path.read_text())


def test_contract_is_proposal_without_mutation_api() -> None:
    data = json.loads((FIXTURES / "valid" / "select-task.json").read_text())
    proposal = UserNodeProposal.model_validate(data)
    forbidden = {"save", "commit", "execute", "transition", "mutate"}
    assert forbidden.isdisjoint(dir(proposal))
