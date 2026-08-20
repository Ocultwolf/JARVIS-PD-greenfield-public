import hashlib
import json
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, text
from sqlalchemy.exc import IntegrityError

from jarvis_pd.loop.domain.states import (
    RUN_TRANSITIONS,
    TASK_TRANSITIONS,
    RunState,
    TaskState,
    require_transition,
)
from jarvis_pd.persistence.uow import UnitOfWork
from jarvis_pd.shared.errors import DomainError, ErrorCode


def _canonical_hash(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


class IntegrityService:
    """Only Phase 0 canonical writer; agents and adapters never receive its connection."""

    def __init__(self, uow_factory, clock) -> None:
        self.uow_factory = uow_factory
        self.clock = clock

    def _start_idempotent(
        self, conn: Connection, actor_id: str, key: str, request: dict[str, Any]
    ) -> dict[str, Any] | None:
        request_hash = _canonical_hash(request)
        row = (
            conn.execute(
                text(
                    "SELECT request_hash, state, result FROM idempotency_records WHERE actor_id=:actor AND idempotency_key=:key FOR UPDATE"
                ),
                {"actor": actor_id, "key": key},
            )
            .mappings()
            .first()
        )
        if row:
            if row["request_hash"] != request_hash:
                raise DomainError(
                    ErrorCode.IDEMPOTENCY_CONFLICT, "Key reused with different request"
                )
            return row["result"]
        conn.execute(
            text(
                "INSERT INTO idempotency_records(actor_id,idempotency_key,request_hash,state) VALUES (:actor,:key,:hash,'IN_PROGRESS')"
            ),
            {"actor": actor_id, "key": key, "hash": request_hash},
        )
        return None

    def _finish_idempotent(
        self, conn: Connection, actor_id: str, key: str, result: dict[str, Any]
    ) -> None:
        conn.execute(
            text(
                "UPDATE idempotency_records SET state='SUCCEEDED', result=CAST(:result AS jsonb), updated_at=now() WHERE actor_id=:actor AND idempotency_key=:key"
            ),
            {"actor": actor_id, "key": key, "result": json.dumps(result)},
        )

    @staticmethod
    def _outbox(
        conn: Connection, message_type: str, aggregate_type: str, aggregate_id: UUID, payload: dict
    ) -> None:
        conn.execute(
            text(
                "INSERT INTO outbox(id,message_type,aggregate_type,aggregate_id,payload) VALUES (:id,:type,:aggregate_type,:aggregate_id,CAST(:payload AS jsonb))"
            ),
            {
                "id": uuid4(),
                "type": message_type,
                "aggregate_type": aggregate_type,
                "aggregate_id": aggregate_id,
                "payload": json.dumps(payload),
            },
        )

    @staticmethod
    def _audit(
        conn: Connection,
        *,
        request_id: UUID,
        actor_id: str,
        command_type: str,
        aggregate_type: str,
        aggregate_id: UUID,
        previous_state: str | None,
        new_state: str | None,
        approval_id: UUID | None = None,
        details: dict | None = None,
    ) -> None:
        conn.execute(
            text("""INSERT INTO audit_records(id,request_id,actor_id,command_type,aggregate_type,aggregate_id,approval_id,previous_state,new_state,outcome,details)
                    VALUES (:id,:request,:actor,:command,:aggregate_type,:aggregate_id,:approval,:previous,:new,'COMPLETED',CAST(:details AS jsonb))"""),
            {
                "id": uuid4(),
                "request": request_id,
                "actor": actor_id,
                "command": command_type,
                "aggregate_type": aggregate_type,
                "aggregate_id": aggregate_id,
                "approval": approval_id,
                "previous": previous_state,
                "new": new_state,
                "details": json.dumps(details or {}),
            },
        )

    def create_task(
        self,
        *,
        task_id: UUID,
        title: str,
        acceptance_criteria: list[str],
        actor_id: str,
        request_id: UUID,
        idempotency_key: str,
    ) -> dict[str, Any]:
        request = {
            "task_id": str(task_id),
            "title": title,
            "acceptance_criteria": acceptance_criteria,
        }
        with self.uow_factory() as uow:
            cached = self._start_idempotent(uow.conn, actor_id, idempotency_key, request)
            if cached is not None:
                return cached
            uow.conn.execute(
                text(
                    "INSERT INTO tasks(id,title,acceptance_criteria,state) VALUES (:id,:title,CAST(:criteria AS jsonb),'PROPOSED')"
                ),
                {"id": task_id, "title": title, "criteria": json.dumps(acceptance_criteria)},
            )
            result = {"task_id": str(task_id), "state": "PROPOSED"}
            self._outbox(uow.conn, "loop.task.created", "Task", task_id, result)
            self._audit(
                uow.conn,
                request_id=request_id,
                actor_id=actor_id,
                command_type="loop.task.create.requested",
                aggregate_type="Task",
                aggregate_id=task_id,
                previous_state=None,
                new_state="PROPOSED",
            )
            self._finish_idempotent(uow.conn, actor_id, idempotency_key, result)
            return result

    def mark_task_ready(self, *, task_id: UUID, actor_id: str, request_id: UUID) -> None:
        with self.uow_factory() as uow:
            row = uow.conn.execute(
                text("SELECT state FROM tasks WHERE id=:id FOR UPDATE"), {"id": task_id}
            ).first()
            if row is None:
                raise DomainError(ErrorCode.PRECONDITION_FAILED, "Task not found")
            current = TaskState(row.state)
            require_transition(current, TaskState.READY, TASK_TRANSITIONS)
            uow.conn.execute(
                text(
                    "UPDATE tasks SET state='READY',revision=revision+1,updated_at=now() WHERE id=:id"
                ),
                {"id": task_id},
            )
            self._outbox(
                uow.conn,
                "loop.task.ready",
                "Task",
                task_id,
                {"task_id": str(task_id), "state": "READY"},
            )
            self._audit(
                uow.conn,
                request_id=request_id,
                actor_id=actor_id,
                command_type="loop.task.mark_ready.requested",
                aggregate_type="Task",
                aggregate_id=task_id,
                previous_state=current,
                new_state="READY",
            )

    def acquire_lease(
        self, conn: Connection, *, resource_id: UUID, owner_id: str, ttl: timedelta
    ) -> int:
        expires_at = self.clock.now() + ttl
        row = conn.execute(
            text("""INSERT INTO leases(resource_type,resource_id,owner_id,fencing_token,state,expires_at)
                    VALUES ('Task',:resource,:owner,1,'ACTIVE',:expires)
                    ON CONFLICT(resource_type,resource_id) DO UPDATE
                    SET owner_id=EXCLUDED.owner_id,fencing_token=leases.fencing_token+1,state='ACTIVE',expires_at=EXCLUDED.expires_at,updated_at=now()
                    WHERE leases.expires_at <= :now OR leases.state IN ('EXPIRED','RELEASED','REVOKED')
                    RETURNING fencing_token"""),
            {
                "resource": resource_id,
                "owner": owner_id,
                "expires": expires_at,
                "now": self.clock.now(),
            },
        ).first()
        if row is None:
            raise DomainError(ErrorCode.LEASE_REQUIRED, "Resource already has a valid lease")
        return int(row.fencing_token)

    def start_task(
        self, *, task_id: UUID, run_id: UUID, owner_id: str, actor_id: str, request_id: UUID
    ) -> int:
        with self.uow_factory() as uow:
            row = uow.conn.execute(
                text("SELECT state FROM tasks WHERE id=:id FOR UPDATE"), {"id": task_id}
            ).first()
            if row is None:
                raise DomainError(ErrorCode.PRECONDITION_FAILED, "Task not found")
            current = TaskState(row.state)
            require_transition(current, TaskState.RUNNING, TASK_TRANSITIONS)
            token = self.acquire_lease(
                uow.conn, resource_id=task_id, owner_id=owner_id, ttl=timedelta(seconds=90)
            )
            self._audit(
                uow.conn,
                request_id=request_id,
                actor_id=actor_id,
                command_type="loop.lease.acquire.requested",
                aggregate_type="Lease",
                aggregate_id=task_id,
                previous_state=None,
                new_state="ACTIVE",
                details={"owner_id": owner_id, "fencing_token": token},
            )
            try:
                uow.conn.execute(
                    text(
                        "INSERT INTO runs(id,task_id,state,fencing_token) VALUES (:id,:task,'RUNNING',:token)"
                    ),
                    {"id": run_id, "task": task_id, "token": token},
                )
            except IntegrityError as error:
                raise DomainError(
                    ErrorCode.PRECONDITION_FAILED, "Task already has an active Run"
                ) from error
            uow.conn.execute(
                text(
                    "UPDATE tasks SET state='RUNNING',revision=revision+1,updated_at=now() WHERE id=:id"
                ),
                {"id": task_id},
            )
            self._outbox(
                uow.conn,
                "loop.task.started",
                "Task",
                task_id,
                {"task_id": str(task_id), "run_id": str(run_id)},
            )
            self._outbox(
                uow.conn,
                "loop.run.created",
                "Run",
                run_id,
                {"run_id": str(run_id), "task_id": str(task_id)},
            )
            self._outbox(
                uow.conn,
                "loop.run.started",
                "Run",
                run_id,
                {"run_id": str(run_id), "fencing_token": token},
            )
            self._audit(
                uow.conn,
                request_id=request_id,
                actor_id=actor_id,
                command_type="loop.task.start.requested",
                aggregate_type="Task",
                aggregate_id=task_id,
                previous_state=current,
                new_state="RUNNING",
                details={"run_id": str(run_id), "fencing_token": token},
            )
            return token

    def renew_lease(
        self,
        *,
        resource_id: UUID,
        owner_id: str,
        fencing_token: int,
        ttl: timedelta,
        actor_id: str,
        request_id: UUID,
    ) -> datetime:
        now = self.clock.now()
        expires_at = now + ttl
        with self.uow_factory() as uow:
            row = (
                uow.conn.execute(
                    text(
                        "SELECT owner_id,fencing_token,state,expires_at FROM leases "
                        "WHERE resource_type='Task' AND resource_id=:resource FOR UPDATE"
                    ),
                    {"resource": resource_id},
                )
                .mappings()
                .first()
            )
            if row is None or row["state"] != "ACTIVE" or row["expires_at"] <= now:
                raise DomainError(ErrorCode.LEASE_REQUIRED, "Lease is not active")
            if row["owner_id"] != owner_id or row["fencing_token"] != fencing_token:
                raise DomainError(ErrorCode.FENCING_REJECTED, "Lease owner or token is stale")
            uow.conn.execute(
                text(
                    "UPDATE leases SET state='ACTIVE',expires_at=:expires,updated_at=now() "
                    "WHERE resource_type='Task' AND resource_id=:resource"
                ),
                {"expires": expires_at, "resource": resource_id},
            )
            self._audit(
                uow.conn,
                request_id=request_id,
                actor_id=actor_id,
                command_type="loop.lease.renew.requested",
                aggregate_type="Lease",
                aggregate_id=resource_id,
                previous_state="ACTIVE",
                new_state="ACTIVE",
                details={
                    "owner_id": owner_id,
                    "fencing_token": fencing_token,
                    "expires_at": expires_at.isoformat(),
                },
            )
            return expires_at

    def release_lease(
        self,
        *,
        resource_id: UUID,
        owner_id: str,
        fencing_token: int,
        actor_id: str,
        request_id: UUID,
    ) -> None:
        now = self.clock.now()
        with self.uow_factory() as uow:
            row = (
                uow.conn.execute(
                    text(
                        "SELECT owner_id,fencing_token,state,expires_at FROM leases "
                        "WHERE resource_type='Task' AND resource_id=:resource FOR UPDATE"
                    ),
                    {"resource": resource_id},
                )
                .mappings()
                .first()
            )
            if row is None or row["state"] != "ACTIVE" or row["expires_at"] <= now:
                raise DomainError(ErrorCode.LEASE_REQUIRED, "Lease is not active")
            if row["owner_id"] != owner_id or row["fencing_token"] != fencing_token:
                raise DomainError(ErrorCode.FENCING_REJECTED, "Lease owner or token is stale")
            uow.conn.execute(
                text(
                    "UPDATE leases SET state='RELEASED',expires_at=:now,updated_at=now() "
                    "WHERE resource_type='Task' AND resource_id=:resource"
                ),
                {"now": now, "resource": resource_id},
            )
            self._audit(
                uow.conn,
                request_id=request_id,
                actor_id=actor_id,
                command_type="loop.lease.release.requested",
                aggregate_type="Lease",
                aggregate_id=resource_id,
                previous_state="ACTIVE",
                new_state="RELEASED",
                details={"owner_id": owner_id, "fencing_token": fencing_token},
            )

    def complete_run(
        self, *, run_id: UUID, fencing_token: int, actor_id: str, request_id: UUID
    ) -> None:
        with self.uow_factory() as uow:
            row = (
                uow.conn.execute(
                    text(
                        "SELECT r.state,r.task_id,l.fencing_token,l.state AS lease_state,l.expires_at FROM runs r JOIN leases l ON l.resource_type='Task' AND l.resource_id=r.task_id WHERE r.id=:id FOR UPDATE OF r,l"
                    ),
                    {"id": run_id},
                )
                .mappings()
                .first()
            )
            if row is None:
                raise DomainError(ErrorCode.PRECONDITION_FAILED, "Run not found")
            if (
                row["fencing_token"] != fencing_token
                or row["lease_state"] != "ACTIVE"
                or row["expires_at"] <= self.clock.now()
            ):
                raise DomainError(ErrorCode.FENCING_REJECTED, "Stale fencing token")
            current = RunState(row["state"])
            require_transition(current, RunState.SUCCEEDED, RUN_TRANSITIONS)
            uow.conn.execute(
                text(
                    "UPDATE runs SET state='SUCCEEDED',revision=revision+1,updated_at=now() WHERE id=:id"
                ),
                {"id": run_id},
            )
            self._outbox(uow.conn, "loop.run.completed", "Run", run_id, {"run_id": str(run_id)})
            self._audit(
                uow.conn,
                request_id=request_id,
                actor_id=actor_id,
                command_type="loop.run.complete.requested",
                aggregate_type="Run",
                aggregate_id=run_id,
                previous_state=current,
                new_state="SUCCEEDED",
                details={"fencing_token": fencing_token},
            )

    def cancel_run(
        self, *, run_id: UUID, operation_id: UUID, actor_id: str, request_id: UUID
    ) -> None:
        """C023 has no normative success Event: persist state and audit only."""
        with self.uow_factory() as uow:
            approval = (
                uow.conn.execute(
                    text(
                        "SELECT id,state,requested_from FROM approvals WHERE operation_id=:operation FOR UPDATE"
                    ),
                    {"operation": operation_id},
                )
                .mappings()
                .first()
            )
            if (
                approval is None
                or approval["state"] != "APPROVED"
                or approval["requested_from"] != "HumanOwner"
            ):
                raise DomainError(ErrorCode.APPROVAL_REQUIRED, "Exact HumanOwner Approval required")
            row = uow.conn.execute(
                text("SELECT state FROM runs WHERE id=:id FOR UPDATE"), {"id": run_id}
            ).first()
            if row is None:
                raise DomainError(ErrorCode.PRECONDITION_FAILED, "Run not found")
            current = RunState(row.state)
            require_transition(current, RunState.CANCELLED, RUN_TRANSITIONS)
            uow.conn.execute(
                text(
                    "UPDATE runs SET state='CANCELLED',revision=revision+1,updated_at=now() WHERE id=:id"
                ),
                {"id": run_id},
            )
            uow.conn.execute(
                text("UPDATE approvals SET state='CONSUMED' WHERE id=:id"), {"id": approval["id"]}
            )
            self._audit(
                uow.conn,
                request_id=request_id,
                actor_id=actor_id,
                command_type="loop.run.cancel.requested",
                aggregate_type="Run",
                aggregate_id=run_id,
                previous_state=current,
                new_state="CANCELLED",
                approval_id=approval["id"],
            )

    def request_approval(
        self, *, approval_id: UUID, operation_id: UUID, actor_id: str, request_id: UUID
    ) -> None:
        with self.uow_factory() as uow:
            uow.conn.execute(
                text(
                    "INSERT INTO approvals(id,operation_id,state,requested_from) VALUES (:id,:operation,'PENDING','HumanOwner')"
                ),
                {"id": approval_id, "operation": operation_id},
            )
            self._outbox(
                uow.conn,
                "security.approval.requested",
                "Approval",
                approval_id,
                {"approval_id": str(approval_id), "operation_id": str(operation_id)},
            )
            self._audit(
                uow.conn,
                request_id=request_id,
                actor_id=actor_id,
                command_type="security.approval.request.requested",
                aggregate_type="Approval",
                aggregate_id=approval_id,
                previous_state=None,
                new_state="PENDING",
            )

    def approve(self, *, approval_id: UUID, human_owner_id: str, request_id: UUID) -> None:
        with self.uow_factory() as uow:
            result = uow.conn.execute(
                text(
                    "UPDATE approvals SET state='APPROVED',decided_by=:actor,decided_at=now() WHERE id=:id AND state='PENDING' RETURNING operation_id"
                ),
                {"id": approval_id, "actor": human_owner_id},
            ).first()
            if result is None:
                raise DomainError(ErrorCode.APPROVAL_MISMATCH, "Approval is not pending")
            self._outbox(
                uow.conn,
                "security.approval.granted",
                "Approval",
                approval_id,
                {
                    "approval_id": str(approval_id),
                    "operation_id": str(result.operation_id),
                },
            )
            self._audit(
                uow.conn,
                request_id=request_id,
                actor_id=human_owner_id,
                command_type="security.approval.grant.requested",
                aggregate_type="Approval",
                aggregate_id=approval_id,
                previous_state="PENDING",
                new_state="APPROVED",
            )

    def release_outbox(self, *, message_id: UUID, actor_id: str, request_id: UUID) -> None:
        with self.uow_factory() as uow:
            row = uow.conn.execute(
                text(
                    "UPDATE outbox SET state='DELIVERED',released_at=now() WHERE id=:id AND state='QUEUED' RETURNING aggregate_id"
                ),
                {"id": message_id},
            ).first()
            if row is None:
                raise DomainError(ErrorCode.PRECONDITION_FAILED, "Outbox message is not queued")
            self._audit(
                uow.conn,
                request_id=request_id,
                actor_id=actor_id,
                command_type="infrastructure.outbox.release",
                aggregate_type="OutboxMessage",
                aggregate_id=message_id,
                previous_state="QUEUED",
                new_state="DELIVERED",
            )

    def replay_outbox(self, *, message_id: UUID, actor_id: str, request_id: UUID) -> None:
        """Requeue a delivered message; consumers still deduplicate by message ID."""
        with self.uow_factory() as uow:
            row = uow.conn.execute(
                text(
                    "UPDATE outbox SET state='QUEUED',released_at=NULL,"
                    "attempts=attempts+1 WHERE id=:id AND state='DELIVERED' "
                    "RETURNING aggregate_id"
                ),
                {"id": message_id},
            ).first()
            if row is None:
                raise DomainError(
                    ErrorCode.PRECONDITION_FAILED,
                    "Only a delivered outbox message can be replayed",
                )
            self._audit(
                uow.conn,
                request_id=request_id,
                actor_id=actor_id,
                command_type="infrastructure.outbox.replay",
                aggregate_type="OutboxMessage",
                aggregate_id=message_id,
                previous_state="DELIVERED",
                new_state="QUEUED",
            )

    def receive_inbox(
        self,
        *,
        message_id: UUID,
        message_type: str,
        payload: dict[str, Any],
    ) -> bool:
        """Return True only for the first durable receipt of a message."""
        payload_hash = _canonical_hash(payload)
        with self.uow_factory() as uow:
            row = uow.conn.execute(
                text("SELECT payload_hash FROM inbox WHERE message_id=:id FOR UPDATE"),
                {"id": message_id},
            ).first()
            if row:
                if row.payload_hash != payload_hash:
                    raise DomainError(
                        ErrorCode.IDEMPOTENCY_CONFLICT,
                        "Message ID reused with different payload",
                    )
                return False
            uow.conn.execute(
                text(
                    "INSERT INTO inbox(message_id,message_type,payload_hash,state) "
                    "VALUES (:id,:type,:hash,'RECEIVED')"
                ),
                {"id": message_id, "type": message_type, "hash": payload_hash},
            )
            return True

    def release_inbox(self, *, message_id: UUID, actor_id: str, request_id: UUID) -> None:
        with self.uow_factory() as uow:
            row = uow.conn.execute(
                text(
                    "UPDATE inbox SET state='RELEASED',released_at=now() "
                    "WHERE message_id=:id AND state='RECEIVED' RETURNING message_id"
                ),
                {"id": message_id},
            ).first()
            if row is None:
                raise DomainError(
                    ErrorCode.PRECONDITION_FAILED,
                    "Inbox message is not awaiting controlled release",
                )
            self._audit(
                uow.conn,
                request_id=request_id,
                actor_id=actor_id,
                command_type="infrastructure.inbox.release",
                aggregate_type="InboxMessage",
                aggregate_id=message_id,
                previous_state="RECEIVED",
                new_state="RELEASED",
            )


def uow_factory(engine):
    return lambda: UnitOfWork(engine)
