import json
from uuid import UUID

from sqlalchemy import text

from jarvis_pd.contracts.gate1 import ApprovalDecision, ApprovalRequest
from jarvis_pd.loop.application.integrity_service import IntegrityService
from jarvis_pd.shared.errors import DomainError, ErrorCode


class ApprovalService:
    """Canonical lifecycle for the exact, authenticated Gate 1 approval."""

    def __init__(self, uow_factory, clock) -> None:
        self.uow_factory = uow_factory
        self.clock = clock

    def request(self, request: ApprovalRequest, *, requested_by: str, request_id: UUID) -> None:
        if request.max_uses != 1:
            raise DomainError(ErrorCode.PRECONDITION_FAILED, "WP3 approval must be single-use")
        document = request.model_dump(mode="json")
        with self.uow_factory() as uow:
            uow.conn.execute(
                text(
                    "INSERT INTO approvals(id,operation_id,state,requested_from,expires_at,"
                    "operation_hash,max_uses,uses_consumed,requested_by,request_document) VALUES "
                    "(:id,:operation,'PENDING',:requested_from,:expires,:hash,1,0,:actor,"
                    "CAST(:document AS jsonb))"
                ),
                {
                    "id": request.approval_request_id,
                    "operation": request.operation_id,
                    "requested_from": request.requested_from,
                    "expires": request.expires_at,
                    "hash": request.operation_hash,
                    "actor": requested_by,
                    "document": json.dumps(document),
                },
            )
            IntegrityService._audit(
                uow.conn,
                request_id=request_id,
                actor_id=requested_by,
                command_type="internal.approval.request",
                aggregate_type="Approval",
                aggregate_id=request.approval_request_id,
                previous_state=None,
                new_state="PENDING",
                details={"operation_hash": request.operation_hash},
            )

    def decide(
        self,
        decision: ApprovalDecision,
        *,
        authenticated_actor_id: str,
        request_id: UUID,
    ) -> None:
        if authenticated_actor_id != decision.decided_by:
            raise DomainError(ErrorCode.UNAUTHORIZED, "Authenticated actor does not match decision")
        target = "APPROVED" if decision.decision == "APPROVED" else "DENIED"
        expired = False
        with self.uow_factory() as uow:
            row = (
                uow.conn.execute(
                    text(
                        "SELECT state,operation_hash,requested_from,expires_at FROM approvals "
                        "WHERE id=:id FOR UPDATE"
                    ),
                    {"id": decision.approval_request_id},
                )
                .mappings()
                .first()
            )
            if row is not None and row["state"] == "EXPIRED":
                expired = True
            elif row is None or row["state"] != "PENDING":
                raise DomainError(ErrorCode.APPROVAL_MISMATCH, "Approval is not pending")
            if expired:
                pass
            elif row["operation_hash"] != decision.operation_hash:
                raise DomainError(ErrorCode.APPROVAL_MISMATCH, "Operation hash changed")
            elif row["requested_from"] != authenticated_actor_id:
                raise DomainError(
                    ErrorCode.UNAUTHORIZED, "Decision actor is not requested approver"
                )
            elif row["expires_at"] <= self.clock.now():
                uow.conn.execute(
                    text(
                        "UPDATE approvals SET state='EXPIRED' WHERE id=:id AND state='PENDING'"
                    ),
                    {"id": decision.approval_request_id},
                )
                IntegrityService._outbox(
                    uow.conn,
                    "internal.approval.expired",
                    "Approval",
                    decision.approval_request_id,
                    {"approval_id": str(decision.approval_request_id), "state": "EXPIRED"},
                )
                IntegrityService._audit(
                    uow.conn,
                    request_id=request_id,
                    actor_id=authenticated_actor_id,
                    command_type="internal.approval.expire",
                    aggregate_type="Approval",
                    aggregate_id=decision.approval_request_id,
                    previous_state="PENDING",
                    new_state="EXPIRED",
                    details={"operation_hash": decision.operation_hash},
                )
                expired = True
            else:
                uow.conn.execute(
                    text(
                        "UPDATE approvals SET state=:state,decided_by=:actor,"
                        "decision_reason=:reason,decided_at=:decided,"
                        "decision_document=CAST(:document AS jsonb) WHERE id=:id"
                    ),
                    {
                        "state": target,
                        "actor": authenticated_actor_id,
                        "reason": decision.decision_reason,
                        "decided": decision.decided_at,
                        "document": json.dumps(decision.model_dump(mode="json")),
                        "id": decision.approval_request_id,
                    },
                )
                IntegrityService._audit(
                    uow.conn,
                    request_id=request_id,
                    actor_id=authenticated_actor_id,
                    command_type="internal.approval.decide",
                    aggregate_type="Approval",
                    aggregate_id=decision.approval_request_id,
                    previous_state="PENDING",
                    new_state=target,
                    details={"operation_hash": decision.operation_hash},
                )
        if expired:
            # The UoW has committed EXPIRED, audit and outbox before returning
            # the structured error to the caller.
            raise DomainError(ErrorCode.APPROVAL_MISMATCH, "Approval expired")
