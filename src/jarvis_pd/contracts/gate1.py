from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

Sha256 = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
NonEmpty = Annotated[str, Field(min_length=1, max_length=500)]
ContractReference = Annotated[str, Field(pattern=r"^[a-z0-9._-]+@[0-9]+\.[0-9]+\.[0-9]+$")]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SessionStrategy(StrEnum):
    FRESH_SESSION = "FRESH_SESSION"
    VERIFIED_SESSION_UPDATE = "VERIFIED_SESSION_UPDATE"
    SESSION_RECREATE = "SESSION_RECREATE"


class SessionBinding(ContractModel):
    strategy: SessionStrategy
    workspace_id: NonEmpty
    session_id: str | None = None


class InvocationConstraints(ContractModel):
    deadline: datetime
    max_output_bytes: Annotated[int, Field(ge=1, le=1_048_576)]
    allowed_response_contract: ContractReference


class NodeInvocationRequest(ContractModel):
    contract: Literal["jarvis.node-invocation-request"]
    schema_version: Literal["1.0.0"]
    invocation_id: UUID
    node_run_id: UUID
    run_id: UUID
    node_id: NonEmpty
    capability: NonEmpty
    input_refs: Annotated[list[str], Field(min_length=1, max_length=32)]
    context_manifest_id: UUID
    session_binding: SessionBinding
    constraints: InvocationConstraints


class AgentInvocation(ContractModel):
    contract: Literal["jarvis.agent-invocation"]
    schema_version: Literal["1.0.0"]
    invocation_id: UUID
    agent_id: NonEmpty
    node_run_id: UUID
    run_id: UUID
    message: Annotated[str, Field(min_length=1, max_length=65_536)]
    context_injection_id: UUID
    expected_output_contract: ContractReference
    timeout_seconds: Literal[60]
    session_policy: Literal["FRESH_OR_VERIFIED"]
    idempotency_key: Annotated[str, Field(min_length=1, max_length=200)]


class AgentResultStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNAVAILABLE = "UNAVAILABLE"
    INVALID_OUTPUT = "INVALID_OUTPUT"


class AgentResult(ContractModel):
    contract: Literal["jarvis.agent-result"]
    schema_version: Literal["1.0.0"]
    invocation_id: UUID
    node_run_id: UUID
    status: AgentResultStatus
    requested_contract: ContractReference
    raw_output: str | None = None
    structured_output: dict[str, Any] | None = None
    output_hash: Sha256 | None = None
    session_id: str | None = None
    started_at: datetime
    completed_at: datetime
    input_tokens: Annotated[int, Field(ge=0)] | None = None
    output_tokens: Annotated[int, Field(ge=0)] | None = None
    error_code: str | None = None
    evidence_refs: Annotated[list[str], Field(default_factory=list, max_length=32)]

    @model_validator(mode="after")
    def validate_result(self) -> "AgentResult":
        if self.completed_at < self.started_at:
            raise ValueError("completed_at cannot precede started_at")
        if self.status == AgentResultStatus.SUCCEEDED:
            if self.structured_output is None or self.output_hash is None:
                raise ValueError("SUCCEEDED requires structured_output and output_hash")
            if self.error_code is not None:
                raise ValueError("SUCCEEDED cannot include error_code")
        elif self.structured_output is not None:
            raise ValueError("non-success result cannot confirm structured_output")
        elif self.error_code is None:
            raise ValueError("non-success result requires error_code")
        return self


class RiskClass(StrEnum):
    R0 = "R0"
    R1 = "R1"


class PlannerStep(ContractModel):
    step_id: UUID
    intent: NonEmpty
    required_capabilities: Annotated[list[str], Field(min_length=1, max_length=16)]
    dependencies: Annotated[list[UUID], Field(default_factory=list, max_length=16)]
    expected_evidence: Annotated[list[str], Field(min_length=1, max_length=16)]
    risk_class: RiskClass


class PlannerProposal(ContractModel):
    contract: Literal["jarvis.planner-proposal"]
    schema_version: Literal["1.0.0"]
    proposal_id: UUID
    run_id: UUID
    goal_id: UUID
    assumptions: Annotated[list[str], Field(default_factory=list, max_length=16)]
    steps: Annotated[list[PlannerStep], Field(min_length=1, max_length=12)]
    open_questions: Annotated[list[str], Field(default_factory=list, max_length=16)]
    requested_approval_ids: Annotated[list[UUID], Field(default_factory=list, max_length=8)]
    completion_criteria: Annotated[list[str], Field(min_length=1, max_length=16)]
    explanation: Annotated[str, Field(min_length=1, max_length=2000)]


class ContextItem(ContractModel):
    ref: NonEmpty
    version: NonEmpty
    content_hash: Sha256
    reason: NonEmpty


