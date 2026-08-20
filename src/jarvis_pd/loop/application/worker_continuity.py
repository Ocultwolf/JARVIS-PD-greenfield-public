import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import Connection, text

from jarvis_pd.contracts.gate1 import (
    EffectReceipt,
    ReconciliationOutcome,
    ReconciliationResult,
    ToolCallOutcome,
    ToolCallRequest,
)
from jarvis_pd.loop.application.host_latch import HostLatch
from jarvis_pd.loop.application.integrity_service import IntegrityService
from jarvis_pd.loop.application.selection_service import SelectionService
from jarvis_pd.shared.errors import DomainError, ErrorCode


def _message_hash(message_type: str, payload: dict[str, Any]) -> str:
    identity = {"message_type": message_type, "payload": payload}
    value = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(value).hexdigest()


@dataclass(frozen=True, slots=True)
class WorkerRun:
    task_id: UUID
    run_id: UUID
    owner_id: str
    fencing_token: int


class WorkerExecutionError(Exception):
    def __init__(
        self,
        message: str,
        *,
        result: EffectReceipt | ReconciliationResult | object | None = None,
        non_recoverable: bool = False,
    ) -> None:
        super().__init__(message)
        self.result = result
        self.non_recoverable = non_recoverable


class WorkerContinuityService:
    """PostgreSQL-canonical WP4 continuity with a host-persistent latch."""

    def __init__(self, uow_factory, clock, host_latch: HostLatch) -> None:
        self.uow_factory = uow_factory
        self.clock = clock
        self.host_latch = host_latch

    @staticmethod
    def _positive_ttl(ttl: timedelta) -> None:
        if ttl <= timedelta(0):
            raise DomainError(ErrorCode.PRECONDITION_FAILED, "TTL_MUST_BE_POSITIVE")

    def require_latch_clear(self) -> None:
        host = self.host_latch.read()
        with self.uow_factory() as uow:
            projection = (
                uow.conn.execute(
                    text(
                        "SELECT active,reason,revision,host_state_hash FROM gate1_local_latch "
                        "WHERE latch_id='gate1-local'"
                    )
                )
                .mappings()
                .first()
            )
            if projection is None:
                raise DomainError(ErrorCode.UNAUTHORIZED, "LATCH_PROJECTION_MISSING")
            if (
                projection["active"] != host.active
                or projection["reason"] != host.reason
                or projection["revision"] != host.revision
                or projection["host_state_hash"] != host.state_hash
            ):
                raise DomainError(ErrorCode.UNAUTHORIZED, "LATCH_PROJECTION_DIVERGED")
            if host.active:
                raise DomainError(ErrorCode.UNAUTHORIZED, "HOST_LATCH_ACTIVE")

    def heartbeat(
        self, *, worker_id: str, owner_id: str, status: str, ttl: timedelta, request_id: UUID
    ) -> datetime:
        self._positive_ttl(ttl)
        if status not in {"IDLE", "RUNNING", "INTERRUPTED", "STOPPED"}:
            raise DomainError(ErrorCode.PRECONDITION_FAILED, "Invalid heartbeat status")
        now = self.clock.now()
        expires_at = now + ttl
        with self.uow_factory() as uow:
            existing = (
                uow.conn.execute(
                    text(
                        "SELECT owner_id,status FROM worker_heartbeats "
                        "WHERE worker_id=:worker FOR UPDATE"
                    ),
                    {"worker": worker_id},
                )
                .mappings()
                .first()
            )
            if existing is not None and existing["owner_id"] != owner_id:
                raise DomainError(ErrorCode.FENCING_REJECTED, "Heartbeat owner mismatch")
            uow.conn.execute(
                text(
                    "INSERT INTO worker_heartbeats(worker_id,owner_id,status,last_seen_at,expires_at) "
                    "VALUES (:worker,:owner,:status,:now,:expires) ON CONFLICT(worker_id) DO UPDATE "
                    "SET status=EXCLUDED.status,last_seen_at=EXCLUDED.last_seen_at,"
                    "expires_at=EXCLUDED.expires_at,revision=worker_heartbeats.revision+1"
                ),
                {
                    "worker": worker_id,
                    "owner": owner_id,
                    "status": status,
                    "now": now,
                    "expires": expires_at,
                },
            )
            IntegrityService._audit(
                uow.conn,
                request_id=request_id,
                actor_id=owner_id,
                command_type="internal.worker.heartbeat",
                aggregate_type="Worker",
                aggregate_id=request_id,
                previous_state=existing["status"] if existing else None,
                new_state=status,
                details={"worker_id": worker_id, "expires_at": expires_at.isoformat()},
            )
        return expires_at

    def worker_lost(self, worker_id: str) -> bool:
        with self.uow_factory() as uow:
            expires_at = uow.conn.execute(
                text("SELECT expires_at FROM worker_heartbeats WHERE worker_id=:worker"),
                {"worker": worker_id},
            ).scalar_one_or_none()
            return expires_at is None or expires_at <= self.clock.now()

    def renew_lease(
        self,
        *,
        resource_id: UUID,
        owner_id: str,
        fencing_token: int,
        ttl: timedelta,
        request_id: UUID,
    ) -> datetime:
        self._positive_ttl(ttl)
        self.require_latch_clear()
        now = self.clock.now()
        expires_at = now + ttl
        with self.uow_factory() as uow:
            row = uow.conn.execute(
                text(
                    "UPDATE leases SET expires_at=:expires,updated_at=:now "
                    "WHERE resource_type='Task' AND resource_id=:resource AND owner_id=:owner "
                    "AND fencing_token=:token AND state='ACTIVE' AND expires_at>:now "
                    "RETURNING fencing_token"
                ),
                {
                    "expires": expires_at,
                    "now": now,
                    "resource": resource_id,
                    "owner": owner_id,
                    "token": fencing_token,
                },
            ).first()
            if row is None:
                raise DomainError(ErrorCode.FENCING_REJECTED, "Lease renewal rejected")
            IntegrityService._audit(
                uow.conn,
                request_id=request_id,
                actor_id=owner_id,
                command_type="internal.worker.lease.renewed",
                aggregate_type="Lease",
                aggregate_id=resource_id,
                previous_state="ACTIVE",
                new_state="ACTIVE",
                details={"fencing_token": fencing_token, "expires_at": expires_at.isoformat()},
            )
        return expires_at

    def assert_run_authority(self, *, run_id: UUID, owner_id: str, fencing_token: int) -> None:
        with self.uow_factory() as uow:
            valid = uow.conn.execute(
                text(
                    "SELECT 1 FROM runs r JOIN leases l ON l.resource_type='Task' "
                    "AND l.resource_id=r.task_id WHERE r.id=:run AND r.fencing_token=:token "
                    "AND l.owner_id=:owner AND l.fencing_token=:token AND l.state='ACTIVE' "
                    "AND l.expires_at>:now"
                ),
                {"run": run_id, "owner": owner_id, "token": fencing_token, "now": self.clock.now()},
            ).first()
            if valid is None:
                raise DomainError(ErrorCode.FENCING_REJECTED, "Stale run authority")

    def takeover(
        self, *, run_id: UUID, new_owner_id: str, ttl: timedelta, request_id: UUID
    ) -> WorkerRun:
        self._positive_ttl(ttl)
        self.require_latch_clear()
        now = self.clock.now()
        with self.uow_factory() as uow:
            row = (
                uow.conn.execute(
                    text(
                        "SELECT r.task_id,r.state,l.owner_id,l.fencing_token,l.state AS lease_state,"
                        "l.expires_at,(SELECT max(h.expires_at) FROM worker_heartbeats h "
                        "WHERE h.owner_id=l.owner_id) AS heartbeat_expires "
                        "FROM runs r JOIN leases l ON l.resource_type='Task' "
                        "AND l.resource_id=r.task_id WHERE r.id=:run FOR UPDATE OF r,l"
                    ),
                    {"run": run_id},
                )
                .mappings()
                .first()
            )
            if row is None:
                raise DomainError(ErrorCode.PRECONDITION_FAILED, "Run not found")
            if row["state"] not in {"RUNNING", "PAUSED", "BLOCKED"}:
                raise DomainError(ErrorCode.PRECONDITION_FAILED, "Run is not recoverable")
            if row["lease_state"] == "ACTIVE" and row["expires_at"] > now:
                raise DomainError(ErrorCode.LEASE_REQUIRED, "Previous owner retains a valid lease")
            if row["heartbeat_expires"] is None or row["heartbeat_expires"] > now:
                raise DomainError(ErrorCode.PRECONDITION_FAILED, "Previous heartbeat not expired")
            token = int(row["fencing_token"]) + 1
            updated = uow.conn.execute(
                text(
                    "UPDATE leases SET owner_id=:owner,fencing_token=:token,state='ACTIVE',"
                    "expires_at=:expires,updated_at=:now WHERE resource_type='Task' "
                    "AND resource_id=:task AND fencing_token=:old_token RETURNING fencing_token"
                ),
                {
                    "owner": new_owner_id,
                    "token": token,
                    "expires": now + ttl,
                    "now": now,
                    "task": row["task_id"],
                    "old_token": row["fencing_token"],
                },
            ).first()
            if updated is None:
                raise DomainError(ErrorCode.FENCING_REJECTED, "Concurrent takeover won")
            changed = uow.conn.execute(
                text(
                    "UPDATE runs SET fencing_token=:token,state='RUNNING',revision=revision+1,"
                    "updated_at=:now WHERE id=:run AND state=CAST(:old_state AS run_state) "
                    "RETURNING id"
                ),
                {"token": token, "now": now, "run": run_id, "old_state": row["state"]},
            ).first()
            if changed is None:
                raise DomainError(ErrorCode.REVISION_CONFLICT, "Run changed during takeover")
            uow.conn.execute(
                text(
                    "UPDATE worker_interruptions SET recovered_at=:now,recovered_by=:owner,"
                    "recovery_fencing_token=:token WHERE run_id=:run AND recovered_at IS NULL"
                ),
                {"now": now, "owner": new_owner_id, "token": token, "run": run_id},
            )
            details = {
                "previous_owner": row["owner_id"],
                "owner_id": new_owner_id,
                "fencing_token": token,
            }
            IntegrityService._outbox(uow.conn, "internal.worker.takeover", "Run", run_id, details)
            IntegrityService._audit(
                uow.conn,
                request_id=request_id,
                actor_id=new_owner_id,
                command_type="internal.worker.takeover",
                aggregate_type="Run",
                aggregate_id=run_id,
                previous_state=row["state"],
                new_state="RUNNING",
                details=details,
            )
            return WorkerRun(row["task_id"], run_id, new_owner_id, token)

    def _lock_run_bundle(self, conn: Connection, run: WorkerRun) -> dict[str, Any]:
        row = (
            conn.execute(
                text(
                    "SELECT r.task_id,r.state AS run_state,r.fencing_token AS run_token,"
                    "t.state AS task_state,l.owner_id,l.fencing_token AS lease_token,"
                    "l.state AS lease_state,l.expires_at FROM runs r "
                    "JOIN tasks t ON t.id=r.task_id JOIN leases l ON l.resource_type='Task' "
                    "AND l.resource_id=r.task_id WHERE r.id=:run FOR UPDATE OF r,t,l"
                ),
                {"run": run.run_id},
            )
            .mappings()
            .first()
        )
        if row is None:
            raise DomainError(ErrorCode.PRECONDITION_FAILED, "Run, Task, or lease missing")
        value = dict(row)
        if value["task_id"] != run.task_id or value["task_state"] != "RUNNING":
            raise DomainError(ErrorCode.PRECONDITION_FAILED, "Run/Task state divergence")
        if (
            value["owner_id"] != run.owner_id
            or value["run_token"] != run.fencing_token
            or value["lease_token"] != run.fencing_token
            or value["lease_state"] != "ACTIVE"
            or value["expires_at"] <= self.clock.now()
        ):
            raise DomainError(ErrorCode.FENCING_REJECTED, "Worker authority rejected")
        return value

    @staticmethod
    def _nonempty_evidence(values: list[str]) -> bool:
        return bool(values) and all(isinstance(value, str) and value.strip() for value in values)

    def _validate_canonical_result(
        self,
        conn: Connection,
        run: WorkerRun,
        result: EffectReceipt | ReconciliationResult | object,
    ) -> tuple[str, dict[str, Any], set[str]]:
        if isinstance(result, EffectReceipt):
            if not isinstance(result.outcome, ToolCallOutcome):
                raise DomainError(ErrorCode.PRECONDITION_FAILED, "RECEIPT_OUTCOME_INVALID")
            row = (
                conn.execute(
                    text(
                        "SELECT er.document,er.tool_call_id,er.operation_id,er.outcome,"
                        "er.resulting_hash,er.verified,er.owner_id AS receipt_owner,"
                        "er.fencing_token AS receipt_token,tc.run_id,tc.action_id,"
                        "tc.operation_id AS tool_operation_id,tc.tool_id,tc.state AS tool_state,"
                        "tc.operation_hash,tc.request,tc.resulting_content,"
                        "tc.owner_id AS tool_owner,tc.fencing_token AS tool_token,"
                        "a.run_id AS action_run_id,a.kind AS action_kind,a.state AS action_state "
                        "FROM effect_receipts er JOIN tool_calls tc ON tc.id=er.tool_call_id "
                        "JOIN actions a ON a.id=tc.action_id WHERE er.id=:id "
                        "FOR UPDATE OF er,tc,a"
                    ),
                    {"id": result.receipt_id},
                )
                .mappings()
                .first()
            )
            if row is None:
                raise DomainError(ErrorCode.PRECONDITION_FAILED, "CANONICAL_RECEIPT_MISSING")
            try:
                persisted = EffectReceipt.model_validate(row["document"])
                request = ToolCallRequest.model_validate(row["request"])
            except ValidationError as error:
                raise DomainError(
                    ErrorCode.PRECONDITION_FAILED, "CANONICAL_RECEIPT_INVALID"
                ) from error
            if persisted.model_dump(mode="json") != result.model_dump(mode="json"):
                raise DomainError(ErrorCode.PRECONDITION_FAILED, "RECEIPT_DOCUMENT_MISMATCH")
            expected_hash = (
                "sha256:" + hashlib.sha256(row["resulting_content"]).hexdigest()
                if row["resulting_content"] is not None
                else None
            )
            if (
                row["tool_call_id"] != result.tool_call_id
                or row["operation_id"] != result.operation_id
                or row["tool_operation_id"] != result.operation_id
                or row["run_id"] != run.run_id
                or row["action_run_id"] != run.run_id
                or request.run_id != run.run_id
                or request.action_id != row["action_id"]
                or request.tool_call_id != result.tool_call_id
                or request.operation_id != result.operation_id
                or request.operation_hash != row["operation_hash"]
                or row["tool_id"] != "fixture.atomic_write"
                or row["action_kind"] != "fixture.atomic_write"
                or row["action_state"] != "AUTHORIZED"
                or row["tool_owner"] != run.owner_id
                or row["receipt_owner"] != run.owner_id
                or row["tool_token"] != run.fencing_token
                or row["receipt_token"] != run.fencing_token
                or row["outcome"] != result.outcome.value
                or row["verified"] != result.verified
                or row["resulting_hash"] != result.resulting_hash
                or not self._nonempty_evidence(result.evidence_refs)
            ):
                raise DomainError(ErrorCode.PRECONDITION_FAILED, "RECEIPT_RELATION_MISMATCH")
            mapping = {
                ToolCallOutcome.SUCCEEDED: ("CONFIRMED_SUCCEEDED", "SUCCEEDED", {"RUNNING"}),
                ToolCallOutcome.FAILED: ("CONFIRMED_FAILED", "FAILED", {"RUNNING"}),
                ToolCallOutcome.OUTCOME_UNKNOWN: (
                    "OUTCOME_UNKNOWN",
                    "OUTCOME_UNKNOWN",
                    {"RECONCILIATION_REQUIRED"},
                ),
            }
            classification, expected_tool_state, allowed_run_states = mapping[result.outcome]
            if row["tool_state"] != expected_tool_state:
                raise DomainError(ErrorCode.PRECONDITION_FAILED, "TOOL_CALL_STATE_MISMATCH")
            if result.outcome == ToolCallOutcome.SUCCEEDED and (
                not result.verified
                or result.resulting_hash is None
                or result.resulting_hash != expected_hash
            ):
                raise DomainError(ErrorCode.PRECONDITION_FAILED, "SUCCESS_RECEIPT_UNVERIFIED")
            return (
                classification,
                {
                    "result_kind": "EffectReceipt",
                    "result_id": str(result.receipt_id),
                    "tool_call_id": str(result.tool_call_id),
                    "operation_id": str(result.operation_id),
                    "evidence_refs": result.evidence_refs,
                },
                allowed_run_states,
            )
        if isinstance(result, ReconciliationResult):
            if not isinstance(result.outcome, ReconciliationOutcome):
                raise DomainError(ErrorCode.PRECONDITION_FAILED, "RECONCILIATION_OUTCOME_INVALID")
            row = (
                conn.execute(
                    text(
                        "SELECT rc.document,rc.tool_call_id,rc.operation_id,rc.outcome,"
                        "rc.observed_hash,rc.owner_id AS result_owner,"
                        "rc.fencing_token AS result_token,tc.run_id,tc.action_id,"
                        "tc.operation_id AS tool_operation_id,tc.tool_id,tc.state AS tool_state,"
                        "tc.operation_hash,tc.request,tc.resulting_content,"
                        "tc.owner_id AS tool_owner,tc.fencing_token AS tool_token,"
                        "a.run_id AS action_run_id,a.kind AS action_kind,a.state AS action_state "
                        "FROM reconciliations rc JOIN tool_calls tc ON tc.id=rc.tool_call_id "
                        "JOIN actions a ON a.id=tc.action_id WHERE rc.id=:id "
                        "FOR UPDATE OF rc,tc,a"
                    ),
                    {"id": result.reconciliation_id},
                )
                .mappings()
                .first()
            )
            if row is None:
                raise DomainError(ErrorCode.PRECONDITION_FAILED, "RECONCILIATION_RESULT_MISSING")
            try:
                persisted_reconciliation = ReconciliationResult.model_validate(row["document"])
                request = ToolCallRequest.model_validate(row["request"])
            except ValidationError as error:
                raise DomainError(
                    ErrorCode.PRECONDITION_FAILED, "RECONCILIATION_RESULT_INVALID"
                ) from error
            if persisted_reconciliation.model_dump(mode="json") != result.model_dump(mode="json"):
                raise DomainError(ErrorCode.PRECONDITION_FAILED, "RECONCILIATION_DOCUMENT_MISMATCH")
            if (
                row["tool_call_id"] != result.tool_call_id
                or row["operation_id"] != result.operation_id
                or row["tool_operation_id"] != result.operation_id
                or row["run_id"] != run.run_id
                or row["action_run_id"] != run.run_id
                or request.run_id != run.run_id
                or request.action_id != row["action_id"]
                or request.tool_call_id != result.tool_call_id
                or request.operation_id != result.operation_id
                or request.operation_hash != row["operation_hash"]
                or row["tool_id"] != "fixture.atomic_write"
                or row["action_kind"] != "fixture.atomic_write"
                or row["action_state"] != "AUTHORIZED"
                or row["tool_owner"] != run.owner_id
                or row["result_owner"] != run.owner_id
                or row["tool_token"] != run.fencing_token
                or row["result_token"] != run.fencing_token
                or row["outcome"] != result.outcome.value
                or row["observed_hash"] != result.observed_hash
                or not self._nonempty_evidence(result.evidence_refs)
            ):
                raise DomainError(ErrorCode.PRECONDITION_FAILED, "RECONCILIATION_RELATION_MISMATCH")
            reconciliation_mapping = {
                ReconciliationOutcome.CONFIRMED_SUCCEEDED: (
                    "CONFIRMED_SUCCEEDED",
                    "SUCCEEDED",
                    {"SUCCEEDED"},
                ),
                ReconciliationOutcome.CONFIRMED_FAILED: (
                    "CONFIRMED_FAILED",
                    "FAILED",
                    {"FAILED"},
                ),
                ReconciliationOutcome.NO_EFFECT: ("NO_EFFECT", "FAILED", {"FAILED"}),
                ReconciliationOutcome.UNRESOLVED: (
                    "OUTCOME_UNKNOWN",
                    "OUTCOME_UNKNOWN",
                    {"RECONCILIATION_REQUIRED"},
                ),
            }
            classification, expected_tool_state, allowed_run_states = reconciliation_mapping[
                result.outcome
            ]
            if row["tool_state"] != expected_tool_state:
                raise DomainError(ErrorCode.PRECONDITION_FAILED, "TOOL_CALL_STATE_MISMATCH")
            if result.outcome == ReconciliationOutcome.CONFIRMED_SUCCEEDED:
                expected_hash = (
                    "sha256:" + hashlib.sha256(row["resulting_content"]).hexdigest()
                    if row["resulting_content"] is not None
                    else None
                )
                if result.observed_hash is None or result.observed_hash != expected_hash:
                    raise DomainError(ErrorCode.PRECONDITION_FAILED, "RECONCILIATION_HASH_MISMATCH")
            return (
                classification,
                {
                    "result_kind": "ReconciliationResult",
                    "result_id": str(result.reconciliation_id),
                    "tool_call_id": str(result.tool_call_id),
                    "operation_id": str(result.operation_id),
                    "evidence_refs": result.evidence_refs,
                },
                allowed_run_states,
            )
        raise DomainError(ErrorCode.PRECONDITION_FAILED, "CANONICAL_RESULT_REQUIRED")

    def _transition_outcome(
        self,
        conn: Connection,
        *,
        run: WorkerRun,
        row: dict[str, Any],
        classification: str,
        request_id: UUID,
        correlation_id: UUID,
        details: dict[str, Any],
        before_outbox: Callable[[], None] | None = None,
        interruption_id: UUID | None = None,
    ) -> None:
        targets = {
            "CONFIRMED_SUCCEEDED": "SUCCEEDED",
            "NO_EFFECT": "PAUSED",
            "CONFIRMED_FAILED": "FAILED",
            "OUTCOME_UNKNOWN": "RECONCILIATION_REQUIRED",
            "NON_RECOVERABLE": "FAILED",
        }
        target = targets[classification]
        now = self.clock.now()
        run_changed = conn.execute(
            text(
                "UPDATE runs SET state=CAST(:target AS run_state),revision=revision+1,"
                "updated_at=:now WHERE id=:run AND state=CAST(:old AS run_state) "
                "AND fencing_token=:token RETURNING id"
            ),
            {
                "target": target,
                "old": row["run_state"],
                "now": now,
                "run": run.run_id,
                "token": run.fencing_token,
            },
        ).first()
        if run_changed is None:
            raise DomainError(ErrorCode.REVISION_CONFLICT, "Run outcome CAS failed")
        terminal = classification in {
            "CONFIRMED_SUCCEEDED",
            "CONFIRMED_FAILED",
            "NON_RECOVERABLE",
        }
        if terminal:
            task_target = "COMPLETED" if classification == "CONFIRMED_SUCCEEDED" else "FAILED"
            task_changed = conn.execute(
                text(
                    "UPDATE tasks SET state=CAST(:target AS task_state),revision=revision+1,"
                    "updated_at=:now WHERE id=:task AND state='RUNNING' RETURNING id"
                ),
                {"target": task_target, "now": now, "task": row["task_id"]},
            ).first()
            lease_changed = conn.execute(
                text(
                    "UPDATE leases SET state='RELEASED',expires_at=:now,updated_at=:now "
                    "WHERE resource_type='Task' AND resource_id=:task AND owner_id=:owner "
                    "AND fencing_token=:token AND state='ACTIVE' AND expires_at>:now "
                    "RETURNING resource_id"
                ),
                {
                    "now": now,
                    "task": row["task_id"],
                    "owner": run.owner_id,
                    "token": run.fencing_token,
                },
            ).first()
            if task_changed is None or lease_changed is None:
                raise DomainError(ErrorCode.REVISION_CONFLICT, "Task/lease outcome CAS failed")
        if classification != "CONFIRMED_SUCCEEDED":
            interruption_id = interruption_id or uuid4()
            phase = "PRE_DISPATCH" if classification == "NO_EFFECT" else "POST_DISPATCH"
            stored_classification = {
                "NO_EFFECT": "PRE_DISPATCH_NO_EFFECT",
                "CONFIRMED_FAILED": "POST_DISPATCH_CONFIRMED_FAILED",
                "OUTCOME_UNKNOWN": "POST_DISPATCH_UNKNOWN",
                "NON_RECOVERABLE": "NON_RECOVERABLE",
            }[classification]
            conn.execute(
                text(
                    "INSERT INTO worker_interruptions(id,run_id,owner_id,fencing_token,phase,"
                    "classification,last_confirmed_state,correlation_id,details,observed_at) "
                    "VALUES (:id,:run,:owner,:token,:phase,:class,CAST(:state AS run_state),"
                    ":correlation,CAST(:details AS jsonb),:now)"
                ),
                {
                    "id": interruption_id,
                    "run": run.run_id,
                    "owner": run.owner_id,
                    "token": run.fencing_token,
                    "phase": phase,
                    "class": stored_classification,
                    "state": row["run_state"],
                    "correlation": correlation_id,
                    "details": json.dumps(details),
                    "now": now,
                },
            )
        if before_outbox is not None:
            before_outbox()
        payload = {
            "run_id": str(run.run_id),
            "task_id": str(row["task_id"]),
            "fencing_token": run.fencing_token,
            "classification": classification,
            **details,
        }
        IntegrityService._outbox(
            conn,
            "loop.run.completed" if target == "SUCCEEDED" else "internal.worker.outcome-recorded",
            "Run",
            run.run_id,
            payload,
        )
        IntegrityService._audit(
            conn,
            request_id=request_id,
            actor_id=run.owner_id,
            command_type=(
                "internal.worker.completed"
                if target == "SUCCEEDED"
                else "internal.worker.interrupted"
            ),
            aggregate_type="Run",
            aggregate_id=run.run_id,
            previous_state=row["run_state"],
            new_state=target,
            details={
                **payload,
                "interruption_id": str(interruption_id) if interruption_id else None,
            },
        )

    def apply_execution_result(
        self,
        *,
        run: WorkerRun,
        result: EffectReceipt | ReconciliationResult | object,
        request_id: UUID,
        before_outbox: Callable[[], None] | None = None,
    ) -> str:
        with self.uow_factory() as uow:
            row = self._lock_run_bundle(uow.conn, run)
            classification, details, allowed_run_states = self._validate_canonical_result(
                uow.conn, run, result
            )
            if row["run_state"] not in allowed_run_states:
                raise DomainError(ErrorCode.PRECONDITION_FAILED, "CANONICAL_RUN_STATE_MISMATCH")
            self._transition_outcome(
                uow.conn,
                run=run,
                row=row,
                classification=classification,
                request_id=request_id,
                correlation_id=request_id,
                details=details,
                before_outbox=before_outbox,
            )
        return classification

    def record_interruption(
        self,
        *,
        run: WorkerRun,
        phase: str,
        classification: str,
        request_id: UUID,
        correlation_id: UUID,
        causation_id: UUID | None = None,
        details: dict[str, Any] | None = None,
    ) -> UUID:
        normalized = {
            "PRE_DISPATCH_NO_EFFECT": "NO_EFFECT",
            "POST_DISPATCH_CONFIRMED_FAILED": "CONFIRMED_FAILED",
            "POST_DISPATCH_UNKNOWN": "OUTCOME_UNKNOWN",
            "NON_RECOVERABLE": "NON_RECOVERABLE",
        }
        if classification not in normalized:
            raise DomainError(ErrorCode.PRECONDITION_FAILED, "Invalid interruption class")
        interruption_id = uuid4()
        with self.uow_factory() as uow:
            row = self._lock_run_bundle(uow.conn, run)
            if row["run_state"] != "RUNNING":
                raise DomainError(ErrorCode.PRECONDITION_FAILED, "Run no longer running")
            enriched = {
                **(details or {}),
                "phase": phase,
                "causation_id": str(causation_id) if causation_id else None,
            }
            self._transition_outcome(
                uow.conn,
                run=run,
                row=row,
                classification=normalized[classification],
                request_id=request_id,
                correlation_id=correlation_id,
                details=enriched,
                interruption_id=interruption_id,
            )
        return interruption_id

    def mark_unverifiable(self, *, run: WorkerRun, request_id: UUID, reason: str) -> None:
        with self.uow_factory() as uow:
            row = self._lock_run_bundle(uow.conn, run)
            if row["run_state"] not in {"RUNNING", "RECONCILIATION_REQUIRED"}:
                raise DomainError(
                    ErrorCode.PRECONDITION_FAILED, "Unverifiable result cannot alter terminal Run"
                )
            self._transition_outcome(
                uow.conn,
                run=run,
                row=row,
                classification="OUTCOME_UNKNOWN",
                request_id=request_id,
                correlation_id=request_id,
                details={"reason": reason},
            )

    def receive_once(self, *, message_id: UUID, message_type: str, payload: dict[str, Any]) -> bool:
        digest = _message_hash(message_type, payload)
        with self.uow_factory() as uow:
            row = uow.conn.execute(
                text("SELECT message_type,payload_hash FROM inbox WHERE message_id=:id FOR UPDATE"),
                {"id": message_id},
            ).first()
            if row is not None:
                if row.message_type != message_type or row.payload_hash != digest:
                    raise DomainError(ErrorCode.IDEMPOTENCY_CONFLICT, "Message identity conflict")
                return False
            uow.conn.execute(
                text(
                    "INSERT INTO inbox(message_id,message_type,payload_hash,state,released_at) "
                    "VALUES (:id,:type,:hash,'RELEASED',:now)"
                ),
                {"id": message_id, "type": message_type, "hash": digest, "now": self.clock.now()},
            )
            return True

    def _consume_local(
        self,
        *,
        message_id: UUID,
        message_type: str,
        payload: dict[str, Any],
        materialize: Callable[[Connection, dict[str, Any]], None],
    ) -> bool:
        digest = _message_hash(message_type, payload)
        with self.uow_factory() as uow:
            row = uow.conn.execute(
                text("SELECT message_type,payload_hash FROM inbox WHERE message_id=:id FOR UPDATE"),
                {"id": message_id},
            ).first()
            if row is not None:
                if row.message_type != message_type or row.payload_hash != digest:
                    raise DomainError(ErrorCode.IDEMPOTENCY_CONFLICT, "Message identity conflict")
                return False
            materialize(uow.conn, payload)
            uow.conn.execute(
                text(
                    "INSERT INTO inbox(message_id,message_type,payload_hash,state,released_at) "
                    "VALUES (:id,:type,:hash,'RELEASED',:now)"
                ),
                {"id": message_id, "type": message_type, "hash": digest, "now": self.clock.now()},
            )
            return True

    def dispatch_local(
        self,
        *,
        outbox_id: UUID,
        delivery_owner: str,
        materialize: Callable[[Connection, dict[str, Any]], None],
        before_delivery: Callable[[], None] | None = None,
        after_delivery_before_confirm: Callable[[], None] | None = None,
    ) -> bool:
        self.require_latch_clear()
        now = self.clock.now()
        with self.uow_factory() as uow:
            row = (
                uow.conn.execute(
                    text("SELECT state FROM outbox WHERE id=:id FOR UPDATE"), {"id": outbox_id}
                )
                .mappings()
                .first()
            )
            if row is None:
                raise DomainError(ErrorCode.PRECONDITION_FAILED, "Outbox message not found")
            if row["state"] == "RELEASED":
                return False
            if row["state"] not in {"QUEUED", "DELIVERING"}:
                raise DomainError(ErrorCode.PRECONDITION_FAILED, "Outbox state not dispatchable")
            claimed = (
                uow.conn.execute(
                    text(
                        "UPDATE outbox SET state='DELIVERING',attempts=attempts+1,"
                        "delivery_owner=:owner,delivery_started_at=:now,last_delivery_error=NULL "
                        "WHERE id=:id AND state=:state RETURNING message_type,payload"
                    ),
                    {"owner": delivery_owner, "now": now, "id": outbox_id, "state": row["state"]},
                )
                .mappings()
                .first()
            )
            if claimed is None:
                raise DomainError(ErrorCode.REVISION_CONFLICT, "Outbox claim lost")
            message_type = claimed["message_type"]
            payload = dict(claimed["payload"])
            IntegrityService._audit(
                uow.conn,
                request_id=uuid4(),
                actor_id=delivery_owner,
                command_type="internal.outbox.delivery.attempted",
                aggregate_type="Outbox",
                aggregate_id=outbox_id,
                previous_state=row["state"],
                new_state="DELIVERING",
                details={"redelivery": row["state"] == "DELIVERING"},
            )
        try:
            if before_delivery is not None:
                before_delivery()
            self.require_latch_clear()
            self._consume_local(
                message_id=outbox_id,
                message_type=message_type,
                payload=payload,
                materialize=materialize,
            )
            if after_delivery_before_confirm is not None:
                after_delivery_before_confirm()
        except Exception as error:
            with self.uow_factory() as uow:
                uow.conn.execute(
                    text(
                        "UPDATE outbox SET last_delivery_error=:error "
                        "WHERE id=:id AND state='DELIVERING'"
                    ),
                    {"id": outbox_id, "error": type(error).__name__},
                )
                IntegrityService._audit(
                    uow.conn,
                    request_id=uuid4(),
                    actor_id=delivery_owner,
                    command_type="internal.outbox.delivery.interrupted",
                    aggregate_type="Outbox",
                    aggregate_id=outbox_id,
                    previous_state="DELIVERING",
                    new_state="DELIVERING",
                    details={"error_type": type(error).__name__},
                )
            raise
        with self.uow_factory() as uow:
            confirmed = uow.conn.execute(
                text(
                    "UPDATE outbox SET state='RELEASED',released_at=:now "
                    "WHERE id=:id AND state='DELIVERING' RETURNING id"
                ),
                {"id": outbox_id, "now": self.clock.now()},
            ).first()
            if confirmed is None:
                raise DomainError(ErrorCode.REVISION_CONFLICT, "Outbox confirmation lost")
            IntegrityService._audit(
                uow.conn,
                request_id=outbox_id,
                actor_id=delivery_owner,
                command_type="internal.outbox.delivery.confirmed",
                aggregate_type="Outbox",
                aggregate_id=outbox_id,
                previous_state="DELIVERING",
                new_state="RELEASED",
                details={"attempts_confirmed": True},
            )
        return True


