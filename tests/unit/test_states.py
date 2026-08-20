import pytest

from jarvis_pd.loop.domain.states import (
    RUN_TRANSITIONS,
    TASK_TRANSITIONS,
    RunState,
    TaskState,
    require_transition,
)
from jarvis_pd.shared.errors import DomainError


def test_task_main_path_is_explicit() -> None:
    require_transition(TaskState.PROPOSED, TaskState.READY, TASK_TRANSITIONS)
    require_transition(TaskState.READY, TaskState.RUNNING, TASK_TRANSITIONS)
    require_transition(TaskState.RUNNING, TaskState.VALIDATING, TASK_TRANSITIONS)
    require_transition(TaskState.VALIDATING, TaskState.COMPLETED, TASK_TRANSITIONS)


def test_run_cannot_skip_from_created_to_succeeded() -> None:
    with pytest.raises(DomainError):
        require_transition(RunState.CREATED, RunState.SUCCEEDED, RUN_TRANSITIONS)
