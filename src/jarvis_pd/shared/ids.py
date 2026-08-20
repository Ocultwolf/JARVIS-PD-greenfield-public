from __future__ import annotations

from typing import NewType
from uuid import UUID, uuid4

GoalId = NewType("GoalId", UUID)
TaskId = NewType("TaskId", UUID)
RunId = NewType("RunId", UUID)
ActionId = NewType("ActionId", UUID)
EvidenceId = NewType("EvidenceId", UUID)
ApprovalId = NewType("ApprovalId", UUID)
MessageId = NewType("MessageId", UUID)
ActorId = NewType("ActorId", str)


def new_uuid() -> UUID:
    return uuid4()
