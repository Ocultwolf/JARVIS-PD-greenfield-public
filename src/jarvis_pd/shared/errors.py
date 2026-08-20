from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    SCHEMA_INVALID = "SCHEMA_INVALID"
    VERSION_UNSUPPORTED = "VERSION_UNSUPPORTED"
    UNAUTHORIZED = "UNAUTHORIZED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVAL_MISMATCH = "APPROVAL_MISMATCH"
    PRECONDITION_FAILED = "PRECONDITION_FAILED"
    REVISION_CONFLICT = "REVISION_CONFLICT"
    LEASE_REQUIRED = "LEASE_REQUIRED"
    FENCING_REJECTED = "FENCING_REJECTED"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


@dataclass(frozen=True, slots=True)
class DomainError(Exception):
    code: ErrorCode
    message: str
    details: dict[str, Any] | None = None

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"
