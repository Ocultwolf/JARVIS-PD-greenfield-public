import json
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import UTC, datetime, timedelta
from threading import Event
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, text

from jarvis_pd.contracts.gate1 import (
    ApprovalDecision,
    ApprovalRequest,
    CompensationRequest,
    ReconciliationOutcome,
    SandboxLimits,
    ToolCallOutcome,
    ToolCallRequest,
)
from jarvis_pd.loop.application.approval_service import ApprovalService
from jarvis_pd.loop.application.compensation_service import (
    FixtureCompensationService,
    compensation_hash,
)
from jarvis_pd.loop.application.fixture_atomic_write import FixtureAtomicWrite
from jarvis_pd.loop.application.integrity_service import IntegrityService, uow_factory
from jarvis_pd.loop.application.reconciliation_service import FixtureReconciliationService
from jarvis_pd.loop.application.tool_effect_service import ToolEffectService, operation_hash
from jarvis_pd.shared.errors import DomainError, ErrorCode


class FixedClock:
    value = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value


def _seed(engine) -> tuple[UUID, UUID, UUID, int]:
    goal, task, run, action = uuid4(), uuid4(), uuid4(), uuid4()
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO goals(id,title,state) VALUES (:id,'G','ACTIVE')"), {"id": goal}
        )
        connection.execute(
            text(
                "INSERT INTO tasks(id,goal_id,title,acceptance_criteria,state,priority) "
                "VALUES (:id,:goal,'T',CAST(:criteria AS jsonb),'RUNNING',1)"
            ),
            {"id": task, "goal": goal, "criteria": json.dumps(["done"])},
        )
        connection.execute(
            text("INSERT INTO runs(id,task_id,state,fencing_token) VALUES (:id,:task,'RUNNING',1)"),
            {"id": run, "task": task},
        )
        connection.execute(
            text(
                "INSERT INTO actions(id,run_id,kind,state) "
                "VALUES (:id,:run,'fixture.atomic_write','AUTHORIZED')"
            ),
            {"id": action, "run": run},
        )
        connection.execute(
            text(
                "INSERT INTO leases(resource_type,resource_id,owner_id,"
                "fencing_token,state,expires_at) "
                "VALUES ('Task',:task,'worker',1,'ACTIVE',:expires)"
            ),
            {"task": task, "expires": FixedClock.value + timedelta(hours=1)},
        )
    return goal, run, action, 1


def _request(
    run: UUID, action: UUID, approval: UUID, content: bytes, *, key="effect-1"
) -> ToolCallRequest:
    value = ToolCallRequest(
        contract="jarvis.tool-call-request",
        schema_version="1.0.0",
        tool_call_id=uuid4(),
        operation_id=uuid4(),
        action_id=action,
        run_id=run,
        tool_id="fixture.atomic_write",
        actor_id="worker",
        target_relative_path="result/output.txt",
        content_hash=FixtureAtomicWrite.digest(content),
        operation_hash="sha256:" + "0" * 64,
        idempotency_key=key,
        approval_id=approval,
        risk_class="R1",
        effect_class="COMPENSATABLE",
        sandbox=SandboxLimits(
            network_enabled=False,
            allowed_operations=["fixture.atomic_write"],
            max_files=1,
            max_bytes=4096,
            timeout_seconds=30,
        ),
    )
    return value.model_copy(update={"operation_hash": operation_hash(value)})


def _approve(engine, clock, request: ToolCallRequest, approval_id: UUID) -> None:
    service = ApprovalService(uow_factory(engine), clock)
    approval = ApprovalRequest(
        contract="jarvis.approval-request",
        schema_version="1.0.0",
        approval_request_id=approval_id,
        operation_id=request.operation_id,
        operation_hash=request.operation_hash,
        requested_from="human-owner",
        requested_at=clock.now(),
        expires_at=clock.now() + timedelta(minutes=30),
        max_uses=1,
        status="PENDING",
    )
    service.request(approval, requested_by="worker", request_id=uuid4())
    service.decide(
        ApprovalDecision(
            contract="jarvis.approval-decision",
            schema_version="1.0.0",
            approval_request_id=approval_id,
            operation_hash=request.operation_hash,
            decision="APPROVED",
            decided_by="human-owner",
            decided_at=clock.now(),
            decision_reason="Synthetic acceptance",
        ),
        authenticated_actor_id="human-owner",
        request_id=uuid4(),
    )


@pytest.fixture
def wp3(engine, tmp_path):
    clock = FixedClock()
    root = tmp_path / "sandbox"
    (root / "result").mkdir(parents=True)
    adapter = FixtureAtomicWrite(root)
    service = ToolEffectService(uow_factory(engine), clock, adapter, lambda: False)
    return clock, root, adapter, service


@pytest.mark.integration
def test_approval_single_use_effect_receipt_and_atomic_records(engine, wp3) -> None:
    clock, root, _, service = wp3
    goal, run, action, token = _seed(engine)
    approval_id = uuid4()
    request = _request(run, action, approval_id, b"confirmed")
    _approve(engine, clock, request, approval_id)
    receipt = service.execute(
        request,
        b"confirmed",
        owner_id="worker",
        fencing_token=token,
        authorized_goal_id=goal,
        request_id=uuid4(),
    )
    assert receipt.outcome == ToolCallOutcome.SUCCEEDED and receipt.verified
    assert (root / "result/output.txt").read_bytes() == b"confirmed"
    replay = service.execute(
        request,
        b"confirmed",
        owner_id="worker",
        fencing_token=token,
        authorized_goal_id=goal,
        request_id=uuid4(),
    )
    assert replay.receipt_id == receipt.receipt_id
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT state FROM approvals WHERE id=:id"), {"id": approval_id}
            ).scalar_one()
            == "CONSUMED"
        )
        assert connection.execute(text("SELECT count(*) FROM effect_receipts")).scalar_one() == 1
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM outbox WHERE message_type='internal.tool-effect.recorded'"
                )
            ).scalar_one()
            == 1
        )


@pytest.mark.integration
def test_pre_effect_revalidation_rejects_changed_fencing_without_effect(engine, wp3) -> None:
    clock, root, _, service = wp3
    goal, run, action, token = _seed(engine)
    approval_id = uuid4()
    request = _request(run, action, approval_id, b"blocked")
    _approve(engine, clock, request, approval_id)

    def stale() -> None:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE leases SET fencing_token=2 WHERE resource_id="
                    "(SELECT task_id FROM runs WHERE id=:run)"
                ),
                {"run": run},
            )

    with pytest.raises(DomainError) as caught:
        service.execute(
            request,
            b"blocked",
            owner_id="worker",
            fencing_token=token,
            authorized_goal_id=goal,
            request_id=uuid4(),
            before_pre_effect=stale,
        )
    assert caught.value.code == ErrorCode.FENCING_REJECTED
    assert not (root / "result/output.txt").exists()


