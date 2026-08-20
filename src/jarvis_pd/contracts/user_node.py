from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProposalKind(StrEnum):
    SELECT_TASK = "SELECT_TASK"
    PROPOSE_TASK = "PROPOSE_TASK"
    REPORT_BLOCKER = "REPORT_BLOCKER"
    REQUEST_ESCALATION = "REQUEST_ESCALATION"
    NO_ACTION = "NO_ACTION"


class RejectionReason(StrEnum):
    INSUFFICIENT_CONTEXT = "INSUFFICIENT_CONTEXT"
    NO_ELIGIBLE_TASK = "NO_ELIGIBLE_TASK"
    AUTHORITY_REQUIRED = "AUTHORITY_REQUIRED"
    CONTRACT_UNSUPPORTED = "CONTRACT_UNSUPPORTED"
    UNSAFE_OR_AMBIGUOUS = "UNSAFE_OR_AMBIGUOUS"


class ProposedTask(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: Annotated[str, Field(min_length=1, max_length=200)]
    acceptance_criteria: Annotated[list[str], Field(min_length=1, max_length=10)]
    rationale: Annotated[str, Field(min_length=1, max_length=1000)]


class UserNodeProposal(BaseModel):
    """Untrusted proposal. It deliberately exposes no persistence methods."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    contract: Literal["jarvis.user-node-output"]
    schema_version: Literal["1.0.0"]
    proposal_id: UUID
    run_id: UUID
    kind: ProposalKind
    selected_task_id: UUID | None = None
    proposed_tasks: Annotated[list[ProposedTask], Field(default_factory=list, max_length=5)]
    rejection_reason: RejectionReason | None = None
    explanation: Annotated[str, Field(min_length=1, max_length=2000)]

    @model_validator(mode="after")
    def validate_kind_payload(self) -> "UserNodeProposal":
        if self.kind == ProposalKind.SELECT_TASK and self.selected_task_id is None:
            raise ValueError("SELECT_TASK requires selected_task_id")
        if self.kind == ProposalKind.PROPOSE_TASK and not self.proposed_tasks:
            raise ValueError("PROPOSE_TASK requires proposed_tasks")
        if self.kind == ProposalKind.NO_ACTION and self.rejection_reason is None:
            raise ValueError("NO_ACTION requires rejection_reason")
        if self.kind != ProposalKind.SELECT_TASK and self.selected_task_id is not None:
            raise ValueError("selected_task_id is only valid for SELECT_TASK")
        return self
