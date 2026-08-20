import json
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text

from jarvis_pd.loop.application.integrity_service import IntegrityService
from jarvis_pd.shared.errors import DomainError, ErrorCode


class SelectionService:
    """Atomic owner of the closed Gate 1 selection rule."""

    def __init__(self, uow_factory, clock) -> None:
        self.uow_factory = uow_factory
        self.clock = clock
        self._integrity = IntegrityService(uow_factory, clock)

    def select_and_start(
        self,
        *,
        run_id: UUID,
        owner_id: str,
        actor_id: str,
        request_id: UUID,
        idempotency_key: str,
        authorized_goal_id: UUID,
        latch_active: bool = False,
    ) -> dict[str, Any]:
        if latch_active:
            raise DomainError(ErrorCode.UNAUTHORIZED, "Host latch blocks work acquisition")
        request = {
            "run_id": str(run_id),
            "owner_id": owner_id,
            "authorized_goal_id": str(authorized_goal_id),
        }
        with self.uow_factory() as uow:
            # Serialize the closed G1 global limit; row locks alone only protect one Task.
            uow.conn.execute(text("SELECT pg_advisory_xact_lock(1247905793)"))
            cached = self._integrity._start_idempotent(uow.conn, actor_id, idempotency_key, request)
            if cached is not None:
                return cached
            active = uow.conn.execute(
                text(
                    "SELECT count(*) FROM runs WHERE state IN "
                    "('CREATED','READY','RUNNING','PAUSED','BLOCKED','RECONCILIATION_REQUIRED')"
                )
            ).scalar_one()
            if active >= 1:
                raise DomainError(ErrorCode.PRECONDITION_FAILED, "MAX_CONCURRENT_RUNS")
            row = (
                uow.conn.execute(
                    text(
                        "SELECT t.id FROM tasks t JOIN goals g ON g.id=t.goal_id "
                        "WHERE t.state='READY' AND g.state='ACTIVE' AND g.id=:authorized_goal_id "
                        "AND NOT EXISTS (SELECT 1 FROM task_dependencies d JOIN tasks p "
                        "ON p.id=d.depends_on_task_id WHERE d.task_id=t.id "
                        "AND p.state<>'COMPLETED') "
                        "AND NOT EXISTS (SELECT 1 FROM runs r WHERE r.task_id=t.id AND r.state IN "
                        "('CREATED','READY','RUNNING','PAUSED','BLOCKED',"
                        "'RECONCILIATION_REQUIRED')) "
                        "ORDER BY t.priority DESC,t.created_at ASC,t.id ASC "
                        "FOR UPDATE OF t SKIP LOCKED LIMIT 1"
                    ),
                    {"authorized_goal_id": authorized_goal_id},
                )
                .mappings()
                .first()
            )
            if row is None:
                raise DomainError(ErrorCode.PRECONDITION_FAILED, "NO_ELIGIBLE_WORK")
            task_id = row["id"]
            token = self._integrity.acquire_lease(
                uow.conn,
                resource_id=task_id,
                owner_id=owner_id,
                ttl=timedelta(seconds=90),
            )
            uow.conn.execute(
                text(
                    "INSERT INTO runs(id,task_id,state,fencing_token) "
                    "VALUES (:run,:task,'RUNNING',:token)"
                ),
                {"run": run_id, "task": task_id, "token": token},
            )
            uow.conn.execute(
                text(
                    "UPDATE tasks SET state='RUNNING',revision=revision+1,"
                    "updated_at=now() WHERE id=:id"
                ),
                {"id": task_id},
            )
            result = {
                "task_id": str(task_id),
                "run_id": str(run_id),
                "fencing_token": token,
            }
            IntegrityService._outbox(uow.conn, "loop.task.started", "Task", task_id, result)
            IntegrityService._outbox(uow.conn, "loop.run.started", "Run", run_id, result)
            IntegrityService._audit(
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
            uow.conn.execute(
                text(
                    "INSERT INTO audit_records(id,request_id,actor_id,command_type,aggregate_type,"
                    "aggregate_id,previous_state,new_state,outcome,details) VALUES "
                    "(:id,:request,:actor,'internal.selection.materialized','Task',:task,'READY',"
                    "'RUNNING','COMPLETED',CAST(:details AS jsonb))"
                ),
                {
                    "id": uuid4(),
                    "request": request_id,
                    "actor": actor_id,
                    "task": task_id,
                    "details": json.dumps(result),
                },
            )
            self._integrity._finish_idempotent(uow.conn, actor_id, idempotency_key, result)
            return result