@pytest.mark.integration
def test_ambiguous_timeout_is_unknown_without_replay_and_reconciles(engine, wp3) -> None:
    clock, root, adapter, service = wp3
    goal, run, action, token = _seed(engine)
    approval_id = uuid4()
    request = _request(run, action, approval_id, b"maybe")
    _approve(engine, clock, request, approval_id)
    receipt = service.execute(
        request,
        b"maybe",
        owner_id="worker",
        fencing_token=token,
        authorized_goal_id=goal,
        request_id=uuid4(),
        ambiguous_after_write=True,
    )
    assert receipt.outcome == ToolCallOutcome.OUTCOME_UNKNOWN and not receipt.verified
    assert (root / "result/output.txt").read_bytes() == b"maybe"
    replay = service.execute(
        request,
        b"maybe",
        owner_id="worker",
        fencing_token=token,
        authorized_goal_id=goal,
        request_id=uuid4(),
    )
    assert replay.outcome == ToolCallOutcome.OUTCOME_UNKNOWN
    reconciler = FixtureReconciliationService(uow_factory(engine), clock, adapter)
    result = reconciler.reconcile(
        tool_call_id=request.tool_call_id,
        owner_id="worker",
        fencing_token=token,
        idempotency_key="reconcile-1",
        request_id=uuid4(),
    )
    assert result.outcome == ReconciliationOutcome.CONFIRMED_SUCCEEDED
    assert (
        reconciler.reconcile(
            tool_call_id=request.tool_call_id,
            owner_id="worker",
            fencing_token=token,
            idempotency_key="reconcile-1",
            request_id=uuid4(),
        )
        == result
    )
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT state FROM runs WHERE id=:id"), {"id": run}
            ).scalar_one()
            == "SUCCEEDED"
        )


@pytest.mark.integration
@pytest.mark.parametrize(
    ("replacement", "expected"),
    [
        (None, ReconciliationOutcome.NO_EFFECT),
        (b"contradictory", ReconciliationOutcome.UNRESOLVED),
    ],
)
def test_reconciliation_confirms_absence_or_preserves_unknown(
    engine, wp3, replacement, expected
) -> None:
    clock, root, adapter, service = wp3
    goal, run, action, token = _seed(engine)
    approval_id = uuid4()
    request = _request(run, action, approval_id, b"ambiguous")
    _approve(engine, clock, request, approval_id)
    service.execute(
        request,
        b"ambiguous",
        owner_id="worker",
        fencing_token=token,
        authorized_goal_id=goal,
        request_id=uuid4(),
        ambiguous_after_write=True,
    )
    target = root / "result/output.txt"
    if replacement is None:
        target.unlink()
    else:
        target.write_bytes(replacement)
    result = FixtureReconciliationService(uow_factory(engine), clock, adapter).reconcile(
        tool_call_id=request.tool_call_id,
        owner_id="worker",
        fencing_token=token,
        idempotency_key="reconcile-observation",
        request_id=uuid4(),
    )
    assert result.outcome == expected
    with engine.connect() as connection:
        state = connection.execute(
            text("SELECT state FROM runs WHERE id=:id"), {"id": run}
        ).scalar_one()
    expected_state = (
        "FAILED" if expected == ReconciliationOutcome.NO_EFFECT else "RECONCILIATION_REQUIRED"
    )
    assert state == expected_state


@pytest.mark.integration
def test_denied_approval_is_rejected_without_effect(engine, wp3) -> None:
    clock, root, _, service = wp3
    goal, run, action, token = _seed(engine)
    approval_id = uuid4()
    request = _request(run, action, approval_id, b"denied")
    approvals = ApprovalService(uow_factory(engine), clock)
    approvals.request(
        ApprovalRequest(
            contract="jarvis.approval-request",
            schema_version="1.0.0",
            approval_request_id=approval_id,
            operation_id=request.operation_id,
            operation_hash=request.operation_hash,
            requested_from="human-owner",
            requested_at=clock.now(),
            expires_at=clock.now() + timedelta(minutes=10),
            max_uses=1,
            status="PENDING",
        ),
        requested_by="worker",
        request_id=uuid4(),
    )
    approvals.decide(
        ApprovalDecision(
            contract="jarvis.approval-decision",
            schema_version="1.0.0",
            approval_request_id=approval_id,
            operation_hash=request.operation_hash,
            decision="DENIED",
            decided_by="human-owner",
            decided_at=clock.now(),
            decision_reason="Denied fixture",
        ),
        authenticated_actor_id="human-owner",
        request_id=uuid4(),
    )
    with pytest.raises(DomainError) as denied:
        service.execute(
            request,
            b"denied",
            owner_id="worker",
            fencing_token=token,
            authorized_goal_id=goal,
            request_id=uuid4(),
        )
    assert denied.value.code == ErrorCode.APPROVAL_MISMATCH
    assert not (root / "result/output.txt").exists()


@pytest.mark.integration
def test_concurrent_double_consumption_produces_one_effect(engine, wp3) -> None:
    clock, root, _, service = wp3
    goal, run, action, token = _seed(engine)
    approval_id = uuid4()
    request = _request(run, action, approval_id, b"once")
    _approve(engine, clock, request, approval_id)

    def invoke(index):
        try:
            return service.execute(
                request,
                b"once",
                owner_id="worker",
                fencing_token=token,
                authorized_goal_id=goal,
                request_id=uuid4(),
            )
        except DomainError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(invoke, (1, 2)))
    receipts = [value for value in results if hasattr(value, "receipt_id")]
    assert receipts
    assert len({value.receipt_id for value in receipts}) == 1
    assert (root / "result/output.txt").read_bytes() == b"once"
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM effect_receipts")).scalar_one() == 1
        assert (
            connection.execute(
                text("SELECT uses_consumed FROM approvals WHERE id=:id"), {"id": approval_id}
            ).scalar_one()
            == 1
        )


