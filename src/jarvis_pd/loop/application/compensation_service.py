import hashlib
import json
from collections.abc import Callable
from uuid import UUID, uuid4

from sqlalchemy import text

from jarvis_pd.contracts.gate1 import CompensationRequest
from jarvis_pd.loop.application.effect_coordination import wp3_effect_lock
from jarvis_pd.loop.application.fixture_atomic_write import (
    AmbiguousEffectError,
    ConfirmedNoEffectError,
    FixtureAtomicWrite,
)
from jarvis_pd.loop.application.integrity_service import IntegrityService
from jarvis_pd.shared.errors import DomainError, ErrorCode


def compensation_hash(request: CompensationRequest) -> str:
    value = request.model_dump(mode="json", exclude={"operation_hash"})
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


class FixtureCompensationService:
    """Separate, approved restoration of the exact prior fixture state."""

    def __init__(self, uow_factory, clock, adapter: FixtureAtomicWrite) -> None:
        self.uow_factory = uow_factory
        self.clock = clock
        self.adapter = adapter

    def compensate(
        self,
        request: CompensationRequest,
        *,
        owner_id: str,
        fencing_token: int,
        request_id: UUID,
        after_barrier: Callable[[], None] | None = None,
        ambiguous_after_restore: bool = False,
    ) -> dict[str, str | None]:
        if compensation_hash(request) != request.operation_hash:
            raise DomainError(ErrorCode.PRECONDITION_FAILED, "Compensation hash mismatch")
        with wp3_effect_lock(self.uow_factory, "compensation", request.compensation_id):
            return self._compensate_locked(
                request,
                owner_id=owner_id,
                fencing_token=fencing_token,
                request_id=request_id,
                after_barrier=after_barrier,
                ambiguous_after_restore=ambiguous_after_restore,
            )

    def _compensate_locked(
        self,
        request: CompensationRequest,
        *,
        owner_id: str,
        fencing_token: int,
        request_id: UUID,
        after_barrier: Callable[[], None] | None,
        ambiguous_after_restore: bool,
    ) -> dict[str, str | None]:
        document = request.model_dump(mode="json")
        with self.uow_factory() as uow:
            existing = (
                uow.conn.execute(
                    text(
                        "SELECT request,receipt FROM compensation_operations "
                        "WHERE actor_id=:actor AND idempotency_key=:key FOR UPDATE"
                    ),
                    {"actor": request.actor_id, "key": request.idempotency_key},
                )
                .mappings()
                .first()
            )
            if existing:
                if existing["request"] != document:
                    raise DomainError(ErrorCode.IDEMPOTENCY_CONFLICT, "Compensation key conflict")
                if existing["receipt"] is None:
                    raise DomainError(ErrorCode.PRECONDITION_FAILED, "Compensation in progress")
                return dict(existing["receipt"])
            row = (
                uow.conn.execute(
                    text(
                        "SELECT tc.request,tc.previous_content,tc.state,tc.run_id,r.task_id,"
                        "r.state AS run_state,"
                        "r.fencing_token,l.owner_id,l.fencing_token AS lease_token,l.state AS lease_state,"
                        "l.expires_at FROM tool_calls tc JOIN runs r ON r.id=tc.run_id "
                        "JOIN leases l ON l.resource_type='Task' AND l.resource_id=r.task_id "
                        "WHERE tc.id=:tool FOR UPDATE OF tc,r,l"
                    ),
                    {"tool": request.original_tool_call_id},
                )
                .mappings()
                .first()
            )
            if row is None or row["state"] != "SUCCEEDED":
                raise DomainError(ErrorCode.PRECONDITION_FAILED, "Original effect is not confirmed")
            if row["lease_state"] != "ACTIVE" or row["expires_at"] <= self.clock.now():
                raise DomainError(ErrorCode.LEASE_REQUIRED, "Lease is not active")
            if (
                row["owner_id"] != owner_id
                or row["lease_token"] != fencing_token
                or row["fencing_token"] != fencing_token
            ):
                raise DomainError(ErrorCode.FENCING_REJECTED, "Owner or token is stale")
            expected_hash = FixtureAtomicWrite.result_digest(row["previous_content"])
            if request.expected_result_hash != expected_hash:
                raise DomainError(
                    ErrorCode.PRECONDITION_FAILED,
                    "Expected compensation result hash does not match the approved prior state",
                )
            approval = uow.conn.execute(
                text(
                    "UPDATE approvals SET state='CONSUMED',uses_consumed=uses_consumed+1 "
                    "WHERE operation_id=:operation AND operation_hash=:hash AND state='APPROVED' "
                    "AND expires_at>:now AND uses_consumed=0 AND max_uses=1 RETURNING id"
                ),
                {
                    "operation": request.operation_id,
                    "hash": request.operation_hash,
                    "now": self.clock.now(),
                },
            ).first()
            if approval is None:
                raise DomainError(
                    ErrorCode.APPROVAL_REQUIRED, "Exact compensation approval required"
                )
            approval_id = approval[0]
            original_request = row["request"]
            previous = row["previous_content"]
            # Compensation uses the same pre-dispatch ambiguity barrier as the
            # original effect.  A stale executor cannot create this state
            # afterward; it is committed while its lease/fence is still valid.
            uow.conn.execute(
                text(
                    "INSERT INTO compensation_operations(id,original_tool_call_id,operation_id,"
                    "operation_hash,actor_id,idempotency_key,state,request,run_id,approval_id,"
                    "owner_id,fencing_token,previous_content,expected_result_hash,run_state_before_unknown) VALUES "
                    "(:id,:tool,:operation,:hash,:actor,:key,'OUTCOME_UNKNOWN',CAST(:request AS jsonb),"
                    ":run,:approval,:owner,:token,:previous,:expected,:run_state)"
                ),
                {
                    "id": request.compensation_id,
                    "tool": request.original_tool_call_id,
                    "operation": request.operation_id,
                    "hash": request.operation_hash,
                    "actor": request.actor_id,
                    "key": request.idempotency_key,
                    "request": json.dumps(document),
                    "run": row["run_id"],
                    "approval": approval_id,
                    "owner": owner_id,
                    "token": fencing_token,
                    "previous": previous,
                    "expected": request.expected_result_hash,
                    "run_state": row["run_state"],
                },
            )
            run_blocked = uow.conn.execute(
                text(
                    "UPDATE runs SET state='RECONCILIATION_REQUIRED',revision=revision+1,"
                    "updated_at=now() WHERE id=:id AND state IN ('RUNNING','SUCCEEDED') "
                    "RETURNING id"
                ),
                {"id": row["run_id"]},
            ).first()
            if run_blocked is None:
                raise DomainError(
                    ErrorCode.PRECONDITION_FAILED,
                    "Run could not enter the compensation reconciliation barrier",
                )
        if after_barrier is not None:
            after_barrier()
        outcome = "SUCCEEDED"
        resulting_hash: str | None
        error_code: str | None
        try:
            resulting_hash = self.adapter.restore(
                original_request["target_relative_path"],
                previous,
                max_bytes=original_request["sandbox"]["max_bytes"],
                ambiguous_after_restore=ambiguous_after_restore,
            )
        except AmbiguousEffectError as error:
            resulting_hash = None
            outcome = "OUTCOME_UNKNOWN"
            error_code = error.code
        except ConfirmedNoEffectError as error:
            resulting_hash = None
            outcome = "FAILED"
            error_code = error.code
        else:
            error_code = None
            if resulting_hash != request.expected_result_hash:
                outcome = "FAILED"
                error_code = "COMPENSATION_RESULT_HASH_MISMATCH"
        receipt = {
            "compensation_id": str(request.compensation_id),
            "original_tool_call_id": str(request.original_tool_call_id),
            "outcome": outcome,
            "resulting_hash": resulting_hash,
            "expected_result_hash": request.expected_result_hash,
            "error_code": error_code,
        }
        with self.uow_factory() as uow:
            lease = (
                uow.conn.execute(
                    text(
                        "SELECT r.fencing_token,l.owner_id,l.fencing_token AS lease_token,"
                        "l.state,l.expires_at FROM tool_calls tc JOIN runs r ON r.id=tc.run_id "
                        "JOIN leases l ON l.resource_type='Task' AND l.resource_id=r.task_id "
                        "WHERE tc.id=:tool FOR UPDATE OF r,l"
                    ),
                    {"tool": request.original_tool_call_id},
                )
                .mappings()
                .one()
            )
            if lease["state"] != "ACTIVE" or lease["expires_at"] <= self.clock.now():
                raise DomainError(ErrorCode.LEASE_REQUIRED, "Lease is not active after effect")
            if (
                lease["owner_id"] != owner_id
                or lease["lease_token"] != fencing_token
                or lease["fencing_token"] != fencing_token
            ):
                raise DomainError(ErrorCode.FENCING_REJECTED, "Owner or token became stale")
            if outcome != "OUTCOME_UNKNOWN":
                run_transition = uow.conn.execute(
                    text(
                        "UPDATE runs SET state=(SELECT run_state_before_unknown "
                        "FROM compensation_operations WHERE id=:id),revision=revision+1,"
                        "updated_at=now() WHERE id=(SELECT run_id FROM compensation_operations "
                        "WHERE id=:id) AND state='RECONCILIATION_REQUIRED' RETURNING id"
                    ),
                    {"id": request.compensation_id},
                ).first()
                if run_transition is None:
                    raise DomainError(
                        ErrorCode.PRECONDITION_FAILED,
                        "Compensation lost the Run compare-and-set",
                    )
            updated = uow.conn.execute(
                text(
                    "UPDATE compensation_operations SET state=:state,receipt=CAST(:receipt AS jsonb),"
                    "observed_result_hash=:observed,error_code=:error,updated_at=now() "
                    "WHERE id=:id AND state='OUTCOME_UNKNOWN' RETURNING id"
                ),
                {
                    "state": outcome,
                    "receipt": json.dumps(receipt),
                    "observed": resulting_hash,
                    "error": error_code,
                    "id": request.compensation_id,
                },
            ).first()
            if updated is None:
                raise DomainError(ErrorCode.PRECONDITION_FAILED, "Compensation state changed")
            IntegrityService._outbox(
                uow.conn,
                "internal.fixture.compensated",
                "ToolCall",
                request.original_tool_call_id,
                receipt,
            )
            IntegrityService._audit(
                uow.conn,
                request_id=request_id,
                actor_id=owner_id,
                command_type="internal.fixture.atomic-write.compensate",
                aggregate_type="Compensation",
                aggregate_id=request.compensation_id,
                previous_state="OUTCOME_UNKNOWN",
                new_state=outcome,
                details={"original_tool_call_id": str(request.original_tool_call_id)},
            )
        return receipt

    def reconcile(
        self,
        *,
        compensation_id: UUID,
        owner_id: str,
        fencing_token: int,
        idempotency_key: str,
        request_id: UUID,
    ) -> dict[str, object]:
        """Observe the exact fixture restoration; never dispatches compensation."""
        with wp3_effect_lock(self.uow_factory, "compensation", compensation_id):
            return self._reconcile_locked(
                compensation_id=compensation_id,
                owner_id=owner_id,
                fencing_token=fencing_token,
                idempotency_key=idempotency_key,
                request_id=request_id,
            )

    def _reconcile_locked(
        self,
        *,
        compensation_id: UUID,
        owner_id: str,
        fencing_token: int,
        idempotency_key: str,
        request_id: UUID,
    ) -> dict[str, object]:
        with self.uow_factory() as uow:
            uow.conn.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                {"key": f"compensation-reconcile:{compensation_id}:{idempotency_key}"},
            )
            row = (
                uow.conn.execute(
                    text(
                        "SELECT co.*,tc.request AS tool_request,tc.resulting_content,tc.run_id AS tool_run_id,"
                        "r.state AS run_state,r.fencing_token AS run_token,l.owner_id AS lease_owner_id,"
                        "l.fencing_token AS lease_token,"
                        "l.state AS lease_state,l.expires_at FROM compensation_operations co "
                        "JOIN tool_calls tc ON tc.id=co.original_tool_call_id JOIN runs r ON r.id=co.run_id "
                        "JOIN leases l ON l.resource_type='Task' AND l.resource_id=r.task_id "
                        "WHERE co.id=:id FOR UPDATE OF co,r,l"
                    ),
                    {"id": compensation_id},
                )
                .mappings()
                .first()
            )
            if row is None:
                raise DomainError(ErrorCode.PRECONDITION_FAILED, "Compensation does not exist")
            if row["lease_state"] != "ACTIVE" or row["expires_at"] <= self.clock.now():
                raise DomainError(ErrorCode.LEASE_REQUIRED, "Lease is not active")
            if (
                row["lease_owner_id"] != owner_id
                or row["lease_token"] != fencing_token
                or row["run_token"] != fencing_token
            ):
                raise DomainError(ErrorCode.FENCING_REJECTED, "Owner or token is stale")
            replay = (
                uow.conn.execute(
                    text(
                        "SELECT document,owner_id,fencing_token FROM compensation_reconciliations "
                        "WHERE compensation_id=:id AND idempotency_key=:key"
                    ),
                    {"id": compensation_id, "key": idempotency_key},
                )
                .mappings()
                .first()
            )
            if replay is not None:
                if replay["owner_id"] != owner_id or replay["fencing_token"] != fencing_token:
                    raise DomainError(
                        ErrorCode.IDEMPOTENCY_CONFLICT,
                        "Compensation reconciliation key reused with different authority",
                    )
                return dict(replay["document"])
            if row["state"] != "OUTCOME_UNKNOWN" or row["run_state"] != "RECONCILIATION_REQUIRED":
                raise DomainError(
                    ErrorCode.PRECONDITION_FAILED, "No ambiguous compensation to reconcile"
                )
            target = row["tool_request"]["target_relative_path"]
            observed = self.adapter.observe(target)
            observed_hash = FixtureAtomicWrite.result_digest(observed)
            original_hash = FixtureAtomicWrite.result_digest(row["resulting_content"])
            expected = row["expected_result_hash"]
            if observed_hash == expected:
                outcome, terminal, reason = (
                    "COMPENSATION_CONFIRMED",
                    "SUCCEEDED",
                    "FIXTURE_COMPENSATION_CONFIRMED",
                )
            elif observed_hash == original_hash:
                outcome, terminal, reason = (
                    "COMPENSATION_NOT_OCCURRED",
                    "FAILED",
                    "FIXTURE_COMPENSATION_NOT_OCCURRED",
                )
            else:
                outcome, terminal, reason = "UNRESOLVED", None, "FIXTURE_COMPENSATION_UNRESOLVED"
            result: dict[str, object] = {
                "compensation_id": str(compensation_id),
                "original_tool_call_id": str(row["original_tool_call_id"]),
                "operation_id": str(row["operation_id"]),
                "outcome": outcome,
                "observed_hash": observed_hash,
                "reason_code": reason,
            }
            uow.conn.execute(
                text(
                    "INSERT INTO compensation_reconciliations("
                    "id,compensation_id,operation_id,outcome,observed_hash,evidence_refs,document,"
                    "reconciled_at,owner_id,fencing_token,idempotency_key) VALUES "
                    "(:id,:comp,:op,:outcome,:observed,CAST(:evidence AS jsonb),CAST(:doc AS jsonb),"
                    ":at,:owner,:token,:key)"
                ),
                {
                    "id": uuid4(),
                    "comp": compensation_id,
                    "op": row["operation_id"],
                    "outcome": outcome,
                    "observed": observed_hash,
                    "evidence": json.dumps([f"fixture:{target}:{observed_hash}"]),
                    "doc": json.dumps(result),
                    "at": self.clock.now(),
                    "owner": owner_id,
                    "token": fencing_token,
                    "key": idempotency_key,
                },
            )
            if terminal is not None:
                operation_transition = uow.conn.execute(
                    text(
                        "UPDATE compensation_operations SET state=:state,updated_at=now() "
                        "WHERE id=:id AND state='OUTCOME_UNKNOWN' RETURNING id"
                    ),
                    {"state": terminal, "id": compensation_id},
                ).first()
                if operation_transition is None:
                    raise DomainError(
                        ErrorCode.PRECONDITION_FAILED,
                        "Compensation reconciliation lost its compare-and-set",
                    )
                run_transition = uow.conn.execute(
                    text(
                        "UPDATE runs SET state=:state,revision=revision+1,updated_at=now() "
                        "WHERE id=:id AND state='RECONCILIATION_REQUIRED' RETURNING id"
                    ),
                    {"state": row["run_state_before_unknown"], "id": row["run_id"]},
                ).first()
                if run_transition is None:
                    raise DomainError(
                        ErrorCode.PRECONDITION_FAILED,
                        "Compensation reconciliation lost the Run compare-and-set",
                    )
            IntegrityService._outbox(
                uow.conn,
                "internal.fixture.compensation-reconciled",
                "Compensation",
                compensation_id,
                result,
            )
            IntegrityService._audit(
                uow.conn,
                request_id=request_id,
                actor_id=owner_id,
                command_type="internal.fixture.atomic-write.reconcile-compensation",
                aggregate_type="Compensation",
                aggregate_id=compensation_id,
                previous_state="OUTCOME_UNKNOWN",
                new_state=terminal or "OUTCOME_UNKNOWN",
                details={"outcome": outcome, "fencing_token": fencing_token},
            )
            return result
