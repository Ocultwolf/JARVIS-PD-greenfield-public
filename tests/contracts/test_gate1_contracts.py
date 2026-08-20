import json
from pathlib import Path

import pytest

from jarvis_pd.contracts.registry import (
    CONTRACTS,
    ContractRejectionCode,
    ContractValidationError,
    export_json_schemas,
    validate_contract,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "gate1"
SCHEMAS = Path("docs/contracts")


@pytest.mark.parametrize("path", sorted((FIXTURES / "valid").glob("*.json")))
def test_gate1_valid_fixtures(path: Path) -> None:
    data = json.loads(path.read_text())
    contract_id = f"{data['contract']}@{data['schema_version']}"
    validated = validate_contract(contract_id, data)
    assert validated.model_dump(mode="json")["contract"] == data["contract"]


@pytest.mark.parametrize("path", sorted((FIXTURES / "invalid").glob("*.json")))
def test_gate1_invalid_fixtures_have_known_reason_code(path: Path) -> None:
    fixture = json.loads(path.read_text())
    with pytest.raises(ContractValidationError) as captured:
        validate_contract(fixture["contract_id"], fixture["payload"])
    assert captured.value.code == ContractRejectionCode(fixture["expected_reason_code"])


@pytest.mark.parametrize(
    "filename",
    [
        "injection-session-mismatch.json",
        "injection-transport-mismatch.json",
        "injection-hash-mismatch.json",
        "injection-invocation-mismatch.json",
        "injection-manifest-mismatch.json",
    ],
)
def test_confirmed_injection_rejects_every_normative_correlation_mismatch(
    filename: str,
) -> None:
    fixture = json.loads((FIXTURES / "invalid" / filename).read_text())
    with pytest.raises(ContractValidationError) as captured:
        validate_contract(fixture["contract_id"], fixture["payload"])
    assert captured.value.code == ContractRejectionCode.SCHEMA_VALIDATION_FAILED


def test_latch_configuration_is_inert_and_preserves_deferred_release() -> None:
    fixture = json.loads((FIXTURES / "valid" / "gate1-latch-config.json").read_text())
    validated = validate_contract("jarvis.gate1-latch-config@1.0.0", fixture)
    assert validated.production_release == "DEFERRED_NOT_IMPLEMENTED"
    assert validated.health_recovery_releases_latch is False
    assert not hasattr(validated, "activate")
    assert not hasattr(validated, "release")


def test_all_gate1_contracts_have_published_deterministic_schemas(tmp_path: Path) -> None:
    generated = export_json_schemas(tmp_path)
    gate1_ids = [contract_id for contract_id in CONTRACTS if "user-node-output" not in contract_id]
    assert len(generated) == len(CONTRACTS)
    for contract_id in gate1_ids:
        filename = f"{contract_id.replace('@', '-')}.schema.json"
        published = SCHEMAS / filename
        assert published.exists(), contract_id
        assert json.loads(published.read_text()) == json.loads((tmp_path / filename).read_text())


def test_contract_models_expose_no_execution_or_persistence_authority() -> None:
    forbidden = {"save", "commit", "execute", "transition", "route", "authorize"}
    for model in CONTRACTS.values():
        assert forbidden.isdisjoint(dir(model))
