from enum import StrEnum

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    DEV = "dev"
    TEST = "test"
    STAGING = "staging"
    PROD = "prod"
    RECOVERY = "recovery"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JARVIS_", extra="forbid")

    environment: Environment
    database_dsn_ref: str = Field(pattern=r"^(env|file|vault)://.+")
    release_id: str = Field(min_length=1, max_length=100)
    instance_id: str = Field(min_length=1, max_length=100)
    lease_seconds: int = Field(default=90, ge=5, le=3600)
    max_parallel_runs: int = Field(default=1, ge=1, le=32)

    @model_validator(mode="after")
    def validate_phase_zero_limits(self) -> "Settings":
        if self.max_parallel_runs != 1:
            raise ValueError("Phase 0 requires max_parallel_runs=1")
        return self
