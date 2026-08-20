from enum import StrEnum

from jarvis_pd.shared.errors import DomainError, ErrorCode


class TaskState(StrEnum):
    PROPOSED = "PROPOSED"
    READY = "READY"
    BLOCKED = "BLOCKED"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    RUNNING = "RUNNING"
    VALIDATING = "VALIDATING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    SUPERSEDED = "SUPERSEDED"


class RunState(StrEnum):
    CREATED = "CREATED"
    READY = "READY"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    BLOCKED = "BLOCKED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TASK_TRANSITIONS = {
    TaskState.PROPOSED: {TaskState.READY, TaskState.BLOCKED, TaskState.CANCELLED},
    TaskState.READY: {
        TaskState.RUNNING,
        TaskState.BLOCKED,
        TaskState.WAITING_APPROVAL,
        TaskState.CANCELLED,
    },
    TaskState.BLOCKED: {TaskState.READY, TaskState.CANCELLED, TaskState.SUPERSEDED},
    TaskState.WAITING_APPROVAL: {TaskState.READY, TaskState.CANCELLED},
    TaskState.RUNNING: {
        TaskState.VALIDATING,
        TaskState.BLOCKED,
        TaskState.FAILED,
        TaskState.CANCELLED,
    },
    TaskState.VALIDATING: {TaskState.COMPLETED, TaskState.FAILED, TaskState.BLOCKED},
}

RUN_TRANSITIONS = {
    RunState.CREATED: {RunState.READY, RunState.CANCELLED},
    RunState.READY: {RunState.RUNNING, RunState.CANCELLED},
    RunState.RUNNING: {
        RunState.PAUSED,
        RunState.BLOCKED,
        RunState.RECONCILIATION_REQUIRED,
        RunState.SUCCEEDED,
        RunState.FAILED,
        RunState.CANCELLED,
    },
    RunState.PAUSED: {RunState.RUNNING, RunState.CANCELLED},
    RunState.BLOCKED: {RunState.RUNNING, RunState.CANCELLED},
    RunState.RECONCILIATION_REQUIRED: {RunState.SUCCEEDED, RunState.FAILED},
}


def require_transition(current: StrEnum, target: StrEnum, transitions: dict) -> None:
    if target not in transitions.get(current, set()):
        raise DomainError(
            ErrorCode.PRECONDITION_FAILED,
            f"Transition {current} -> {target} is not allowed",
        )