@pytest.mark.integration
def test_scope_latch_action_and_expiry_revalidate_to_safe_denial(engine, wp3) -> None:
    clock, root, adapter, service = wp3
    goal, run, action, token = _seed(engine)
    approval_id = uuid4()
    request = _request(run, action, approval_id, b"blocked")
    _approve(engine, clock, request, approval_id)
    with pytest.raises(DomainError):
        service.execute(
            request,
            b"blocked",
            owner_id="worker",
            fencing_token=token,
            authorized_goal_id=uuid4(),
            request_id=uuid4(),
        )
    latched = ToolEffectService(uow_factory(engine), clock, adapter, lambda: True)
    with pytest.raises(DomainError):
        latched.execute(
            request,
            b"blocked",
            owner_id="worker",
            fencing_token=token,
            authorized_goal_id=goal,
            request_id=uuid4(),
        )
    with pytest.raises(DomainError):
        wrong_action = request.model_copy(update={"action_id": uuid4()})
        wrong_action = wrong_action.model_copy(
            update={"operation_hash": operation_hash(wrong_action)}
        )
        service.execute(
            wrong_action,
            b"blocked",
            owner_id="worker",
            fencing_token=token,
            authorized_goal_id=goal,
            request_id=uuid4(),
        )
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE approvals SET expires_at=:expired WHERE id=:id"),
            {"expired": clock.now() - timedelta(seconds=1), "id": approval_id},
        )
    with pytest.raises(DomainError) as expired:
        service.execute(
            request,
            b"blocked",
            owner_id="worker",
            fencing_token=token,
            authorized_goal_id=goal,
            request_id=uuid4(),
        )
    assert expired.value.code == ErrorCode.APPROVAL_MISMATCH
    assert not (root / "result/output.txt").exists()


@pytest.mark.integration
def test_compensation_is_separate_approved_idempotent_and_audited(engine, wp3) -> None:
    clock, root, adapter, service = wp3
    target = root / "result/output.txt"
    target.write_bytes(b"before")
    goal, run, action, token = _seed(engine)
    approval_id = uuid4()
    effect = _request(run, action, approval_id, b"after")
    _approve(engine, clock, effect, approval_id)
    service.execute(
        effect,
        b"after",
        owner_id="worker",
        fencing_token=token,
        authorized_goal_id=goal,
        request_id=uuid4(),
    )
    compensation = CompensationRequest(
        contract="jarvis.compensation-request",
        schema_version="1.0.0",
        compensation_id=uuid4(),
        original_tool_call_id=effect.tool_call_id,
        operation_id=uuid4(),
        operation_hash="sha256:" + "0" * 64,
        actor_id="worker",
        idempotency_key="compensate-1",
        reason="Restore synthetic fixture",
        expected_result_hash=FixtureAtomicWrite.digest(b"before"),
    )
    compensation = compensation.model_copy(
        update={"operation_hash": compensation_hash(compensation)}
    )
    compensation_approval = uuid4()
    _approve_compensation(engine, clock, compensation, compensation_approval)
    compensator = FixtureCompensationService(uow_factory(engine), clock, adapter)
    receipt = compensator.compensate(
        compensation,
        owner_id="worker",
        fencing_token=token,
        request_id=uuid4(),
    )
    assert receipt["outcome"] == "SUCCEEDED"
    assert target.read_bytes() == b"before"
    assert (
        compensator.compensate(
            compensation,
            owner_id="worker",
            fencing_token=token,
            request_id=uuid4(),
        )
        == receipt
    )


def _approve_compensation(engine, clock, request, approval_id) -> None:
    service = ApprovalService(uow_factory(engine), clock)
    service.request(
        ApprovalRequest(
            contract="jarvis.approval-request",
            schema_version="1.0.0",
            approval_request_id=approval_id,
            operation_id=request.operation_id,
            operation_hash=request.operation_hash,
            requested_from="human-owner",
            requested_at=clock.now(),
            expires_at=clock.now() + timedelta(minutes=10),
            max_uses=1,
            status="PENDING",
        ),
        requested_by="worker",
        request_id=uuid4(),
    )
    service.decide(
        ApprovalDecision(
            contract="jarvis.approval-decision",
            schema_version="1.0.0",
            approval_request_id=approval_id,
            operation_hash=request.operation_hash,
            decision="APPROVED",
            decided_by="human-owner",
            decided_at=clock.now(),
            decision_reason="Approve compensation",
        ),
        authenticated_actor_id="human-owner",
        request_id=uuid4(),
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    ("fault_phase", "expected_outcome", "expected_run"),
    [
        ("before_replace", ToolCallOutcome.FAILED, "RUNNING"),
        ("after_replace", ToolCallOutcome.OUTCOME_UNKNOWN, "RECONCILIATION_REQUIRED"),
        (
            "after_directory_fsync",
            ToolCallOutcome.OUTCOME_UNKNOWN,
            "RECONCILIATION_REQUIRED",
        ),
        (
            "after_verification_read",
            ToolCallOutcome.OUTCOME_UNKNOWN,
            "RECONCILIATION_REQUIRED",
        ),
    ],
)
def test_filesystem_commit_point_classifies_faults_without_redispatch(
    engine, tmp_path, fault_phase, expected_outcome, expected_run
) -> None:
    clock = FixedClock()
    root = tmp_path / "fault-sandbox"
    (root / "result").mkdir(parents=True)
    dispatches = 0

    def fault(phase: str) -> None:
        nonlocal dispatches
        if phase == "before_replace":
            dispatches += 1
        if phase == fault_phase:
            raise OSError("injected")

    adapter = FixtureAtomicWrite(root, fault)
    service = ToolEffectService(uow_factory(engine), clock, adapter, lambda: False)
    goal, run, action, token = _seed(engine)
    approval_id = uuid4()
    request = _request(run, action, approval_id, b"materialized")
    _approve(engine, clock, request, approval_id)

    receipt = service.execute(
        request,
        b"materialized",
        owner_id="worker",
        fencing_token=token,
        authorized_goal_id=goal,
        request_id=uuid4(),
    )
    assert receipt.outcome == expected_outcome
    replay = service.execute(
        request,
        b"materialized",
        owner_id="worker",
        fencing_token=token,
        authorized_goal_id=goal,
        request_id=uuid4(),
    )
    assert replay.receipt_id == receipt.receipt_id
    assert dispatches == 1
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT state FROM runs WHERE id=:id"), {"id": run}
            ).scalar_one()
            == expected_run
        )
        assert (
            connection.execute(
                text("SELECT state FROM approvals WHERE id=:id"), {"id": approval_id}
            ).scalar_one()
            == "CONSUMED"
        )

    if expected_outcome == ToolCallOutcome.OUTCOME_UNKNOWN:
        adapter.fault_hook = None
        result = FixtureReconciliationService(uow_factory(engine), clock, adapter).reconcile(
            tool_call_id=request.tool_call_id,
            owner_id="worker",
            fencing_token=token,
            idempotency_key=f"reconcile-{fault_phase}",
            request_id=uuid4(),
        )
        assert result.outcome == ReconciliationOutcome.CONFIRMED_SUCCEEDED