class ExcludedContextItem(ContractModel):
    ref: NonEmpty
    reason_code: Literal[
        "NOT_REQUIRED",
        "SENSITIVITY_DENIED",
        "BUDGET_EXCEEDED",
        "UNTRUSTED_SOURCE",
        "EXPIRED",
    ]


class TokenBudget(ContractModel):
    max_tokens: Annotated[int, Field(ge=1)]
    used_tokens: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def validate_usage(self) -> "TokenBudget":
        if self.used_tokens > self.max_tokens:
            raise ValueError("used_tokens cannot exceed max_tokens")
        return self


class ContextManifest(ContractModel):
    contract: Literal["jarvis.context-manifest"]
    schema_version: Literal["1.0.0"]
    context_manifest_id: UUID
    invocation_id: UUID
    session_id: NonEmpty
    transport: NonEmpty
    consumer_id: NonEmpty
    purpose: NonEmpty
    knowledge_versions: Annotated[list[str], Field(default_factory=list, max_length=32)]
    policy_version: NonEmpty
    included_items: Annotated[list[ContextItem], Field(max_length=64)]
    excluded_items: Annotated[list[ExcludedContextItem], Field(max_length=64)]
    budget: TokenBudget
    sensitivity: Literal["INTERNAL"]
    content_hash: Sha256
    status: Literal["SEALED"]
    sealed_at: datetime


