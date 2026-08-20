import json
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from jarvis_pd.contracts.gate1 import (
    AgentInvocation,
    AgentResult,
    ApprovalDecision,
    ApprovalRequest,
    CompensationRequest,
    ContextManifest,
    EffectReceipt,
    Gate1LatchConfig,
    Gate1Limits,
    InjectionReceipt,
    NodeInvocationRequest,
    PlannerProposal,
    ReconciliationResult,
    ToolCallRequest,
)
from jarvis_pd.contracts.user_node import UserNodeProposal


class ContractRejectionCode(StrEnum):
    SCHEMA_VALIDATION_FAILED = "SCHEMA_VALIDATION_FAILED"
    UNSUPPORTED_SCHEMA_VERSION = "UNSUPPORTED_SCHEMA_VERSION"


class ContractValidationError(ValueError):
    def __init__(self, code: ContractRejectionCode, contract_id: str) -> None:
        self.code = code
        self.contract_id = contract_id
        super().__init__(f"{code}: {contract_id}")


CONTRACTS: dict[str, type[BaseModel]] = {
    "jarvis.user-node-output@1.0.0": UserNodeProposal,
    "jarvis.node-invocation-request@1.0.0": NodeInvocationRequest,
    "jarvis.agent-invocation@1.0.0": AgentInvocation,
    "jarvis.agent-result@1.0.0": AgentResult,
    "jarvis.planner-proposal@1.0.0": PlannerProposal,
    "jarvis.context-manifest@1.0.0": ContextManifest,
    "jarvis.injection-receipt@1.0.0": InjectionReceipt,
    "jarvis.tool-call-request@1.0.0": ToolCallRequest,
    "jarvis.effect-receipt@1.0.0": EffectReceipt,
    "jarvis.reconciliation-result@1.0.0": ReconciliationResult,
    "jarvis.compensation-request@1.0.0": CompensationRequest,
    "jarvis.approval-request@1.0.0": ApprovalRequest,
    "jarvis.approval-decision@1.0.0": ApprovalDecision,
    "jarvis.gate1-limits@1.0.0": Gate1Limits,
    "jarvis.gate1-latch-config@1.0.0": Gate1LatchConfig,
}


def validate_contract(contract_id: str, payload: str | bytes | dict[str, Any]) -> BaseModel:
    model = CONTRACTS.get(contract_id)
    if model is None:
        raise ContractValidationError(ContractRejectionCode.UNSUPPORTED_SCHEMA_VERSION, contract_id)
    try:
        if isinstance(payload, (str, bytes)):
            return model.model_validate_json(payload)
        return model.model_validate(payload)
    except ValidationError as exc:
        raise ContractValidationError(
            ContractRejectionCode.SCHEMA_VALIDATION_FAILED, contract_id
        ) from exc


def export_json_schemas(target: Path) -> list[Path]:
    target.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for contract_id, model in CONTRACTS.items():
        path = target / f"{contract_id.replace('@', '-')}.schema.json"
        path.write_text(json.dumps(model.model_json_schema(), indent=2), encoding="utf-8")
        written.append(path)
    return written