@pytest.mark.integration
def test_expired_approval_is_durable_audited_and_idempotent(engine) -> None:
    clock = FixedClock()
    service = ApprovalService(uow_factory(engine), clock)
    approval_id, operation_id = uuid4(), uuid4()
    operation_digest = "sha256:" + "a" * 64
    service.request(
        ApprovalRequest(
            contract="jarvis.approval-request",
            schema_version="1.0.0",
            approval_request_id=approval_id,
            operation_id=operation_id,
            operation_hash=operation_digest,
            requested_from="human-owner",
            requested_at=clock.now() - timedelta(hours=2),
            expires_at=clock.now() - timedelta(hours=1),
            max_uses=1,
            status="PENDING",
        ),
        requested_by="worker",
        request_id=uuid4(),
    )
    decision = ApprovalDecision(
        contract="jarvis.approval-decision",
        schema_version="1.0.0",
        approval_request_id=approval_id,
        operation_hash=operation_digest,
        decision="APPROVED",
        decided_by="human-owner",
        decided_at=clock.now(),
        decision_reason="Too late",
    )
    for _ in range(2):
        with pytest.raises(DomainError) as caught:
            service.decide(
                decision,
                authenticated_actor_id="human-owner",
                request_id=uuid4(),
            )
        assert caught.value.code == ErrorCode.APPROVAL_MISMATCH
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT state FROM approvals WHERE id=:id"), {"id": approval_id}
            ).scalar_one()
            == "EXPIRED"
        )
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM audit_records WHERE aggregate_id=:id "
                    "AND new_state='EXPIRED'"
                ),
                {"id": approval_id},
            ).scalar_one()
            == 1
        )
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM outbox WHERE aggregate_id=:id "
                    "AND message_type='internal.approval.expired'"
                ),
                {"id": approval_id},
            ).scalar_one()
            == 1
        )


@pytest.mark.integration
def test_ambiguous_compensation_blocks_reconciles_and_preserves_original_effect(
    engine, wp3
) -> None:
    clock, root, adapter, service = wp3
    target = root / "result/output.txt"
    target.write_bytes(b"before")
    goal, run, action, token = _seed(engine)
    approval_id = uuid4()
    effect = _request(run, action, approval_id, b"after")
    _approve(engine, clock, effect, approval_id)
    original_receipt = service.execute(
        effect,
        b"after",
        owner_id="worker",
        fencing_token=token,
        authorized_goal_id=goal,
        request_id=uuid4(),
    )
    compensation = CompensationRequest(
        contract="jarvis.compensation-request",
        schema_version="1.0.0",
        compensation_id=uuid4(),
        original_tool_call_id=effect.tool_call_id,
        operation_id=uuid4(),
        operation_hash="sha256:" + "0" * 64,
        actor_id="worker",
        idempotency_key="ambiguous-compensation",
        reason="Restore exact prior fixture",
        expected_result_hash=FixtureAtomicWrite.digest(b"before"),
    )
    compensation = compensation.model_copy(
        update={"operation_hash": compensation_hash(compensation)}
    )
    _approve_compensation(engine, clock, compensation, uuid4())

    def post_replace(phase: str) -> None:
        if phase == "after_replace":
            raise OSError("response lost")

    adapter.fault_hook = post_replace
    compensator = FixtureCompensationService(uow_factory(engine), clock, adapter)
    receipt = compensator.compensate(
        compensation,
        owner_id="worker",
        fencing_token=token,
        request_id=uuid4(),
    )
    assert receipt["outcome"] == "OUTCOME_UNKNOWN"
    assert target.read_bytes() == b"before"
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT state FROM runs WHERE id=:id"), {"id": run}
            ).scalar_one()
            == "RECONCILIATION_REQUIRED"
        )
        assert connection.execute(
            text("SELECT document FROM effect_receipts WHERE tool_call_id=:id"),
            {"id": effect.tool_call_id},
        ).scalar_one()["receipt_id"] == str(original_receipt.receipt_id)

    adapter.fault_hook = None
    result = compensator.reconcile(
        compensation_id=compensation.compensation_id,
        owner_id="worker",
        fencing_token=token,
        idempotency_key="compensation-observation-1",
        request_id=uuid4(),
    )
    assert result["outcome"] == "COMPENSATION_CONFIRMED"
    assert (
        compensator.reconcile(
            compensation_id=compensation.compensation_id,
            owner_id="worker",
            fencing_token=token,
            idempotency_key="compensation-observation-1",
            request_id=uuid4(),
        )
        == result
    )
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT state FROM runs WHERE id=:id"), {"id": run}
            ).scalar_one()
            == "RUNNING"
        )


@pytest.mark.integration
def test_compensation_expected_hash_mismatch_cannot_consume_approval_or_dispatch(
    engine, wp3
) -> None:
    clock, root, adapter, service = wp3
    target = root / "result/output.txt"
    target.write_bytes(b"before")
    goal, run, action, token = _seed(engine)
    approval_id = uuid4()
    effect = _request(run, action, approval_id, b"after")
    _approve(engine, clock, effect, approval_id)
    service.execute(
        effect,
        b"after",
        owner_id="worker",
        fencing_token=token,
        authorized_goal_id=goal,
        request_id=uuid4(),
    )
    wrong = CompensationRequest(
        contract="jarvis.compensation-request",
        schema_version="1.0.0",
        compensation_id=uuid4(),
        original_tool_call_id=effect.tool_call_id,
        operation_id=uuid4(),
        operation_hash="sha256:" + "0" * 64,
        actor_id="worker",
        idempotency_key="wrong-expected-result",
        reason="Wrong approved expectation",
        expected_result_hash=FixtureAtomicWrite.digest(b"not-before"),
    )
    wrong = wrong.model_copy(update={"operation_hash": compensation_hash(wrong)})
    compensation_approval = uuid4()
    _approve_compensation(engine, clock, wrong, compensation_approval)
    with pytest.raises(DomainError) as caught:
        FixtureCompensationService(uow_factory(engine), clock, adapter).compensate(
            wrong,
            owner_id="worker",
            fencing_token=token,
            request_id=uuid4(),
        )
    assert caught.value.code == ErrorCode.PRECONDITION_FAILED
    assert target.read_bytes() == b"after"
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT state FROM approvals WHERE id=:id"),
                {"id": compensation_approval},
            ).scalar_one()
            == "APPROVED"
        )