class InjectionStatus(StrEnum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class InjectionReceipt(ContractModel):
    contract: Literal["jarvis.injection-receipt"]
    schema_version: Literal["1.0.0"]
    injection_id: UUID
    invocation_id: UUID
    context_manifest_id: UUID
    manifest_invocation_id: UUID
    manifest_context_manifest_id: UUID
    manifest_session_id: NonEmpty
    manifest_transport: NonEmpty
    status: InjectionStatus
    strategy: SessionStrategy
    transport: NonEmpty
    session_id: str | None = None
    manifest_hash: Sha256
    injected_hash: Sha256 | None = None
    injected_at: datetime
    verification_method: str | None = None
    error_code: str | None = None

    @model_validator(mode="after")
    def validate_confirmation(self) -> "InjectionReceipt":
        if self.status == InjectionStatus.CONFIRMED:
            if not self.session_id or not self.verification_method:
                raise ValueError("CONFIRMED requires session and verification method")
            if self.injected_hash != self.manifest_hash:
                raise ValueError("CONFIRMED requires matching manifest and injected hashes")
            if self.manifest_invocation_id != self.invocation_id:
                raise ValueError("CONFIRMED requires the manifest invocation")
            if self.manifest_context_manifest_id != self.context_manifest_id:
                raise ValueError("CONFIRMED requires the exact context manifest")
            if self.manifest_session_id != self.session_id:
                raise ValueError("CONFIRMED requires the manifest session")
            if self.manifest_transport != self.transport:
                raise ValueError("CONFIRMED requires the manifest transport")
            if self.error_code is not None:
                raise ValueError("CONFIRMED cannot include error_code")
        elif self.error_code is None and self.status in {
            InjectionStatus.FAILED,
            InjectionStatus.UNKNOWN,
        }:
            raise ValueError("FAILED or UNKNOWN requires error_code")
        return self


class SandboxLimits(ContractModel):
    network_enabled: Literal[False]
    allowed_operations: Annotated[
        list[Literal["fixture.atomic_write"]], Field(min_length=1, max_length=1)
    ]
    max_files: Literal[1]
    max_bytes: Annotated[int, Field(ge=1, le=1_048_576)]
    timeout_seconds: Literal[30]


class ToolCallRequest(ContractModel):
    contract: Literal["jarvis.tool-call-request"]
    schema_version: Literal["1.0.0"]
    tool_call_id: UUID
    operation_id: UUID
    action_id: UUID
    run_id: UUID
    tool_id: Literal["fixture.atomic_write"]
    actor_id: NonEmpty
    target_relative_path: Annotated[str, Field(min_length=1, max_length=255)]
    content_hash: Sha256
    operation_hash: Sha256
    idempotency_key: Annotated[str, Field(min_length=1, max_length=200)]
    approval_id: UUID | None = None
    risk_class: Literal["R1"]
    effect_class: Literal["COMPENSATABLE"]
    sandbox: SandboxLimits

    @model_validator(mode="after")
    def validate_relative_path(self) -> "ToolCallRequest":
        raw = self.target_relative_path
        path = PurePosixPath(raw)
        if path.is_absolute() or ".." in path.parts or raw.startswith("/"):
            raise ValueError("target_relative_path must remain below sandbox root")
        if any(part in {"", "."} for part in path.parts):
            raise ValueError("target_relative_path must be normalized")
        return self


class ToolCallOutcome(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"


class EffectReceipt(ContractModel):
    contract: Literal["jarvis.effect-receipt"]
    schema_version: Literal["1.0.0"]
    receipt_id: UUID
    tool_call_id: UUID
    operation_id: UUID
    outcome: ToolCallOutcome
    previous_hash: Sha256 | None = None
    resulting_hash: Sha256 | None = None
    verified: bool
    observed_at: datetime
    evidence_refs: Annotated[list[NonEmpty], Field(min_length=1, max_length=32)]
    error_code: str | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> "EffectReceipt":
        if self.outcome == ToolCallOutcome.SUCCEEDED:
            if not self.verified or self.resulting_hash is None:
                raise ValueError("SUCCEEDED requires verified resulting_hash")
            if self.error_code is not None:
                raise ValueError("SUCCEEDED cannot include error_code")
        elif self.outcome == ToolCallOutcome.OUTCOME_UNKNOWN and self.verified:
            raise ValueError("OUTCOME_UNKNOWN cannot be verified")
        elif self.error_code is None:
            raise ValueError("FAILED or OUTCOME_UNKNOWN requires error_code")
        return self


class ReconciliationOutcome(StrEnum):
    NO_EFFECT = "NO_EFFECT"
    CONFIRMED_SUCCEEDED = "CONFIRMED_SUCCEEDED"
    CONFIRMED_FAILED = "CONFIRMED_FAILED"
    UNRESOLVED = "UNRESOLVED"


class ReconciliationResult(ContractModel):
    contract: Literal["jarvis.reconciliation-result"]
    schema_version: Literal["1.0.0"]
    reconciliation_id: UUID
    tool_call_id: UUID
    operation_id: UUID
    outcome: ReconciliationOutcome
    observed_hash: Sha256 | None = None
    reconciled_at: datetime
    evidence_refs: Annotated[list[str], Field(min_length=1, max_length=32)]
    reason_code: NonEmpty


class CompensationRequest(ContractModel):
    contract: Literal["jarvis.compensation-request"]
    schema_version: Literal["1.0.0"]
    compensation_id: UUID
    original_tool_call_id: UUID
    operation_id: UUID
    operation_hash: Sha256
    actor_id: NonEmpty
    idempotency_key: Annotated[str, Field(min_length=1, max_length=200)]
    reason: NonEmpty
    expected_result_hash: Sha256


class ApprovalRequest(ContractModel):
    contract: Literal["jarvis.approval-request"]
    schema_version: Literal["1.0.0"]
    approval_request_id: UUID
    operation_id: UUID
    operation_hash: Sha256
    requested_from: NonEmpty
    requested_at: datetime
    expires_at: datetime
    max_uses: Annotated[int, Field(ge=1, le=10)]
    status: Literal["PENDING"]

    @model_validator(mode="after")
    def validate_expiry(self) -> "ApprovalRequest":
        if self.expires_at <= self.requested_at:
            raise ValueError("expires_at must be after requested_at")
        return self


class ApprovalDecision(ContractModel):
    contract: Literal["jarvis.approval-decision"]
    schema_version: Literal["1.0.0"]
    approval_request_id: UUID
    operation_hash: Sha256
    decision: Literal["APPROVED", "DENIED"]
    decided_by: NonEmpty
    decided_at: datetime
    decision_reason: NonEmpty


class Gate1Limits(ContractModel):
    contract: Literal["jarvis.gate1-limits"]
    schema_version: Literal["1.0.0"]
    max_concurrent_runs: Literal[1]
    max_actions_per_run: Literal[1]
    max_tool_calls_per_action: Literal[1]
    max_node_attempts: Literal[2]
    max_graph_transitions: Literal[12]
    run_timeout_seconds: Literal[300]
    node_timeout_seconds: Literal[60]
    tool_timeout_seconds: Literal[30]
    model_id: None = None
    token_budget: None = None


class Gate1LatchConfig(ContractModel):
    contract: Literal["jarvis.gate1-latch-config"]
    schema_version: Literal["1.0.0"]
    config_id: Literal["gate1-latch"]
    host_latch_id: NonEmpty
    persistence: Literal["HOST_PERSISTENT"]
    postgresql_role: Literal["PROJECTION_AND_AUDIT_ONLY"]
    independent_of_llm: Literal[True]
    independent_of_langgraph: Literal[True]
    independent_of_openclaw: Literal[True]
    guard_before_work_acquisition: Literal[True]
    guard_before_effect: Literal[True]
    health_recovery_releases_latch: Literal[False]
    production_release: Literal["DEFERRED_NOT_IMPLEMENTED"]
