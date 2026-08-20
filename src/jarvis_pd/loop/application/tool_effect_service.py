import hashlib
import json
from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, text

from jarvis_pd.contracts.gate1 import EffectReceipt, ToolCallOutcome, ToolCallRequest
from jarvis_pd.loop.application.effect_coordination import wp3_effect_lock
from jarvis_pd.loop.application.fixture_atomic_write import (
    AmbiguousEffectError,
    ConfirmedNoEffectError,
    FixtureAtomicWrite,
)
from jarvis_pd.loop.application.integrity_service import IntegrityService
from jarvis_pd.loop.application.policy import Gate1PolicyEvaluator, PolicyInput
from jarvis_pd.shared.errors import DomainError, ErrorCode


def operation_hash(request: ToolCallRequest) -> str:
    payload = request.model_dump(mode="json", exclude={"operation_hash"})
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


class ToolEffectService:
    """Deterministic PEP and canonical writer for the only Gate 1 Tool."""

    def __init__(
        self,
        uow_factory,
        clock,
        adapter: FixtureAtomicWrite,
        latch_active: Callable[[], bool],
    ) -> None:
        self.uow_factory = uow_factory
        self.clock = clock
        self.adapter = adapter
        self.latch_active = latch_active
        self.policy = Gate1PolicyEvaluator()

    def _canonical(self, conn: Connection, request: ToolCallRequest) -> dict[str, Any]:
        row = (
            conn.execute(
                text(
                    "SELECT r.state AS run_state,r.fencing_token,r.task_id,a.state AS action_state,"
                    "t.goal_id,l.owner_id,l.fencing_token AS lease_token,l.state AS lease_state,"
                    "l.expires_at FROM runs r JOIN actions a ON a.run_id=r.id "
                    "JOIN tasks t ON t.id=r.task_id JOIN leases l ON l.resource_type='Task' "
                    "AND l.resource_id=r.task_id WHERE r.id=:run AND a.id=:action FOR UPDATE OF r,a,l"
                ),
                {"run": request.run_id, "action": request.action_id},
            )
            .mappings()
            .first()
        )
        if row is None:
            raise DomainError(ErrorCode.PRECONDITION_FAILED, "Run/Action mismatch")
        assert row is not None
        return dict(row)

    def _require_lease(self, row: dict[str, Any], *, owner_id: str, fencing_token: int) -> None:
        if row["lease_state"] != "ACTIVE" or row["expires_at"] <= self.clock.now():
            raise DomainError(ErrorCode.LEASE_REQUIRED, "Lease is not active")
        if (
            row["owner_id"] != owner_id
            or row["lease_token"] != fencing_token
            or row["fencing_token"] != fencing_token
        ):
            raise DomainError(ErrorCode.FENCING_REJECTED, "Owner or fencing token is stale")

    def _policy(
        self,
        row: dict[str, Any],
        request: ToolCallRequest,
        *,
        owner_id: str,
        authorized_goal_id: UUID,
        approval_exact: bool,
    ) -> None:
        decision = self.policy.evaluate(
            PolicyInput(
                identity_allowed=request.actor_id == owner_id,
                goal_scope_allowed=row["goal_id"] == authorized_goal_id,
                operation_allowlisted=request.tool_id == "fixture.atomic_write",
                exact_resource_allowed=bool(request.target_relative_path),
                risk_class=request.risk_class,
                within_limits=(
                    request.sandbox.max_files == 1
                    and request.sandbox.max_bytes <= 1_048_576
                    and request.sandbox.timeout_seconds == 30
                ),
                approval_required=True,
                approval_exact=approval_exact,
                latch_active=self.latch_active(),
                outcome_unknown=row["run_state"] == "RECONCILIATION_REQUIRED",
            )
        )
        if not decision.allowed:
            raise DomainError(ErrorCode.UNAUTHORIZED, decision.reason_code)

    def _approval(
        self, conn: Connection, request: ToolCallRequest, *, consume: bool
    ) -> dict[str, Any]:
        if request.approval_id is None:
            raise DomainError(ErrorCode.APPROVAL_REQUIRED, "Exact approval is required")
        row = (
            conn.execute(
                text(
                    "SELECT id,operation_id,operation_hash,state,expires_at,max_uses,uses_consumed "
                    "FROM approvals WHERE id=:id FOR UPDATE"
                ),
                {"id": request.approval_id},
            )
            .mappings()
            .first()
        )
        exact = bool(
            row
            and row["operation_id"] == request.operation_id
            and row["operation_hash"] == request.operation_hash
            and row["state"] == "APPROVED"
            and row["expires_at"] is not None
            and row["expires_at"] > self.clock.now()
            and row["uses_consumed"] < row["max_uses"] == 1
        )
        if not exact:
            raise DomainError(
                ErrorCode.APPROVAL_MISMATCH, "Approval is absent, stale or mismatched"
            )
        assert row is not None
        if consume:
            updated = conn.execute(
                text(
                    "UPDATE approvals SET uses_consumed=uses_consumed+1,state='CONSUMED' "
                    "WHERE id=:id AND state='APPROVED' AND uses_consumed=0 RETURNING id"
                ),
                {"id": request.approval_id},
            ).first()
            if updated is None:
                raise DomainError(ErrorCode.APPROVAL_MISMATCH, "Approval already consumed")
        return dict(row)

    @staticmethod
    def _request_json(request: ToolCallRequest) -> dict[str, Any]:
        return request.model_dump(mode="json")

    def execute(
        self,
        request: ToolCallRequest,
        content: bytes,
        *,
        owner_id: str,
        fencing_token: int,
        authorized_goal_id: UUID,
        request_id: UUID,
        before_pre_effect: Callable[[], None] | None = None,
        after_barrier: Callable[[], None] | None = None,
        ambiguous_after_write: bool = False,
    ) -> EffectReceipt:
        if FixtureAtomicWrite.digest(content) != request.content_hash:
            raise DomainError(ErrorCode.PRECONDITION_FAILED, "Content hash mismatch")
        if operation_hash(request) != request.operation_hash:
            raise DomainError(ErrorCode.PRECONDITION_FAILED, "Operation hash mismatch")
        document = self._request_json(request)
        with self.uow_factory() as uow:
            uow.conn.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                {"key": request.idempotency_key},
            )
            existing = (
                uow.conn.execute(
                    text(
                        "SELECT request,state,id FROM tool_calls WHERE actor_id=:actor AND idempotency_key=:key FOR UPDATE"
                    ),
                    {"actor": request.actor_id, "key": request.idempotency_key},
                )
                .mappings()
                .first()
            )
            if existing:
                if existing["request"] != document:
                    raise DomainError(
                        ErrorCode.IDEMPOTENCY_CONFLICT, "Key reused with different request"
                    )
                receipt = uow.conn.execute(
                    text("SELECT document FROM effect_receipts WHERE tool_call_id=:id"),
                    {"id": existing["id"]},
                ).scalar_one_or_none()
                if receipt is not None:
                    return EffectReceipt.model_validate(receipt)
                raise DomainError(ErrorCode.PRECONDITION_FAILED, "Effect already in progress")
            row = self._canonical(uow.conn, request)
            self._require_lease(row, owner_id=owner_id, fencing_token=fencing_token)
            self._approval(uow.conn, request, consume=False)
            self._policy(
                row,
                request,
                owner_id=owner_id,
                authorized_goal_id=authorized_goal_id,
                approval_exact=True,
            )
            if row["run_state"] != "RUNNING" or row["action_state"] != "AUTHORIZED":
                raise DomainError(ErrorCode.PRECONDITION_FAILED, "Run/Action is not executable")
            uow.conn.execute(
                text(
                    "INSERT INTO tool_calls(id,operation_id,action_id,run_id,tool_id,actor_id,state,"
                    "operation_hash,idempotency_key,request,approval_id,owner_id,fencing_token) VALUES "
                    "(:id,:operation,:action,:run,:tool,:actor,'CREATED',:hash,:key,CAST(:request AS jsonb),"
                    ":approval,:owner,:token)"
                ),
                {
                    "id": request.tool_call_id,
                    "operation": request.operation_id,
                    "action": request.action_id,
                    "run": request.run_id,
                    "tool": request.tool_id,
                    "actor": request.actor_id,
                    "hash": request.operation_hash,
                    "key": request.idempotency_key,
                    "request": json.dumps(document),
                    "approval": request.approval_id,
                    "owner": owner_id,
                    "token": fencing_token,
                },
            )
        if before_pre_effect:
            before_pre_effect()
        with wp3_effect_lock(self.uow_factory, "tool", request.tool_call_id):
            return self._dispatch_reserved(
                request,
                content,
                owner_id=owner_id,
                fencing_token=fencing_token,
                authorized_goal_id=authorized_goal_id,
                request_id=request_id,
                after_barrier=after_barrier,
                ambiguous_after_write=ambiguous_after_write,
            )

    def _dispatch_reserved(
        self,
        request: ToolCallRequest,
        content: bytes,
        *,
        owner_id: str,
        fencing_token: int,
        authorized_goal_id: UUID,
        request_id: UUID,
        after_barrier: Callable[[], None] | None,
        ambiguous_after_write: bool,
    ) -> EffectReceipt:
        with self.uow_factory() as uow:
            row = self._canonical(uow.conn, request)
            self._require_lease(row, owner_id=owner_id, fencing_token=fencing_token)
            self._approval(uow.conn, request, consume=True)
            self._policy(
                row,
                request,
                owner_id=owner_id,
                authorized_goal_id=authorized_goal_id,
                approval_exact=True,
            )
            if row["run_state"] != "RUNNING" or row["action_state"] != "AUTHORIZED":
                raise DomainError(ErrorCode.PRECONDITION_FAILED, "Pre-effect state changed")
            # Arm the existing WP3 reconciliation state before crossing the
            # filesystem boundary.  This is not a receipt and asserts no
            # effect: it is the durable classification required if this
            # process dies or loses authority after dispatch.
            updated = uow.conn.execute(
                text(
                    "UPDATE tool_calls SET state='OUTCOME_UNKNOWN',previous_content=:previous,"
                    "resulting_content=:resulting,updated_at=now() "
                    "WHERE id=:id AND state='CREATED' RETURNING id"
                ),
                {
                    "id": request.tool_call_id,
                    "previous": self.adapter.observe(request.target_relative_path),
                    "resulting": content,
                },
            ).first()
            if updated is None:
                raise DomainError(ErrorCode.PRECONDITION_FAILED, "Tool call is not dispatchable")
            run_blocked = uow.conn.execute(
                text(
                    "UPDATE runs SET state='RECONCILIATION_REQUIRED',revision=revision+1,"
                    "updated_at=now() WHERE id=:id AND state='RUNNING' RETURNING id"
                ),
                {"id": request.run_id},
            ).first()
            if run_blocked is None:
                raise DomainError(
                    ErrorCode.PRECONDITION_FAILED,
                    "Run could not enter the reconciliation barrier",
                )
            IntegrityService._audit(
                uow.conn,
                request_id=request_id,
                actor_id=owner_id,
                command_type="internal.fixture.atomic-write.dispatch",
                aggregate_type="ToolCall",
                aggregate_id=request.tool_call_id,
                previous_state="CREATED",
                new_state="OUTCOME_UNKNOWN",
                approval_id=request.approval_id,
                details={"operation_hash": request.operation_hash, "fencing_token": fencing_token},
            )
        if after_barrier is not None:
            after_barrier()
        try:
            evidence = self.adapter.execute(
                request.target_relative_path,
                content,
                max_bytes=request.sandbox.max_bytes,
                ambiguous_after_write=ambiguous_after_write,
            )
        except AmbiguousEffectError as error:
            assert error.dispatched_content is not None
            return self._finish_unknown(
                request,
                owner_id,
                fencing_token,
                request_id,
                error.code,
                error.previous_content,
                error.dispatched_content,
            )
        except ConfirmedNoEffectError as error:
            return self._finish_failed(
                request, owner_id, fencing_token, request_id, error.code, error.previous_content
            )
        receipt = EffectReceipt(
            contract="jarvis.effect-receipt",
            schema_version="1.0.0",
            receipt_id=uuid4(),
            tool_call_id=request.tool_call_id,
            operation_id=request.operation_id,
            outcome=ToolCallOutcome.SUCCEEDED,
            previous_hash=(
                FixtureAtomicWrite.digest(evidence.previous_content)
                if evidence.previous_content is not None
                else None
            ),
            resulting_hash=evidence.resulting_hash,
            verified=True,
            observed_at=self.clock.now(),
            evidence_refs=[evidence.evidence_ref],
        )
        self._persist_receipt(
            request,
            receipt,
            owner_id,
            fencing_token,
            request_id,
            evidence.previous_content,
            content,
        )
        return receipt

    def _finish_failed(
        self,
        request: ToolCallRequest,
        owner: str,
        token: int,
        request_id: UUID,
        code: str,
        previous_content: bytes | None,
    ) -> EffectReceipt:
        receipt = EffectReceipt(
            contract="jarvis.effect-receipt",
            schema_version="1.0.0",
            receipt_id=uuid4(),
            tool_call_id=request.tool_call_id,
            operation_id=request.operation_id,
            outcome=ToolCallOutcome.FAILED,
            verified=False,
            observed_at=self.clock.now(),
            error_code=code,
            evidence_refs=["fixture:dispatch-failed"],
        )
        self._persist_receipt(request, receipt, owner, token, request_id, previous_content, None)
        return receipt

    def _finish_unknown(
        self,
        request: ToolCallRequest,
        owner: str,
        token: int,
        request_id: UUID,
        code: str,
        previous_content: bytes | None,
        dispatched_content: bytes,
    ) -> EffectReceipt:
        receipt = EffectReceipt(
            contract="jarvis.effect-receipt",
            schema_version="1.0.0",
            receipt_id=uuid4(),
            tool_call_id=request.tool_call_id,
            operation_id=request.operation_id,
            outcome=ToolCallOutcome.OUTCOME_UNKNOWN,
            verified=False,
            observed_at=self.clock.now(),
            error_code=code,
            evidence_refs=["fixture:response-lost"],
        )
        self._persist_receipt(
            request, receipt, owner, token, request_id, previous_content, dispatched_content
        )
        return receipt

    def _persist_receipt(
        self,
        request: ToolCallRequest,
        receipt: EffectReceipt,
        owner: str,
        token: int,
        request_id: UUID,
        previous_content: bytes | None,
        resulting_content: bytes | None,
    ) -> None:
        with self.uow_factory() as uow:
            row = self._canonical(uow.conn, request)
            self._require_lease(row, owner_id=owner, fencing_token=token)
            run_state = (
                "RECONCILIATION_REQUIRED"
                if receipt.outcome == ToolCallOutcome.OUTCOME_UNKNOWN
                else "RUNNING"
            )
            transitioned = uow.conn.execute(
                text(
                    "UPDATE tool_calls SET state=:state,previous_content=:previous,"
                    "resulting_content=:resulting,updated_at=now() "
                    "WHERE id=:id AND state='OUTCOME_UNKNOWN' RETURNING id"
                ),
                {
                    "state": receipt.outcome.value,
                    "previous": previous_content,
                    "resulting": resulting_content,
                    "id": request.tool_call_id,
                },
            ).first()
            if transitioned is None:
                raise DomainError(
                    ErrorCode.PRECONDITION_FAILED,
                    "Tool conclusion lost its canonical compare-and-set",
                )
            if receipt.outcome != ToolCallOutcome.OUTCOME_UNKNOWN:
                run_transition = uow.conn.execute(
                    text(
                        "UPDATE runs SET state='RUNNING',revision=revision+1,updated_at=now() "
                        "WHERE id=:id AND state='RECONCILIATION_REQUIRED' RETURNING id"
                    ),
                    {"id": request.run_id},
                ).first()
                if run_transition is None:
                    raise DomainError(
                        ErrorCode.PRECONDITION_FAILED,
                        "Run conclusion lost its canonical compare-and-set",
                    )
            uow.conn.execute(
                text(
                    "INSERT INTO effect_receipts(id,tool_call_id,outcome,previous_hash,resulting_hash,verified,document,observed_at,operation_id,owner_id,fencing_token,idempotency_key) VALUES (:id,:tool,:outcome,:previous,:resulting,:verified,CAST(:document AS jsonb),:observed,:operation,:owner,:token,:key)"
                ),
                {
                    "id": receipt.receipt_id,
                    "tool": request.tool_call_id,
                    "outcome": receipt.outcome.value,
                    "previous": receipt.previous_hash,
                    "resulting": receipt.resulting_hash,
                    "verified": receipt.verified,
                    "document": json.dumps(receipt.model_dump(mode="json")),
                    "observed": receipt.observed_at,
                    "operation": request.operation_id,
                    "owner": owner,
                    "token": token,
                    "key": request.idempotency_key,
                },
            )
            payload = {"tool_call_id": str(request.tool_call_id), "outcome": receipt.outcome.value}
            IntegrityService._outbox(
                uow.conn, "internal.tool-effect.recorded", "ToolCall", request.tool_call_id, payload
            )
            IntegrityService._audit(
                uow.conn,
                request_id=request_id,
                actor_id=owner,
                command_type="internal.fixture.atomic-write.result",
                aggregate_type="ToolCall",
                aggregate_id=request.tool_call_id,
                previous_state="OUTCOME_UNKNOWN",
                new_state=receipt.outcome.value,
                approval_id=request.approval_id,
                details={
                    "run_state": run_state,
                    "fencing_token": token,
                    "receipt_id": str(receipt.receipt_id),
                },
            )
