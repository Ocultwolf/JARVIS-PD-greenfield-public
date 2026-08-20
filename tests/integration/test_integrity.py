from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from jarvis_pd.loop.application.integrity_service import IntegrityService, uow_factory
from jarvis_pd.persistence.uow import UnitOfWork
from jarvis_pd.shared.clock import SystemClock
from jarvis_pd.shared.errors import DomainError, ErrorCode


@pytest.fixture
def service(engine):
    return IntegrityService(uow_factory(engine), SystemClock())


def _create_ready(service):
    task_id = uuid4()
    service.create_task(
        task_id=task_id,
        title="Fixture task",
        acceptance_criteria=["Deterministic result"],
        actor_id="test-owner",
        request_id=uuid4(),
        idempotency_key=f"create:{task_id}",
    )
    service.mark_task_ready(task_id=task_id, actor_id="test-owner", request_id=uuid4())
    return task_id


@pytest.mark.integration
def test_state_outbox_audit_and_idempotency_are_atomic(engine, service) -> None:
    task_id = uuid4()
    arguments = dict(
        task_id=task_id,
        title="Atomic fixture",
        acceptance_criteria=["Created once"],
        actor_id="actor-1",
        request_id=uuid4(),
        idempotency_key="atomic-1",
    )
    first = service.create_task(**arguments)
    second = service.create_task(**arguments)
    assert first == second
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM tasks")).scalar_one() == 1
        assert connection.execute(text("SELECT count(*) FROM outbox")).scalar_one() == 1
        assert connection.execute(text("SELECT count(*) FROM audit_records")).scalar_one() == 1
        assert (
            connection.execute(text("SELECT count(*) FROM idempotency_records")).scalar_one() == 1
        )


@pytest.mark.integration
def test_idempotency_key_reuse_with_different_payload_is_rejected(service) -> None:
    task_id = uuid4()
    service.create_task(
        task_id=task_id,
        title="First",
        acceptance_criteria=["One"],
        actor_id="actor-1",
        request_id=uuid4(),
        idempotency_key="same-key",
    )
    with pytest.raises(DomainError) as caught:
        service.create_task(
            task_id=uuid4(),
            title="Different",
            acceptance_criteria=["Two"],
            actor_id="actor-1",
            request_id=uuid4(),
            idempotency_key="same-key",
        )
    assert caught.value.code == ErrorCode.IDEMPOTENCY_CONFLICT


@pytest.mark.integration
def test_failure_rolls_back_idempotency_and_all_materialization(engine, service) -> None:
    task_id = _create_ready(service)
    with pytest.raises(IntegrityError):
        service.create_task(
            task_id=task_id,
            title="Duplicate",
            acceptance_criteria=["Must fail"],
            actor_id="actor-2",
            request_id=uuid4(),
            idempotency_key="rolled-back",
        )
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM idempotency_records WHERE idempotency_key='rolled-back'")
            ).scalar_one()
            == 0
        )


@pytest.mark.integration
def test_two_workers_cannot_hold_same_valid_lease(engine, service) -> None:
    resource_id = uuid4()

    def acquire(owner):
        try:
            with UnitOfWork(engine) as uow:
                return service.acquire_lease(
                    uow.conn,
                    resource_id=resource_id,
                    owner_id=owner,
                    ttl=timedelta(seconds=90),
                )
        except DomainError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(acquire, ["worker-a", "worker-b"]))
    assert sum(isinstance(value, int) for value in results) == 1
    assert results.count(ErrorCode.LEASE_REQUIRED) == 1
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM leases WHERE resource_id=:id AND state='ACTIVE'"),
                {"id": resource_id},
            ).scalar_one()
            == 1
        )


@pytest.mark.integration
def test_stale_fencing_token_cannot_write(engine, service) -> None:
    task_id = _create_ready(service)
    run_id = uuid4()
    old_token = service.start_task(
        task_id=task_id,
        run_id=run_id,
        owner_id="worker-old",
        actor_id="scheduler",
        request_id=uuid4(),
    )
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE leases SET state='RELEASED',expires_at=now() WHERE resource_id=:id"),
            {"id": task_id},
        )
    with UnitOfWork(engine) as uow:
        new_token = service.acquire_lease(
            uow.conn,
            resource_id=task_id,
            owner_id="worker-new",
            ttl=timedelta(seconds=90),
        )
    assert new_token > old_token
    with pytest.raises(DomainError) as caught:
        service.complete_run(
            run_id=run_id,
            fencing_token=old_token,
            actor_id="worker-old",
            request_id=uuid4(),
        )
    assert caught.value.code == ErrorCode.FENCING_REJECTED


