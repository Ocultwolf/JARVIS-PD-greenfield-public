import json
from datetime import datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy import Connection, text

from jarvis_pd.contracts.gate1 import AgentInvocation, AgentResult, AgentResultStatus
from jarvis_pd.loop.application.integrity_service import IntegrityService
from jarvis_pd.shared.errors import DomainError, ErrorCode


class AgentRuntimePort(Protocol):
    def invoke(self, invocation: AgentInvocation) -> AgentResult: ...


class Clock(Protocol):
    def now(self) -> datetime: ...


class NodeRuntimeService:
    """Validates WP1 context and owns guarded persistence around a powerless adapter."""

    def __init__(self, uow_factory, runtime: AgentRuntimePort, clock: Clock) -> None:
        self.uow_factory = uow_factory
        self.runtime = runtime
        self.clock = clock

    @staticmethod
    def _request(invocation: AgentInvocation) -> dict:
        return invocation.model_dump(mode="json")

    def _require_current_lease(
        self,
        conn: Connection,
        *,
        run_id: UUID,
        owner_id: str,
        fencing_token: int,
    ) -> None:
        row = (
            conn.execute(
                text(
                    "SELECT r.state,l.owner_id,l.fencing_token,l.state AS lease_state,"
                    "l.expires_at FROM runs r JOIN leases l ON l.resource_type='Task' "
                    "AND l.resource_id=r.task_id WHERE r.id=:run FOR UPDATE OF r,l"
                ),
                {"run": run_id},
            )
            .mappings()
            .first()
        )
        if row is None or row["lease_state"] != "ACTIVE" or row["expires_at"] <= self.clock.now():
            raise DomainError(ErrorCode.LEASE_REQUIRED, "A current Task lease is required")
        if row["owner_id"] != owner_id or row["fencing_token"] != fencing_token:
            raise DomainError(ErrorCode.FENCING_REJECTED, "Lease owner or token is stale")
        if row["state"] != "RUNNING":
            raise DomainError(ErrorCode.PRECONDITION_FAILED, "Run is not RUNNING")

    def _load_prepared_invocation(self, conn: Connection, invocation: AgentInvocation) -> dict:
        collision = (
            conn.execute(
                text(
                    "SELECT id,status,request,result FROM node_invocations "
                    "WHERE idempotency_key=:key FOR UPDATE"
                ),
                {"key": invocation.idempotency_key},
            )
            .mappings()
            .first()
        )
        request = self._request(invocation)
        if collision is not None:
            if collision["id"] != invocation.invocation_id:
                raise DomainError(
                    ErrorCode.IDEMPOTENCY_CONFLICT,
                    "Idempotency key reused for a different invocation",
                )
            if collision["status"] != "CREATED" and collision["request"] != request:
                raise DomainError(
                    ErrorCode.IDEMPOTENCY_CONFLICT,
                    "Idempotency key reused with a different AgentInvocation",
                )
            if collision["result"] is not None:
                return {"replay": AgentResult.model_validate(collision["result"])}

        row = (
            conn.execute(
                text(
                    "SELECT ni.id,ni.status,ni.run_id,ni.node_run_id,ni.context_manifest_id,"
                    "ni.expected_output_contract,ir.id AS receipt_id,ir.status AS receipt_status "
                    "FROM node_invocations ni "
                    "JOIN injection_receipts ir ON ir.id=:receipt "
                    "JOIN context_manifests cm ON cm.id=ir.context_manifest_id "
                    "WHERE ni.id=:invocation AND ni.run_id=:run AND ni.node_run_id=:node_run "
                    "AND ni.expected_output_contract=:contract AND ni.status='CREATED' "
                    "AND ir.invocation_id=ni.id AND ir.status='CONFIRMED' "
                    "AND ir.context_manifest_id=ni.context_manifest_id "
                    "AND ir.manifest_invocation_id=ni.id "
                    "AND ir.manifest_context_manifest_id=cm.id "
                    "AND ir.manifest_session_id=cm.session_id "
                    "AND ir.session_id=cm.session_id "
                    "AND ir.manifest_transport=cm.transport "
                    "AND ir.transport=cm.transport "
                    "AND ir.manifest_hash=cm.content_hash "
                    "AND ir.injected_hash=cm.content_hash "
                    "AND cm.invocation_id=ni.id AND cm.status='SEALED'"
                ),
                {
                    "receipt": invocation.context_injection_id,
                    "invocation": invocation.invocation_id,
                    "run": invocation.run_id,
                    "node_run": invocation.node_run_id,
                    "contract": invocation.expected_output_contract,
                },
            )
            .mappings()
            .first()
        )
        if row is None:
            raise DomainError(
                ErrorCode.PRECONDITION_FAILED,
                "A CONFIRMED InjectionReceipt with exact WP1 correlation is required",
            )
        return dict(row)

    def invoke(
        self,
        invocation: AgentInvocation,
        *,
        owner_id: str,
        fencing_token: int,
        request_id: UUID,
    ) -> AgentResult:
        request = self._request(invocation)
        with self.uow_factory() as uow:
            prepared = self._load_prepared_invocation(uow.conn, invocation)
            replay = prepared.get("replay")
            if replay is not None:
                return replay
            self._require_current_lease(
                uow.conn,
                run_id=invocation.run_id,
                owner_id=owner_id,
                fencing_token=fencing_token,
            )
            updated = uow.conn.execute(
                text(
                    "UPDATE node_invocations SET status='DISPATCHED',"
                    "request=CAST(:request AS jsonb) "
                    "WHERE id=:id AND status='CREATED' RETURNING id"
                ),
                {"request": json.dumps(request), "id": invocation.invocation_id},
            ).first()
            if updated is None:
                raise DomainError(ErrorCode.PRECONDITION_FAILED, "INVOCATION_NOT_PREPARED")

        result = self.runtime.invoke(invocation)
        if (
            result.invocation_id != invocation.invocation_id
            or result.node_run_id != invocation.node_run_id
        ):
            raise DomainError(ErrorCode.PRECONDITION_FAILED, "RUNTIME_CORRELATION_MISMATCH")
        status = {
            AgentResultStatus.SUCCEEDED: "SUCCEEDED",
            AgentResultStatus.FAILED: "FAILED",
            AgentResultStatus.UNAVAILABLE: "UNAVAILABLE",
            AgentResultStatus.INVALID_OUTPUT: "INVALID_OUTPUT",
        }[result.status]
        with self.uow_factory() as uow:
            self._require_current_lease(
                uow.conn,
                run_id=invocation.run_id,
                owner_id=owner_id,
                fencing_token=fencing_token,
            )
            updated = uow.conn.execute(
                text(
                    "UPDATE node_invocations SET status=:status,result=CAST(:result AS jsonb),"
                    "completed_at=:completed WHERE id=:id AND status='DISPATCHED' RETURNING id"
                ),
                {
                    "status": status,
                    "result": json.dumps(result.model_dump(mode="json")),
                    "completed": result.completed_at,
                    "id": invocation.invocation_id,
                },
            ).first()
            if updated is None:
                raise DomainError(ErrorCode.PRECONDITION_FAILED, "INVOCATION_NOT_DISPATCHED")
            IntegrityService._audit(
                uow.conn,
                request_id=request_id,
                actor_id=owner_id,
                command_type="internal.node-invocation.complete",
                aggregate_type="NodeInvocation",
                aggregate_id=invocation.invocation_id,
                previous_state="DISPATCHED",
                new_state=status,
                details={"run_id": str(invocation.run_id), "fencing_token": fencing_token},
            )
            if result.status != AgentResultStatus.SUCCEEDED:
                uow.conn.execute(
                    text(
                        "UPDATE runs SET state='BLOCKED',revision=revision+1,updated_at=now() "
                        "WHERE id=:run AND state='RUNNING'"
                    ),
                    {"run": invocation.run_id},
                )
                payload = {
                    "run_id": str(invocation.run_id),
                    "invocation_id": str(invocation.invocation_id),
                    "reason_code": result.error_code,
                }
                IntegrityService._outbox(
                    uow.conn,
                    "internal.node-runtime.blocked",
                    "Run",
                    invocation.run_id,
                    payload,
                )
                IntegrityService._audit(
                    uow.conn,
                    request_id=request_id,
                    actor_id=owner_id,
                    command_type="internal.node-runtime.block",
                    aggregate_type="Run",
                    aggregate_id=invocation.run_id,
                    previous_state="RUNNING",
                    new_state="BLOCKED",
                    details={"fencing_token": fencing_token, "reason_code": result.error_code},
                )
        return result