@pytest.mark.integration
@pytest.mark.parametrize("phase", ["after_replace", "after_directory_fsync"])
@pytest.mark.parametrize("authority_loss", ["expiry", "owner", "fencing"])
def test_post_effect_authority_loss_is_prearmed_for_observational_reconciliation(
    engine, tmp_path, phase, authority_loss
) -> None:
    clock = FixedClock()
    root = tmp_path / f"post-effect-{phase}-{authority_loss}"
    (root / "result").mkdir(parents=True)
    goal, run, action, token = _seed(engine)
    approval_id = uuid4()
    request = _request(run, action, approval_id, b"materialized")
    _approve(engine, clock, request, approval_id)
    dispatches = 0

    def lose_authority(observed_phase: str) -> None:
        nonlocal dispatches
        if observed_phase == "after_replace":
            dispatches += 1
        if observed_phase != phase:
            return
        with engine.begin() as connection:
            if authority_loss == "expiry":
                connection.execute(
                    text(
                        "UPDATE leases SET expires_at=:now WHERE resource_id="
                        "(SELECT task_id FROM runs WHERE id=:run)"
                    ),
                    {"now": clock.now(), "run": run},
                )
            elif authority_loss == "owner":
                connection.execute(
                    text(
                        "UPDATE leases SET owner_id='successor' WHERE resource_id="
                        "(SELECT task_id FROM runs WHERE id=:run)"
                    ),
                    {"run": run},
                )
            else:
                connection.execute(
                    text("UPDATE runs SET fencing_token=2 WHERE id=:run"), {"run": run}
                )
                connection.execute(
                    text(
                        "UPDATE leases SET fencing_token=2 WHERE resource_id="
                        "(SELECT task_id FROM runs WHERE id=:run)"
                    ),
                    {"run": run},
                )

    adapter = FixtureAtomicWrite(root, lose_authority)
    service = ToolEffectService(uow_factory(engine), clock, adapter, lambda: False)
    with pytest.raises(DomainError) as stale:
        service.execute(
            request,
            b"materialized",
            owner_id="worker",
            fencing_token=token,
            authorized_goal_id=goal,
            request_id=uuid4(),
        )
    expected = (
        ErrorCode.LEASE_REQUIRED if authority_loss == "expiry" else ErrorCode.FENCING_REJECTED
    )
    assert stale.value.code == expected
    assert dispatches == 1
    assert (root / "result/output.txt").read_bytes() == b"materialized"
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT state FROM tool_calls WHERE id=:id"), {"id": request.tool_call_id}
            ).scalar_one()
            == "OUTCOME_UNKNOWN"
        )
        assert (
            connection.execute(
                text("SELECT state FROM runs WHERE id=:id"), {"id": run}
            ).scalar_one()
            == "RECONCILIATION_REQUIRED"
        )
        assert (
            connection.execute(
                text("SELECT state FROM approvals WHERE id=:id"), {"id": approval_id}
            ).scalar_one()
            == "CONSUMED"
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM effect_receipts WHERE tool_call_id=:id"),
                {"id": request.tool_call_id},
            ).scalar_one()
            == 0
        )

    current_owner, current_token = "worker", 1
    with engine.begin() as connection:
        if authority_loss == "expiry":
            connection.execute(
                text(
                    "UPDATE leases SET expires_at=:future WHERE resource_id="
                    "(SELECT task_id FROM runs WHERE id=:run)"
                ),
                {"future": clock.now() + timedelta(hours=1), "run": run},
            )
        elif authority_loss == "owner":
            current_owner = "successor"
        else:
            current_token = 2
    adapter.fault_hook = None
    result = FixtureReconciliationService(uow_factory(engine), clock, adapter).reconcile(
        tool_call_id=request.tool_call_id,
        owner_id=current_owner,
        fencing_token=current_token,
        idempotency_key=f"post-effect-{phase}-{authority_loss}",
        request_id=uuid4(),
    )
    assert result.outcome == ReconciliationOutcome.CONFIRMED_SUCCEEDED
    assert dispatches == 1


@pytest.mark.integration
def test_reconciliation_replay_revalidates_authority_and_conflicts_on_new_authority(
    engine, wp3
) -> None:
    clock, _, adapter, service = wp3
    goal, run, action, token = _seed(engine)
    approval_id = uuid4()
    request = _request(run, action, approval_id, b"replay-authority")
    _approve(engine, clock, request, approval_id)
    service.execute(
        request,
        b"replay-authority",
        owner_id="worker",
        fencing_token=token,
        authorized_goal_id=goal,
        request_id=uuid4(),
        ambiguous_after_write=True,
    )
    reconciler = FixtureReconciliationService(uow_factory(engine), clock, adapter)
    first = reconciler.reconcile(
        tool_call_id=request.tool_call_id,
        owner_id="worker",
        fencing_token=1,
        idempotency_key="authority-bound-replay",
        request_id=uuid4(),
    )
    assert (
        reconciler.reconcile(
            tool_call_id=request.tool_call_id,
            owner_id="worker",
            fencing_token=1,
            idempotency_key="authority-bound-replay",
            request_id=uuid4(),
        )
        == first
    )
    with engine.begin() as connection:
        connection.execute(text("UPDATE runs SET fencing_token=2 WHERE id=:run"), {"run": run})
        connection.execute(
            text(
                "UPDATE leases SET owner_id='successor',fencing_token=2 WHERE resource_id="
                "(SELECT task_id FROM runs WHERE id=:run)"
            ),
            {"run": run},
        )
    with pytest.raises(DomainError) as stale:
        reconciler.reconcile(
            tool_call_id=request.tool_call_id,
            owner_id="worker",
            fencing_token=1,
            idempotency_key="authority-bound-replay",
            request_id=uuid4(),
        )
    assert stale.value.code == ErrorCode.FENCING_REJECTED
    with pytest.raises(DomainError) as conflict:
        reconciler.reconcile(
            tool_call_id=request.tool_call_id,
            owner_id="successor",
            fencing_token=2,
            idempotency_key="authority-bound-replay",
            request_id=uuid4(),
        )
    assert conflict.value.code == ErrorCode.IDEMPOTENCY_CONFLICT


