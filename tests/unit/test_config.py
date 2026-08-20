import pytest
from pydantic import ValidationError

from jarvis_pd.config import Settings


def test_settings_reject_plain_secret_and_parallelism() -> None:
    with pytest.raises(ValidationError):
        Settings(
            environment="dev",
            database_dsn_ref="postgresql://secret",
            release_id="test",
            instance_id="one",
            max_parallel_runs=2,
        )


def test_settings_accept_secret_reference() -> None:
    settings = Settings(
        environment="test",
        database_dsn_ref="env://JARVIS_DATABASE_DSN",
        release_id="test",
        instance_id="one",
    )
    assert settings.max_parallel_runs == 1