@pytest.mark.integration
def test_lease_lifecycle_is_guarded_monotonic_and_auditable(engine, service) -> None:
    task_id = _create_ready(service)
    owner_id = "worker-current"
    token = service.start_task(
        task_id=task_id,
        run_id=uuid4(),
        owner_id=owner_id,
        actor_id="scheduler",
        request_id=uuid4(),
    )
    with engine.connect() as connection:
        original_expiry = connection.execute(
            text("SELECT expires_at FROM leases WHERE resource_id=:id"),
            {"id": task_id},
        ).scalar_one()
    renewed_expiry = service.renew_lease(
        resource_id=task_id,
        owner_id=owner_id,
        fencing_token=token,
        ttl=timedelta(minutes=5),
        actor_id=owner_id,
        request_id=uuid4(),
    )
    assert renewed_expiry > original_expiry
    with pytest.raises(DomainError) as stale_owner:
        service.release_lease(
            resource_id=task_id,
            owner_id="worker-stale",
            fencing_token=token,
            actor_id="worker-stale",
            request_id=uuid4(),
        )
    assert stale_owner.value.code == ErrorCode.FENCING_REJECTED
    service.release_lease(
        resource_id=task_id,
        owner_id=owner_id,
        fencing_token=token,
        actor_id=owner_id,
        request_id=uuid4(),
    )
    with UnitOfWork(engine) as uow:
        next_token = service.acquire_lease(
            uow.conn,
            resource_id=task_id,
            owner_id="worker-next",
            ttl=timedelta(seconds=90),
        )
    assert next_token == token + 1
    with pytest.raises(DomainError) as stale_token:
        service.renew_lease(
            resource_id=task_id,
            owner_id=owner_id,
            fencing_token=token,
            ttl=timedelta(seconds=90),
            actor_id=owner_id,
            request_id=uuid4(),
        )
    assert stale_token.value.code == ErrorCode.FENCING_REJECTED
    with engine.connect() as connection:
        commands = (
            connection.execute(
                text(
                    "SELECT command_type FROM audit_records WHERE aggregate_id=:id "
                    "AND aggregate_type='Lease' ORDER BY sequence"
                ),
                {"id": task_id},
            )
            .scalars()
            .all()
        )
    assert commands == [
        "loop.lease.acquire.requested",
        "loop.lease.renew.requested",
        "loop.lease.release.requested",
    ]


@pytest.mark.integration
def test_expired_lease_cannot_be_renewed(engine, service) -> None:
    task_id = _create_ready(service)
    token = service.start_task(
        task_id=task_id,
        run_id=uuid4(),
        owner_id="worker-expired",
        actor_id="scheduler",
        request_id=uuid4(),
    )
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE leases SET expires_at=now() - interval '1 second' WHERE resource_id=:id"),
            {"id": task_id},
        )
    with pytest.raises(DomainError) as caught:
        service.renew_lease(
            resource_id=task_id,
            owner_id="worker-expired",
            fencing_token=token,
            ttl=timedelta(seconds=90),
            actor_id="worker-expired",
            request_id=uuid4(),
        )
    assert caught.value.code == ErrorCode.LEASE_REQUIRED


@pytest.mark.integration
def test_run_cancellation_requires_exact_approval_and_emits_no_invented_event(
    engine, service
) -> None:
    task_id = _create_ready(service)
    run_id = uuid4()
    service.start_task(
        task_id=task_id,
        run_id=run_id,
        owner_id="worker",
        actor_id="scheduler",
        request_id=uuid4(),
    )
    operation_id = uuid4()
    with pytest.raises(DomainError):
        service.cancel_run(
            run_id=run_id,
            operation_id=operation_id,
            actor_id="operator",
            request_id=uuid4(),
        )
    approval_id = uuid4()
    service.request_approval(
        approval_id=approval_id,
        operation_id=operation_id,
        actor_id="operator",
        request_id=uuid4(),
    )
    service.approve(
        approval_id=approval_id,
        human_owner_id="human-owner",
        request_id=uuid4(),
    )
    service.cancel_run(
        run_id=run_id,
        operation_id=operation_id,
        actor_id="operator",
        request_id=uuid4(),
    )
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT state FROM runs WHERE id=:id"), {"id": run_id}
            ).scalar_one()
            == "CANCELLED"
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM outbox WHERE aggregate_id=:id"), {"id": run_id}
            ).scalar_one()
            == 2
        )
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM outbox WHERE aggregate_id=:id AND "
                    "message_type IN ('security.approval.requested',"
                    "'security.approval.granted')"
                ),
                {"id": approval_id},
            ).scalar_one()
            == 2
        )
        audit = (
            connection.execute(
                text(
                    "SELECT actor_id,approval_id,previous_state,new_state "
                    "FROM audit_records WHERE command_type='loop.run.cancel.requested'"
                )
            )
            .mappings()
            .one()
        )
        assert audit == {
            "actor_id": "operator",
            "approval_id": approval_id,
            "previous_state": "RUNNING",
            "new_state": "CANCELLED",
        }