class LocalWorker:
    """Single-cycle acceptance worker; it is not a daemon or production release."""

    def __init__(self, selection: SelectionService, continuity: WorkerContinuityService) -> None:
        self.selection = selection
        self.continuity = continuity

    def run_once(
        self,
        *,
        worker_id: str,
        owner_id: str,
        authorized_goal_id: UUID,
        execute: Callable[[WorkerRun], EffectReceipt | ReconciliationResult | object],
        request_id: UUID,
        idempotency_key: str,
        after_acquisition: Callable[[], None] | None = None,
    ) -> WorkerRun:
        self.continuity.require_latch_clear()
        self.continuity.heartbeat(
            worker_id=worker_id,
            owner_id=owner_id,
            status="IDLE",
            ttl=timedelta(seconds=30),
            request_id=request_id,
        )
        run_id = uuid4()
        selected = self.selection.select_and_start(
            run_id=run_id,
            owner_id=owner_id,
            actor_id=owner_id,
            request_id=request_id,
            idempotency_key=idempotency_key,
            authorized_goal_id=authorized_goal_id,
        )
        run = WorkerRun(UUID(selected["task_id"]), run_id, owner_id, int(selected["fencing_token"]))
        self.continuity.heartbeat(
            worker_id=worker_id,
            owner_id=owner_id,
            status="RUNNING",
            ttl=timedelta(seconds=30),
            request_id=uuid4(),
        )
        if after_acquisition is not None:
            after_acquisition()
        try:
            self.continuity.require_latch_clear()
        except DomainError:
            self.continuity.record_interruption(
                run=run,
                phase="PRE_DISPATCH",
                classification="PRE_DISPATCH_NO_EFFECT",
                request_id=uuid4(),
                correlation_id=request_id,
                details={"evidence_ref": "host-latch-pre-dispatch"},
            )
            raise
        try:
            result = execute(run)
        except WorkerExecutionError as error:
            if error.non_recoverable and error.result is None:
                self.continuity.record_interruption(
                    run=run,
                    phase="EXECUTE",
                    classification="NON_RECOVERABLE",
                    request_id=uuid4(),
                    correlation_id=request_id,
                    details={"error_type": type(error).__name__},
                )
            elif (
                error.result is not None
                and not (
                    isinstance(error.result, EffectReceipt)
                    and error.result.outcome == ToolCallOutcome.SUCCEEDED
                )
                and not (
                    isinstance(error.result, ReconciliationResult)
                    and error.result.outcome == ReconciliationOutcome.CONFIRMED_SUCCEEDED
                )
            ):
                try:
                    self.continuity.apply_execution_result(
                        run=run, result=error.result, request_id=uuid4()
                    )
                except DomainError:
                    self.continuity.mark_unverifiable(
                        run=run,
                        request_id=uuid4(),
                        reason="CONTRADICTORY_OR_UNVERIFIABLE_WORKER_ERROR",
                    )
            else:
                self.continuity.mark_unverifiable(
                    run=run,
                    request_id=uuid4(),
                    reason="CONTRADICTORY_OR_UNVERIFIABLE_WORKER_ERROR",
                )
            raise
        except Exception as error:
            self.continuity.mark_unverifiable(
                run=run,
                request_id=uuid4(),
                reason=f"UNTYPED_EXCEPTION:{type(error).__name__}",
            )
            raise
        try:
            classification = self.continuity.apply_execution_result(
                run=run, result=result, request_id=uuid4()
            )
        except DomainError as validation_error:
            try:
                self.continuity.mark_unverifiable(
                    run=run,
                    request_id=uuid4(),
                    reason="CANONICAL_RESULT_VALIDATION_FAILED",
                )
            except DomainError:
                raise validation_error from None
            raise
        if classification != "CONFIRMED_SUCCEEDED":
            raise DomainError(ErrorCode.PRECONDITION_FAILED, classification)
        self.continuity.heartbeat(
            worker_id=worker_id,
            owner_id=owner_id,
            status="IDLE",
            ttl=timedelta(seconds=30),
            request_id=uuid4(),
        )
        return run
