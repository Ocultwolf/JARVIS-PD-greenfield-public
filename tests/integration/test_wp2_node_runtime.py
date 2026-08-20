import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from jarvis_pd.contracts.gate1 import AgentInvocation, AgentResultStatus
from jarvis_pd.loop.application.fake_agent_runtime import FakeAgentRuntime, FakeRuntimeResponse
from jarvis_pd.loop.application.integrity_service import uow_factory
from jarvis_pd.loop.application.node_runtime_service import NodeRuntimeService
from jarvis_pd.shared.errors import DomainError, ErrorCode

NOW = datetime(2026, 8, 7, 20, 0, tzinfo=UTC)
HASH = "sha256:" + "a" * 64


class FixedClock:
    def now(self) -> datetime:
        return NOW


def _payload(run_id: UUID) -> dict:
    return {
        "contract": "jarvis.user-node-output",
        "schema_version": "1.0.0",
        "proposal_id": str(uuid4()),
        "run_id": str(run_id),
        "kind": "NO_ACTION",
        "selected_task_id": None,
        "proposed_tasks": [],
        "rejection_reason": "NO_ELIGIBLE_TASK",
        "explanation": "No eligible synthetic fixture work",
    }


def _seed_vertical(engine, *, receipt_status: str = "CONFIRMED") -> tuple[AgentInvocation, int]:
    goal_id, task_id, run_id = uuid4(), uuid4(), uuid4()
    invocation_id, node_run_id = uuid4(), uuid4()
    manifest_id, receipt_id = uuid4(), uuid4()
    token = 7
    invocation = AgentInvocation(
        contract="jarvis.agent-invocation",
        schema_version="1.0.0",
        invocation_id=invocation_id,
        agent_id="user-node",
        node_run_id=node_run_id,
        run_id=run_id,
        message="Synthetic fixture only",
        context_injection_id=receipt_id,
        expected_output_contract="jarvis.user-node-output@1.0.0",
        timeout_seconds=60,
        session_policy="FRESH_OR_VERIFIED",
        idempotency_key=f"node:{invocation_id}",
    )
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO goals(id,title,state) VALUES (:id,'G','ACTIVE')"),
            {"id": goal_id},
        )
        connection.execute(
            text(
                "INSERT INTO tasks(id,goal_id,title,acceptance_criteria,state) "
                "VALUES (:id,:goal,'T',CAST(:criteria AS jsonb),'RUNNING')"
            ),
            {"id": task_id, "goal": goal_id, "criteria": json.dumps(["done"])},
        )
        connection.execute(
            text(
                "INSERT INTO leases(resource_type,resource_id,owner_id,fencing_token,state,"
                "expires_at) VALUES ('Task',:task,'worker',:token,'ACTIVE',:expires)"
            ),
            {"task": task_id, "token": token, "expires": NOW + timedelta(minutes=5)},
        )
        connection.execute(
            text(
                "INSERT INTO runs(id,task_id,state,fencing_token) "
                "VALUES (:id,:task,'RUNNING',:token)"
            ),
            {"id": run_id, "task": task_id, "token": token},
        )
        connection.execute(
            text(
                "INSERT INTO node_invocations(id,node_run_id,run_id,node_id,capability,"
                "expected_output_contract,context_manifest_id,idempotency_key,request,status) "
                "VALUES (:id,:node_run,:run,'user-node','user.continue',:contract,:manifest,"
                ":key,'{}','CREATED')"
            ),
            {
                "id": invocation_id,
                "node_run": node_run_id,
                "run": run_id,
                "contract": invocation.expected_output_contract,
                "manifest": manifest_id,
                "key": invocation.idempotency_key,
            },
        )
        connection.execute(
            text(
                "INSERT INTO context_manifests(id,invocation_id,session_id,transport,consumer_id,"
                "purpose,status,content_hash,document,sealed_at) VALUES "
                "(:id,:invocation,'session-1','fake','user-node','synthetic-test','SEALED',"
                ":hash,'{}',:sealed)"
            ),
            {"id": manifest_id, "invocation": invocation_id, "hash": HASH, "sealed": NOW},
        )
        connection.execute(
            text(
                "INSERT INTO injection_receipts(id,invocation_id,context_manifest_id,"
                "manifest_invocation_id,manifest_context_manifest_id,manifest_session_id,"
                "manifest_transport,status,transport,session_id,manifest_hash,injected_hash,"
                "verification_method,document) VALUES (:id,:invocation,:manifest,:invocation,"
                ":manifest,'session-1','fake',:status,'fake','session-1',:hash,:injected,"
                ":method,'{}')"
            ),
            {
                "id": receipt_id,
                "invocation": invocation_id,
                "manifest": manifest_id,
                "status": receipt_status,
                "hash": HASH,
                "injected": HASH if receipt_status == "CONFIRMED" else None,
                "method": "fake-exact" if receipt_status == "CONFIRMED" else None,
            },
        )
    return invocation, token