@pytest.mark.integration
def test_inbox_deduplicates_and_requires_controlled_release(engine, service) -> None:
    message_id = uuid4()
    payload = {"task_id": str(uuid4())}
    assert service.receive_inbox(
        message_id=message_id,
        message_type="loop.task.create.requested",
        payload=payload,
    )
    assert not service.receive_inbox(
        message_id=message_id,
        message_type="loop.task.create.requested",
        payload=payload,
    )
    with pytest.raises(DomainError) as caught:
        service.receive_inbox(
            message_id=message_id,
            message_type="loop.task.create.requested",
            payload={"task_id": str(uuid4())},
        )
    assert caught.value.code == ErrorCode.IDEMPOTENCY_CONFLICT
    service.release_inbox(
        message_id=message_id,
        actor_id="operator",
        request_id=uuid4(),
    )
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT state FROM inbox WHERE message_id=:id"),
                {"id": message_id},
            ).scalar_one()
            == "RELEASED"
        )
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM audit_records "
                    "WHERE command_type='infrastructure.inbox.release'"
                )
            ).scalar_one()
            == 1
        )


@pytest.mark.integration
def test_outbox_release_and_replay_are_guarded_audited_and_deduplicated(engine, service) -> None:
    task_id = uuid4()
    service.create_task(
        task_id=task_id,
        title="Replay fixture",
        acceptance_criteria=["Canonical state remains singular"],
        actor_id="actor-replay",
        request_id=uuid4(),
        idempotency_key="replay-fixture",
    )
    with engine.connect() as connection:
        message = (
            connection.execute(
                text("SELECT id,message_type,payload FROM outbox WHERE aggregate_id=:task_id"),
                {"task_id": task_id},
            )
            .mappings()
            .one()
        )
    message_id = message["id"]
    service.release_outbox(
        message_id=message_id,
        actor_id="outbox-worker",
        request_id=uuid4(),
    )
    assert service.receive_inbox(
        message_id=message_id,
        message_type=message["message_type"],
        payload=message["payload"],
    )
    with pytest.raises(DomainError) as duplicate_release:
        service.release_outbox(
            message_id=message_id,
            actor_id="outbox-worker",
            request_id=uuid4(),
        )
    assert duplicate_release.value.code == ErrorCode.PRECONDITION_FAILED
    service.replay_outbox(
        message_id=message_id,
        actor_id="operator",
        request_id=uuid4(),
    )
    with pytest.raises(DomainError) as duplicate_replay:
        service.replay_outbox(
            message_id=message_id,
            actor_id="operator",
            request_id=uuid4(),
        )
    assert duplicate_replay.value.code == ErrorCode.PRECONDITION_FAILED
    service.release_outbox(
        message_id=message_id,
        actor_id="outbox-worker",
        request_id=uuid4(),
    )
    assert not service.receive_inbox(
        message_id=message_id,
        message_type=message["message_type"],
        payload=message["payload"],
    )
    service.release_inbox(
        message_id=message_id,
        actor_id="inbox-worker",
        request_id=uuid4(),
    )
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM tasks")).scalar_one() == 1
        outbox = (
            connection.execute(
                text("SELECT state,attempts FROM outbox WHERE id=:id"),
                {"id": message_id},
            )
            .mappings()
            .one()
        )
        assert outbox == {"state": "DELIVERED", "attempts": 1}
        assert (
            connection.execute(
                text("SELECT count(*) FROM inbox WHERE message_id=:id"),
                {"id": message_id},
            ).scalar_one()
            == 1
        )
        commands = (
            connection.execute(
                text(
                    "SELECT command_type FROM audit_records WHERE aggregate_id=:id "
                    "AND aggregate_type='OutboxMessage' ORDER BY sequence"
                ),
                {"id": message_id},
            )
            .scalars()
            .all()
        )
    assert commands == [
        "infrastructure.outbox.release",
        "infrastructure.outbox.replay",
        "infrastructure.outbox.release",
    ]
