from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from jarvis_pd.contracts.gate1 import AgentInvocation, AgentResult, AgentResultStatus
from jarvis_pd.contracts.registry import ContractValidationError, validate_contract


@dataclass(frozen=True, slots=True)
class FakeRuntimeResponse:
    payload: dict[str, Any] | None = None
    unavailable_code: str | None = None


class FakeAgentRuntime:
    """Contract-equivalent fake; it has no canonical database authority."""

    def __init__(
        self,
        responder: Callable[[AgentInvocation, int], FakeRuntimeResponse],
        clock: Callable[[], datetime],
    ) -> None:
        self._responder = responder
        self._clock = clock

    def invoke(self, invocation: AgentInvocation) -> AgentResult:
        started = self._clock()
        for attempt in (1, 2):
            response = self._responder(invocation, attempt)
            if response.unavailable_code is not None:
                return self._failure(
                    invocation, started, AgentResultStatus.UNAVAILABLE, response.unavailable_code
                )
            try:
                validated = validate_contract(
                    invocation.expected_output_contract, response.payload or {}
                )
            except ContractValidationError:
                if attempt == 1:
                    continue
                return self._failure(
                    invocation,
                    started,
                    AgentResultStatus.INVALID_OUTPUT,
                    "CONTRACT_REPAIR_EXHAUSTED",
                )
            payload = validated.model_dump(mode="json")
            return AgentResult(
                contract="jarvis.agent-result",
                schema_version="1.0.0",
                invocation_id=invocation.invocation_id,
                node_run_id=invocation.node_run_id,
                status=AgentResultStatus.SUCCEEDED,
                requested_contract=invocation.expected_output_contract,
                raw_output=None,
                structured_output=payload,
                output_hash=_payload_hash(validated.model_dump_json()),
                session_id=None,
                started_at=started,
                completed_at=self._clock(),
                evidence_refs=[],
            )
        raise AssertionError("bounded attempt loop must return")

    def _failure(
        self,
        invocation: AgentInvocation,
        started: datetime,
        status: AgentResultStatus,
        code: str,
    ) -> AgentResult:
        return AgentResult(
            contract="jarvis.agent-result",
            schema_version="1.0.0",
            invocation_id=invocation.invocation_id,
            node_run_id=invocation.node_run_id,
            status=status,
            requested_contract=invocation.expected_output_contract,
            started_at=started,
            completed_at=self._clock(),
            error_code=code,
            evidence_refs=[],
        )


def _payload_hash(value: str) -> str:
    import hashlib

    return f"sha256:{hashlib.sha256(value.encode()).hexdigest()}"