def _service(engine, responder) -> NodeRuntimeService:
    return NodeRuntimeService(
        uow_factory(engine),
        FakeAgentRuntime(responder, lambda: NOW),
        FixedClock(),
    )


def _invoke(service, invocation, token, **overrides):
    arguments = {
        "owner_id": "worker",
        "fencing_token": token,
        "request_id": uuid4(),
    }
    arguments.update(overrides)
    return service.invoke(invocation, **arguments)


@pytest.mark.integration
def test_vertical_manifest_confirmed_receipt_agent_invocation_runtime(engine) -> None:
    invocation, token = _seed_vertical(engine)
    calls = []

    def responder(value, attempt):
        calls.append(attempt)
        return FakeRuntimeResponse(payload=_payload(invocation.run_id))

    service = _service(engine, responder)
    first = _invoke(service, invocation, token)
    second = _invoke(service, invocation, token)
    assert first == second
    assert first.status == AgentResultStatus.SUCCEEDED
    assert calls == [1]
    with engine.connect() as connection:
        row = (
            connection.execute(
                text("SELECT status,context_manifest_id,request,result FROM node_invocations")
            )
            .mappings()
            .one()
        )
        receipt_manifest = connection.execute(
            text("SELECT context_manifest_id FROM injection_receipts")
        ).scalar_one()
    assert row["status"] == "SUCCEEDED"
    assert row["context_manifest_id"] == receipt_manifest
    assert row["request"]["context_injection_id"] == str(invocation.context_injection_id)
    assert row["result"]["invocation_id"] == str(invocation.invocation_id)


@pytest.mark.integration
def test_non_confirmed_or_wrong_receipt_cannot_dispatch(engine) -> None:
    invocation, token = _seed_vertical(engine, receipt_status="FAILED")
    service = _service(engine, lambda value, attempt: pytest.fail("runtime must not be called"))
    with pytest.raises(DomainError) as not_confirmed:
        _invoke(service, invocation, token)
    assert not_confirmed.value.code == ErrorCode.PRECONDITION_FAILED

    other, _ = _seed_vertical(engine)
    wrong_receipt = invocation.model_copy(
        update={"context_injection_id": other.context_injection_id}
    )
    with pytest.raises(DomainError) as wrong_correlation:
        _invoke(service, wrong_receipt, token)
    assert wrong_correlation.value.code == ErrorCode.PRECONDITION_FAILED


@pytest.mark.integration
@pytest.mark.parametrize(
    ("override", "code"),
    [
        ({"owner_id": "stale-worker"}, ErrorCode.FENCING_REJECTED),
        ({"fencing_token": 6}, ErrorCode.FENCING_REJECTED),
    ],
)
def test_stale_owner_or_fencing_cannot_dispatch(engine, override, code) -> None:
    invocation, token = _seed_vertical(engine)
    service = _service(engine, lambda value, attempt: pytest.fail("runtime must not be called"))
    with pytest.raises(DomainError) as caught:
        _invoke(service, invocation, token, **override)
    assert caught.value.code == code


