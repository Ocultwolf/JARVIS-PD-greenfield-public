from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class JarvisMessageEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    message_id: UUID
    message_type: str = Field(min_length=1, max_length=160)
    schema_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    occurred_at: datetime
    actor_id: str = Field(min_length=1, max_length=200)
    correlation_id: UUID
    causation_id: UUID | None = None
    sensitivity: Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"]
    payload: dict[str, Any]