@pytest.mark.integration
@pytest.mark.parametrize("phase", ["after_replace", "after_directory_fsync"])
@pytest.mark.parametrize("authority_loss", ["expiry", "owner", "fencing"])
def test_post_restore_authority_loss_preserves_effect_and_arms_compensation_reconciliation(
    engine, wp3, phase, authority_loss
) -> None:
    clock, root, adapter, effect_service = wp3
    target = root / "result/output.txt"
    target.write_bytes(b"before")
    goal, run, action, token = _seed(engine)
    effect_approval = uuid4()
    effect = _request(run, action, effect_approval, b"after")
    _approve(engine, clock, effect, effect_approval)
    original_receipt = effect_service.execute(
        effect,
        b"after",
        owner_id="worker",
        fencing_token=token,
        authorized_goal_id=goal,
        request_id=uuid4(),
    )
    compensation = CompensationRequest(
        contract="jarvis.compensation-request",
        schema_version="1.0.0",
        compensation_id=uuid4(),
        original_tool_call_id=effect.tool_call_id,
        operation_id=uuid4(),
        operation_hash="sha256:" + "0" * 64,
        actor_id="worker",
        idempotency_key=f"post-restore-{phase}-{authority_loss}",
        reason="Restore exact prior fixture",
        expected_result_hash=FixtureAtomicWrite.digest(b"before"),
    )
    compensation = compensation.model_copy(
        update={"operation_hash": compensation_hash(compensation)}
    )
    _approve_compensation(engine, clock, compensation, uuid4())
    dispatches = 0

    def lose_authority(observed_phase: str) -> None:
        nonlocal dispatches
        if observed_phase == "after_replace":
            dispatches += 1
        if observed_phase != phase:
            return
        with engine.begin() as connection:
            if authority_loss == "expiry":
                connection.execute(
                    text(
                        "UPDATE leases SET expires_at=:now WHERE resource_id="
                        "(SELECT task_id FROM runs WHERE id=:run)"
                    ),
                    {"now": clock.now(), "run": run},
                )
            elif authority_loss == "owner":
                connection.execute(
                    text(
                        "UPDATE leases SET owner_id='successor' WHERE resource_id="
                        "(SELECT task_id FROM runs WHERE id=:run)"
                    ),
                    {"run": run},
                )
            else:
                connection.execute(
                    text("UPDATE runs SET fencing_token=2 WHERE id=:run"), {"run": run}
                )
                connection.execute(
                    text(
                        "UPDATE leases SET fencing_token=2 WHERE resource_id="
                        "(SELECT task_id FROM runs WHERE id=:run)"
                    ),
                    {"run": run},
                )

    adapter.fault_hook = lose_authority
    compensator = FixtureCompensationService(uow_factory(engine), clock, adapter)
    with pytest.raises(DomainError):
        compensator.compensate(
            compensation,
            owner_id="worker",
            fencing_token=1,
            request_id=uuid4(),
        )
    assert target.read_bytes() == b"before"
    assert dispatches == 1
    with engine.connect() as connection:
        operation = connection.execute(
            text("SELECT state,approval_id FROM compensation_operations WHERE id=:id"),
            {"id": compensation.compensation_id},
        ).one()
        assert operation.state == "OUTCOME_UNKNOWN"
        assert (
            connection.execute(
                text("SELECT state FROM runs WHERE id=:id"), {"id": run}
            ).scalar_one()
            == "RECONCILIATION_REQUIRED"
        )
        assert (
            connection.execute(
                text("SELECT state FROM approvals WHERE id=:id"), {"id": operation.approval_id}
            ).scalar_one()
            == "CONSUMED"
        )
        assert connection.execute(
            text("SELECT document FROM effect_receipts WHERE tool_call_id=:id"),
            {"id": effect.tool_call_id},
        ).scalar_one()["receipt_id"] == str(original_receipt.receipt_id)

    current_owner, current_token = "worker", 1
    with engine.begin() as connection:
        if authority_loss == "expiry":
            connection.execute(
                text(
                    "UPDATE leases SET expires_at=:future WHERE resource_id="
                    "(SELECT task_id FROM runs WHERE id=:run)"
                ),
                {"future": clock.now() + timedelta(hours=1), "run": run},
            )
        elif authority_loss == "owner":
            current_owner = "successor"
        else:
            current_token = 2
    adapter.fault_hook = None
    result = compensator.reconcile(
        compensation_id=compensation.compensation_id,
        owner_id=current_owner,
        fencing_token=current_token,
        idempotency_key=f"observe-{phase}-{authority_loss}",
        request_id=uuid4(),
    )
    assert result["outcome"] == "COMPENSATION_CONFIRMED"
    assert dispatches == 1
    replay = compensator.reconcile(
        compensation_id=compensation.compensation_id,
        owner_id=current_owner,
        fencing_token=current_token,
        idempotency_key=f"observe-{phase}-{authority_loss}",
        request_id=uuid4(),
    )
    assert replay == result


