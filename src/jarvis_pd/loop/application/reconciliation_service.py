import json
from uuid import UUID, uuid4

from sqlalchemy import text

from jarvis_pd.contracts.gate1 import (
    ReconciliationOutcome,
    ReconciliationResult,
    ToolCallRequest,
)
from jarvis_pd.loop.application.effect_coordination import wp3_effect_lock
from jarvis_pd.loop.application.fixture_atomic_write import FixtureAtomicWrite
from jarvis_pd.loop.application.integrity_service import IntegrityService
from jarvis_pd.shared.errors import DomainError, ErrorCode


class FixtureReconciliationService:
    """Observation-only reconciler for fixture.atomic_write; it never retries effects."""

    def __init__(self, uow_factory, clock, adapter: FixtureAtomicWrite) -> None:
        self.uow_factory = uow_factory
        self.clock = clock
        self.adapter = adapter

    def reconcile(
        self,
        *,
        tool_call_id: UUID,
        owner_id: str,
        fencing_token: int,
        idempotency_key: str,
        request_id: UUID,
    ) -> ReconciliationResult:
        with wp3_effect_lock(self.uow_factory, "tool", tool_call_id):
            return self._reconcile_locked(
                tool_call_id=tool_call_id,
                owner_id=owner_id,
                fencing_token=fencing_token,
                idempotency_key=idempotency_key,
                request_id=request_id,
            )

    def _reconcile_locked(
        self,
        *,
        tool_call_id: UUID,
        owner_id: str,
        fencing_token: int,
        idempotency_key: str,
        request_id: UUID,
    ) -> ReconciliationResult:
        with self.uow_factory() as uow:
            uow.conn.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                {"key": f"reconcile:{tool_call_id}:{idempotency_key}"},
            )
            row = (
                uow.conn.execute(
                    text(
                        "SELECT tc.operation_id,tc.request,tc.previous_content,tc.resulting_content,"
                        "tc.run_id,tc.state AS tool_state,r.state,r.task_id,r.fencing_token,l.owner_id,l.fencing_token AS lease_token,"
                        "l.state AS lease_state,l.expires_at FROM tool_calls tc JOIN runs r ON r.id=tc.run_id "
                        "JOIN leases l ON l.resource_type='Task' AND l.resource_id=r.task_id "
                        "WHERE tc.id=:tool FOR UPDATE OF tc,r,l"
                    ),
                    {"tool": tool_call_id},
                )
                .mappings()
                .first()
            )
            if row is None:
                raise DomainError(ErrorCode.PRECONDITION_FAILED, "Tool call does not exist")
            if row["lease_state"] != "ACTIVE" or row["expires_at"] <= self.clock.now():
                raise DomainError(ErrorCode.LEASE_REQUIRED, "Lease is not active")
            if (
                row["owner_id"] != owner_id
                or row["lease_token"] != fencing_token
                or row["fencing_token"] != fencing_token
            ):
                raise DomainError(ErrorCode.FENCING_REJECTED, "Owner or token is stale")
            replay = (
                uow.conn.execute(
                    text(
                        "SELECT document,owner_id,fencing_token FROM reconciliations "
                        "WHERE tool_call_id=:tool AND idempotency_key=:key"
                    ),
                    {"tool": tool_call_id, "key": idempotency_key},
                )
                .mappings()
                .first()
            )
            if replay is not None:
                if replay["owner_id"] != owner_id or replay["fencing_token"] != fencing_token:
                    raise DomainError(
                        ErrorCode.IDEMPOTENCY_CONFLICT,
                        "Reconciliation key reused with different authority",
                    )
                return ReconciliationResult.model_validate(replay["document"])
            if row["tool_state"] != "OUTCOME_UNKNOWN" or row["state"] != "RECONCILIATION_REQUIRED":
                raise DomainError(ErrorCode.PRECONDITION_FAILED, "No UNKNOWN effect to reconcile")
            request = ToolCallRequest.model_validate(row["request"])
            observed = self.adapter.observe(request.target_relative_path)
            observed_hash = FixtureAtomicWrite.digest(observed) if observed is not None else None
            expected_hash = FixtureAtomicWrite.digest(row["resulting_content"])
            previous_hash = (
                FixtureAtomicWrite.digest(row["previous_content"])
                if row["previous_content"] is not None
                else None
            )
            if observed_hash == expected_hash:
                outcome = ReconciliationOutcome.CONFIRMED_SUCCEEDED
                terminal = "SUCCEEDED"
            elif observed is None and row["previous_content"] is None:
                outcome = ReconciliationOutcome.NO_EFFECT
                terminal = "FAILED"
            elif observed_hash == previous_hash:
                outcome = ReconciliationOutcome.CONFIRMED_FAILED
                terminal = "FAILED"
            else:
                outcome = ReconciliationOutcome.UNRESOLVED
                terminal = None
            result = ReconciliationResult(
                contract="jarvis.reconciliation-result",
                schema_version="1.0.0",
                reconciliation_id=uuid4(),
                tool_call_id=tool_call_id,
                operation_id=row["operation_id"],
                outcome=outcome,
                observed_hash=observed_hash,
                reconciled_at=self.clock.now(),
                evidence_refs=[
                    f"fixture:{request.target_relative_path}:{observed_hash or 'absent'}"
                ],
                reason_code=f"FIXTURE_{outcome.value}",
            )
            uow.conn.execute(
                text(
                    "INSERT INTO reconciliations(id,tool_call_id,operation_id,outcome,reason_code,"
                    "evidence_refs,document,reconciled_at,owner_id,fencing_token,idempotency_key) VALUES "
                    "(:id,:tool,:operation,:outcome,:reason,CAST(:evidence AS jsonb),CAST(:document AS jsonb),"
                    ":at,:owner,:token,:key)"
                ),
                {
                    "id": result.reconciliation_id,
                    "tool": tool_call_id,
                    "operation": result.operation_id,
                    "outcome": result.outcome.value,
                    "reason": result.reason_code,
                    "evidence": json.dumps(result.evidence_refs),
                    "document": json.dumps(result.model_dump(mode="json")),
                    "at": result.reconciled_at,
                    "owner": owner_id,
                    "token": fencing_token,
                    "key": idempotency_key,
                },
            )
            if terminal is not None:
                tool_transition = uow.conn.execute(
                    text(
                        "UPDATE tool_calls SET state=:state,updated_at=now() "
                        "WHERE id=:id AND state='OUTCOME_UNKNOWN' RETURNING id"
                    ),
                    {"state": terminal, "id": tool_call_id},
                ).first()
                if tool_transition is None:
                    raise DomainError(
                        ErrorCode.PRECONDITION_FAILED,
                        "Reconciliation lost the ToolCall compare-and-set",
                    )
                run_transition = uow.conn.execute(
                    text(
                        "UPDATE runs SET state=:state,revision=revision+1,updated_at=now() "
                        "WHERE id=:id AND state='RECONCILIATION_REQUIRED' RETURNING id"
                    ),
                    {"state": terminal, "id": row["run_id"]},
                ).first()
                if run_transition is None:
                    raise DomainError(
                        ErrorCode.PRECONDITION_FAILED,
                        "Reconciliation lost the Run compare-and-set",
                    )
            IntegrityService._outbox(
                uow.conn,
                "internal.tool-effect.reconciled",
                "ToolCall",
                tool_call_id,
                {"outcome": outcome.value},
            )
            IntegrityService._audit(
                uow.conn,
                request_id=request_id,
                actor_id=owner_id,
                command_type="internal.fixture.atomic-write.reconcile",
                aggregate_type="ToolCall",
                aggregate_id=tool_call_id,
                previous_state="OUTCOME_UNKNOWN",
                new_state=terminal or "OUTCOME_UNKNOWN",
                details={"outcome": outcome.value, "fencing_token": fencing_token},
            )
            return result