@pytest.mark.integration
def test_expired_lease_cannot_dispatch(engine) -> None:
    invocation, token = _seed_vertical(engine)
    with engine.begin() as connection:
        connection.execute(text("UPDATE leases SET expires_at=:past"), {"past": NOW})
    service = _service(engine, lambda value, attempt: pytest.fail("runtime must not be called"))
    with pytest.raises(DomainError) as caught:
        _invoke(service, invocation, token)
    assert caught.value.code == ErrorCode.LEASE_REQUIRED


@pytest.mark.integration
def test_runtime_failure_blocks_with_state_audit_outbox_in_one_transaction(engine) -> None:
    invocation, token = _seed_vertical(engine)
    service = _service(
        engine,
        lambda value, attempt: FakeRuntimeResponse(unavailable_code="RUNTIME_UNAVAILABLE"),
    )
    result = _invoke(service, invocation, token)
    assert result.status == AgentResultStatus.UNAVAILABLE
    assert result.structured_output is None
    replay = _invoke(service, invocation, token)
    assert replay == result
    with engine.connect() as connection:
        run_state = connection.execute(text("SELECT state FROM runs")).scalar_one()
        audits = (
            connection.execute(text("SELECT command_type FROM audit_records ORDER BY sequence"))
            .scalars()
            .all()
        )
        outbox = (
            connection.execute(text("SELECT message_type,payload FROM outbox")).mappings().one()
        )
    assert run_state == "BLOCKED"
    assert audits == ["internal.node-invocation.complete", "internal.node-runtime.block"]
    assert outbox["message_type"] == "internal.node-runtime.blocked"
    assert outbox["payload"]["invocation_id"] == str(invocation.invocation_id)


@pytest.mark.integration
def test_second_invalid_contract_output_blocks_run_after_one_repair(engine) -> None:
    invocation, token = _seed_vertical(engine)
    attempts = []

    def responder(value, attempt):
        attempts.append(attempt)
        return FakeRuntimeResponse(payload={})

    result = _invoke(_service(engine, responder), invocation, token)
    assert result.status == AgentResultStatus.INVALID_OUTPUT
    assert result.error_code == "CONTRACT_REPAIR_EXHAUSTED"
    assert attempts == [1, 2]
    with engine.connect() as connection:
        assert connection.execute(text("SELECT state FROM runs")).scalar_one() == "BLOCKED"
        assert connection.execute(text("SELECT count(*) FROM outbox")).scalar_one() == 1


@pytest.mark.integration
def test_lease_expiring_during_runtime_prevents_canonical_completion(engine) -> None:
    invocation, token = _seed_vertical(engine)

    def responder(value, attempt):
        with engine.begin() as connection:
            connection.execute(text("UPDATE leases SET expires_at=:past"), {"past": NOW})
        return FakeRuntimeResponse(unavailable_code="RUNTIME_UNAVAILABLE")

    with pytest.raises(DomainError) as caught:
        _invoke(_service(engine, responder), invocation, token)
    assert caught.value.code == ErrorCode.LEASE_REQUIRED
    with engine.connect() as connection:
        assert connection.execute(text("SELECT state FROM runs")).scalar_one() == "RUNNING"
        assert connection.execute(text("SELECT count(*) FROM audit_records")).scalar_one() == 0
        assert connection.execute(text("SELECT count(*) FROM outbox")).scalar_one() == 0


@pytest.mark.integration
@pytest.mark.parametrize(
    "change",
    [
        {"invocation_id": uuid4()},
        {"run_id": uuid4()},
        {"context_injection_id": uuid4()},
        {"message": "A different semantic request"},
    ],
)
def test_same_idempotency_key_with_different_request_conflicts(engine, change) -> None:
    invocation, token = _seed_vertical(engine)
    service = _service(
        engine, lambda value, attempt: FakeRuntimeResponse(payload=_payload(invocation.run_id))
    )
    _invoke(service, invocation, token)
    changed = invocation.model_copy(update=change)
    with pytest.raises(DomainError) as caught:
        _invoke(service, changed, token)
    assert caught.value.code == ErrorCode.IDEMPOTENCY_CONFLICT