@pytest.mark.integration
@pytest.mark.parametrize("failure_point", ["receipt", "audit", "outbox"])
def test_post_effect_persistence_failure_rolls_back_to_durable_unknown(
    engine, wp3, monkeypatch, failure_point
) -> None:
    clock, root, adapter, service = wp3
    goal, run, action, token = _seed(engine)
    approval_id = uuid4()
    request = _request(run, action, approval_id, b"db-failure")
    _approve(engine, clock, request, approval_id)
    listener = None
    if failure_point == "receipt":

        def fail_receipt(_conn, _cursor, statement, _parameters, _context, _many):
            if "INSERT INTO effect_receipts" in statement:
                raise RuntimeError("injected receipt persistence failure")

        listener = fail_receipt
        event.listen(engine, "before_cursor_execute", listener)
    elif failure_point == "audit":
        original_audit = IntegrityService._audit

        def fail_result_audit(*args, **kwargs):
            if kwargs.get("command_type") == "internal.fixture.atomic-write.result":
                raise RuntimeError("injected audit persistence failure")
            return original_audit(*args, **kwargs)

        monkeypatch.setattr(IntegrityService, "_audit", fail_result_audit)
    else:
        original_outbox = IntegrityService._outbox

        def fail_result_outbox(*args, **kwargs):
            if len(args) > 1 and args[1] == "internal.tool-effect.recorded":
                raise RuntimeError("injected outbox persistence failure")
            return original_outbox(*args, **kwargs)

        monkeypatch.setattr(IntegrityService, "_outbox", fail_result_outbox)
    try:
        with pytest.raises(RuntimeError):
            service.execute(
                request,
                b"db-failure",
                owner_id="worker",
                fencing_token=token,
                authorized_goal_id=goal,
                request_id=uuid4(),
            )
    finally:
        if listener is not None:
            event.remove(engine, "before_cursor_execute", listener)
    assert (root / "result/output.txt").read_bytes() == b"db-failure"
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT state FROM tool_calls WHERE id=:id"), {"id": request.tool_call_id}
            ).scalar_one()
            == "OUTCOME_UNKNOWN"
        )
        assert (
            connection.execute(
                text("SELECT state FROM runs WHERE id=:id"), {"id": run}
            ).scalar_one()
            == "RECONCILIATION_REQUIRED"
        )
        assert (
            connection.execute(
                text("SELECT state FROM approvals WHERE id=:id"), {"id": approval_id}
            ).scalar_one()
            == "CONSUMED"
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM effect_receipts WHERE tool_call_id=:id"),
                {"id": request.tool_call_id},
            ).scalar_one()
            == 0
        )
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM outbox WHERE aggregate_id=:id "
                    "AND message_type='internal.tool-effect.recorded'"
                ),
                {"id": request.tool_call_id},
            ).scalar_one()
            == 0
        )
    result = FixtureReconciliationService(uow_factory(engine), clock, adapter).reconcile(
        tool_call_id=request.tool_call_id,
        owner_id="worker",
        fencing_token=1,
        idempotency_key=f"after-{failure_point}-failure",
        request_id=uuid4(),
    )
    assert result.outcome == ReconciliationOutcome.CONFIRMED_SUCCEEDED


@pytest.mark.integration
def test_reconciler_cannot_conclude_between_tool_barrier_and_dispatch(engine, wp3) -> None:
    clock, root, adapter, service = wp3
    goal, run, action, token = _seed(engine)
    approval_id = uuid4()
    request = _request(run, action, approval_id, b"serialized-dispatch")
    _approve(engine, clock, request, approval_id)
    barrier_committed = Event()
    allow_dispatch = Event()
    replaces = 0

    def pause_after_barrier() -> None:
        barrier_committed.set()
        assert allow_dispatch.wait(timeout=5)

    def count_replace(phase: str) -> None:
        nonlocal replaces
        if phase == "after_replace":
            replaces += 1

    adapter.fault_hook = count_replace
    reconciler = FixtureReconciliationService(uow_factory(engine), clock, adapter)
    with ThreadPoolExecutor(max_workers=2) as executor:
        effect_future = executor.submit(
            service.execute,
            request,
            b"serialized-dispatch",
            owner_id="worker",
            fencing_token=token,
            authorized_goal_id=goal,
            request_id=uuid4(),
            after_barrier=pause_after_barrier,
        )
        assert barrier_committed.wait(timeout=5)
        reconciliation_future = executor.submit(
            reconciler.reconcile,
            tool_call_id=request.tool_call_id,
            owner_id="worker",
            fencing_token=token,
            idempotency_key="concurrent-before-dispatch",
            request_id=uuid4(),
        )
        with pytest.raises(TimeoutError):
            reconciliation_future.result(timeout=0.25)
        assert not (root / "result/output.txt").exists()
        allow_dispatch.set()
        receipt = effect_future.result(timeout=5)
        with pytest.raises(DomainError) as rejected:
            reconciliation_future.result(timeout=5)
    assert rejected.value.code == ErrorCode.PRECONDITION_FAILED
    assert receipt.outcome == ToolCallOutcome.SUCCEEDED
    assert replaces == 1
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT state FROM tool_calls WHERE id=:id"), {"id": request.tool_call_id}
            ).scalar_one()
            == "SUCCEEDED"
        )
        assert (
            connection.execute(
                text("SELECT state FROM runs WHERE id=:id"), {"id": run}
            ).scalar_one()
            == "RUNNING"
        )
        assert (
            connection.execute(
                text("SELECT uses_consumed FROM approvals WHERE id=:id"), {"id": approval_id}
            ).scalar_one()
            == 1
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM effect_receipts WHERE tool_call_id=:id"),
                {"id": request.tool_call_id},
            ).scalar_one()
            == 1
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM reconciliations WHERE tool_call_id=:id"),
                {"id": request.tool_call_id},
            ).scalar_one()
            == 0
        )


@pytest.mark.integration
def test_reconciler_cannot_conclude_between_compensation_barrier_and_restore(engine, wp3) -> None:
    clock, root, adapter, service = wp3
    target = root / "result/output.txt"
    target.write_bytes(b"before")
    goal, run, action, token = _seed(engine)
    approval_id = uuid4()
    effect = _request(run, action, approval_id, b"after")
    _approve(engine, clock, effect, approval_id)
    original_receipt = service.execute(
        effect,
        b"after",
        owner_id="worker",
        fencing_token=token,
        authorized_goal_id=goal,
        request_id=uuid4(),
    )
    compensation = CompensationRequest(
        contract="jarvis.compensation-request",
        schema_version="1.0.0",
        compensation_id=uuid4(),
        original_tool_call_id=effect.tool_call_id,
        operation_id=uuid4(),
        operation_hash="sha256:" + "0" * 64,
        actor_id="worker",
        idempotency_key="serialized-compensation",
        reason="Restore exact prior fixture",
        expected_result_hash=FixtureAtomicWrite.digest(b"before"),
    )
    compensation = compensation.model_copy(
        update={"operation_hash": compensation_hash(compensation)}
    )
    _approve_compensation(engine, clock, compensation, uuid4())
    barrier_committed = Event()
    allow_restore = Event()
    restores = 0

    def pause_after_barrier() -> None:
        barrier_committed.set()
        assert allow_restore.wait(timeout=5)

    def count_restore(phase: str) -> None:
        nonlocal restores
        if phase == "after_replace":
            restores += 1

    adapter.fault_hook = count_restore
    compensator = FixtureCompensationService(uow_factory(engine), clock, adapter)
    with ThreadPoolExecutor(max_workers=2) as executor:
        compensation_future = executor.submit(
            compensator.compensate,
            compensation,
            owner_id="worker",
            fencing_token=token,
            request_id=uuid4(),
            after_barrier=pause_after_barrier,
        )
        assert barrier_committed.wait(timeout=5)
        reconciliation_future = executor.submit(
            compensator.reconcile,
            compensation_id=compensation.compensation_id,
            owner_id="worker",
            fencing_token=token,
            idempotency_key="concurrent-before-restore",
            request_id=uuid4(),
        )
        with pytest.raises(TimeoutError):
            reconciliation_future.result(timeout=0.25)
        assert target.read_bytes() == b"after"
        allow_restore.set()
        compensation_receipt = compensation_future.result(timeout=5)
        with pytest.raises(DomainError) as rejected:
            reconciliation_future.result(timeout=5)
    assert rejected.value.code == ErrorCode.PRECONDITION_FAILED
    assert compensation_receipt["outcome"] == "SUCCEEDED"
    assert restores == 1
    assert target.read_bytes() == b"before"
    with engine.connect() as connection:
        operation = connection.execute(
            text("SELECT state,approval_id FROM compensation_operations WHERE id=:id"),
            {"id": compensation.compensation_id},
        ).one()
        assert operation.state == "SUCCEEDED"
        assert (
            connection.execute(
                text("SELECT state FROM runs WHERE id=:id"), {"id": run}
            ).scalar_one()
            == "RUNNING"
        )
        assert (
            connection.execute(
                text("SELECT uses_consumed FROM approvals WHERE id=:id"),
                {"id": operation.approval_id},
            ).scalar_one()
            == 1
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM compensation_reconciliations WHERE compensation_id=:id"),
                {"id": compensation.compensation_id},
            ).scalar_one()
            == 0
        )
        assert connection.execute(
            text("SELECT document FROM effect_receipts WHERE tool_call_id=:id"),
            {"id": effect.tool_call_id},
        ).scalar_one()["receipt_id"] == str(original_receipt.receipt_id)


