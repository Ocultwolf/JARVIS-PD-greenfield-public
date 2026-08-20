import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from jarvis_pd.loop.application.integrity_service import uow_factory
from jarvis_pd.loop.application.selection_service import SelectionService
from jarvis_pd.shared.errors import DomainError, ErrorCode


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 8, 7, 18, 0, tzinfo=UTC)


@pytest.fixture
def selection(engine):
    return SelectionService(uow_factory(engine), FixedClock())


def _goal_task(engine, *, priority=0, created_at=None, goal_state="ACTIVE") -> tuple[UUID, UUID]:
    goal_id, task_id = uuid4(), uuid4()
    created = created_at or datetime.now(UTC)
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO goals(id,title,state) VALUES (:id,'G',:state)"),
            {"id": goal_id, "state": goal_state},
        )
        connection.execute(
            text(
                "INSERT INTO tasks(id,goal_id,title,acceptance_criteria,state,priority,created_at) "
                "VALUES (:id,:goal,'T',CAST(:criteria AS jsonb),'READY',:priority,:created)"
            ),
            {
                "id": task_id,
                "goal": goal_id,
                "criteria": json.dumps(["done"]),
                "priority": priority,
                "created": created,
            },
        )
    return goal_id, task_id


@pytest.mark.integration
def test_selection_uses_exact_normative_order_and_materializes_atomically(
    engine, selection
) -> None:
    now = datetime.now(UTC)
    goal_id, low = _goal_task(engine, priority=1, created_at=now - timedelta(days=1))
    expected = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO tasks(id,goal_id,title,acceptance_criteria,state,priority,created_at) "
                "VALUES (:id,:goal,'T2',CAST(:criteria AS jsonb),'READY',9,:created)"
            ),
            {
                "id": expected,
                "goal": goal_id,
                "criteria": json.dumps(["done"]),
                "created": now,
            },
        )
    result = selection.select_and_start(
        run_id=uuid4(),
        owner_id="worker-1",
        actor_id="scheduler",
        request_id=uuid4(),
        idempotency_key="selection-1",
        authorized_goal_id=goal_id,
    )
    assert result["task_id"] == str(expected)
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT state FROM tasks WHERE id=:id"), {"id": expected}
            ).scalar_one()
            == "RUNNING"
        )
        assert (
            connection.execute(
                text("SELECT state FROM tasks WHERE id=:id"), {"id": low}
            ).scalar_one()
            == "READY"
        )
        assert connection.execute(text("SELECT count(*) FROM outbox")).scalar_one() == 2
        assert connection.execute(text("SELECT count(*) FROM audit_records")).scalar_one() == 2


@pytest.mark.integration
def test_selection_requires_active_goal_and_completed_dependencies(engine, selection) -> None:
    blocked_goal, blocked = _goal_task(engine, priority=10, goal_state="DRAFT")
    eligible_goal, eligible = _goal_task(engine, priority=1)
    _, dependency = _goal_task(engine, priority=0)
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE tasks SET state='BLOCKED' WHERE id=:id"), {"id": dependency}
        )
        connection.execute(
            text("INSERT INTO task_dependencies(task_id,depends_on_task_id) VALUES (:task,:dep)"),
            {"task": eligible, "dep": dependency},
        )
    with pytest.raises(DomainError) as caught:
        selection.select_and_start(
            run_id=uuid4(),
            owner_id="worker",
            actor_id="scheduler",
            request_id=uuid4(),
            idempotency_key="none",
            authorized_goal_id=eligible_goal,
        )
    assert caught.value.code == ErrorCode.PRECONDITION_FAILED
    assert blocked != eligible and blocked_goal != eligible_goal


@pytest.mark.integration
def test_selection_is_idempotent_and_latch_denies_before_work(engine, selection) -> None:
    goal_id, _ = _goal_task(engine)
    run_id = uuid4()
    kwargs = dict(
        run_id=run_id,
        owner_id="worker",
        actor_id="scheduler",
        request_id=uuid4(),
        idempotency_key="same",
        authorized_goal_id=goal_id,
    )
    first = selection.select_and_start(**kwargs)
    second = selection.select_and_start(**kwargs)
    assert first == second
    with pytest.raises(DomainError) as caught:
        selection.select_and_start(
            **{**kwargs, "run_id": uuid4(), "idempotency_key": "latched", "latch_active": True}
        )
    assert caught.value.code == ErrorCode.UNAUTHORIZED


@pytest.mark.integration
def test_global_a2_limit_is_serialized_across_competing_tasks(engine, selection) -> None:
    goal_id, _ = _goal_task(engine, priority=2)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO tasks(id,goal_id,title,acceptance_criteria,state,priority) "
                "VALUES (:id,:goal,'T2',CAST(:criteria AS jsonb),'READY',1)"
            ),
            {"id": uuid4(), "goal": goal_id, "criteria": json.dumps(["done"])},
        )

    def select(index):
        try:
            return selection.select_and_start(
                run_id=uuid4(),
                owner_id=f"worker-{index}",
                actor_id="scheduler",
                request_id=uuid4(),
                idempotency_key=f"race-{index}",
                authorized_goal_id=goal_id,
            )
        except DomainError as error:
            return error.message

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(select, (1, 2)))
    assert sum(isinstance(value, dict) for value in results) == 1
    assert results.count("MAX_CONCURRENT_RUNS") == 1
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM runs")).scalar_one() == 1


@pytest.mark.integration
def test_active_goal_outside_explicit_authorized_scope_is_not_eligible(engine, selection) -> None:
    authorized_goal, _ = _goal_task(engine, priority=1)
    outside_goal, outside_task = _goal_task(engine, priority=100)
    result = selection.select_and_start(
        run_id=uuid4(),
        owner_id="worker",
        actor_id="scheduler",
        request_id=uuid4(),
        idempotency_key="scoped-selection",
        authorized_goal_id=authorized_goal,
    )
    assert result["task_id"] != str(outside_task)
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT state FROM tasks WHERE id=:id"), {"id": outside_task}
            ).scalar_one()
            == "READY"
        )
    assert outside_goal != authorized_goal