@pytest.mark.integration
def test_tool_conclusion_cas_failure_rolls_back_receipt_audit_and_outbox(engine, wp3) -> None:
    clock, root, _, service = wp3
    goal, run, action, token = _seed(engine)
    approval_id = uuid4()
    request = _request(run, action, approval_id, b"cas-loser")
    _approve(engine, clock, request, approval_id)

    def inject_winning_conclusion() -> None:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE tool_calls SET state='FAILED' WHERE id=:id AND state='OUTCOME_UNKNOWN'"
                ),
                {"id": request.tool_call_id},
            )
            connection.execute(
                text(
                    "UPDATE runs SET state='FAILED' WHERE id=:id "
                    "AND state='RECONCILIATION_REQUIRED'"
                ),
                {"id": run},
            )

    with pytest.raises(DomainError) as lost:
        service.execute(
            request,
            b"cas-loser",
            owner_id="worker",
            fencing_token=token,
            authorized_goal_id=goal,
            request_id=uuid4(),
            after_barrier=inject_winning_conclusion,
        )
    assert lost.value.code == ErrorCode.PRECONDITION_FAILED
    assert (root / "result/output.txt").read_bytes() == b"cas-loser"
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT state FROM tool_calls WHERE id=:id"), {"id": request.tool_call_id}
            ).scalar_one()
            == "FAILED"
        )
        assert (
            connection.execute(
                text("SELECT state FROM runs WHERE id=:id"), {"id": run}
            ).scalar_one()
            == "FAILED"
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM effect_receipts WHERE tool_call_id=:id"),
                {"id": request.tool_call_id},
            ).scalar_one()
            == 0
        )
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM outbox WHERE aggregate_id=:id "
                    "AND message_type='internal.tool-effect.recorded'"
                ),
                {"id": request.tool_call_id},
            ).scalar_one()
            == 0
        )
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM audit_records WHERE aggregate_id=:id "
                    "AND command_type='internal.fixture.atomic-write.result'"
                ),
                {"id": request.tool_call_id},
            ).scalar_one()
            == 0
        )


@pytest.mark.integration
def test_compensation_conclusion_cas_failure_preserves_winner_without_publication(
    engine, wp3
) -> None:
    clock, root, adapter, service = wp3
    target = root / "result/output.txt"
    target.write_bytes(b"before")
    goal, run, action, token = _seed(engine)
    approval_id = uuid4()
    effect = _request(run, action, approval_id, b"after")
    _approve(engine, clock, effect, approval_id)
    service.execute(
        effect,
        b"after",
        owner_id="worker",
        fencing_token=token,
        authorized_goal_id=goal,
        request_id=uuid4(),
    )
    compensation = CompensationRequest(
        contract="jarvis.compensation-request",
        schema_version="1.0.0",
        compensation_id=uuid4(),
        original_tool_call_id=effect.tool_call_id,
        operation_id=uuid4(),
        operation_hash="sha256:" + "0" * 64,
        actor_id="worker",
        idempotency_key="compensation-cas-loser",
        reason="Restore exact prior fixture",
        expected_result_hash=FixtureAtomicWrite.digest(b"before"),
    )
    compensation = compensation.model_copy(
        update={"operation_hash": compensation_hash(compensation)}
    )
    _approve_compensation(engine, clock, compensation, uuid4())

    def inject_winning_conclusion() -> None:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE compensation_operations SET state='FAILED' WHERE id=:id "
                    "AND state='OUTCOME_UNKNOWN'"
                ),
                {"id": compensation.compensation_id},
            )
            connection.execute(
                text(
                    "UPDATE runs SET state='RUNNING' WHERE id=:id "
                    "AND state='RECONCILIATION_REQUIRED'"
                ),
                {"id": run},
            )

    compensator = FixtureCompensationService(uow_factory(engine), clock, adapter)
    with pytest.raises(DomainError) as lost:
        compensator.compensate(
            compensation,
            owner_id="worker",
            fencing_token=token,
            request_id=uuid4(),
            after_barrier=inject_winning_conclusion,
        )
    assert lost.value.code == ErrorCode.PRECONDITION_FAILED
    assert target.read_bytes() == b"before"
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT state FROM compensation_operations WHERE id=:id"),
                {"id": compensation.compensation_id},
            ).scalar_one()
            == "FAILED"
        )
        assert (
            connection.execute(
                text("SELECT state FROM runs WHERE id=:id"), {"id": run}
            ).scalar_one()
            == "RUNNING"
        )
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM outbox WHERE aggregate_id=:id "
                    "AND message_type='internal.fixture.compensated'"
                ),
                {"id": effect.tool_call_id},
            ).scalar_one()
            == 0
        )
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM audit_records WHERE aggregate_id=:id "
                    "AND command_type='internal.fixture.atomic-write.compensate'"
                ),
                {"id": compensation.compensation_id},
            ).scalar_one()
            == 0
        )
