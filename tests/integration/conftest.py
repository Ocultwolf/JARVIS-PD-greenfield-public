import os
from pathlib import Path

import pytest
from sqlalchemy import text

from jarvis_pd.persistence.db import build_engine

DSN = os.environ.get(
    "JARVIS_TEST_DATABASE_DSN",
    "postgresql+psycopg://jarvis:jarvis-test-only@127.0.0.1:55432/jarvis",
)


def pytest_collection_modifyitems(items):
    integration_root = Path(__file__).parent.resolve()
    for item in items:
        if integration_root in Path(str(item.path)).resolve().parents:
            item.add_marker(pytest.mark.integration)


@pytest.fixture(scope="session")
def engine():
    value = build_engine(DSN)
    with value.connect() as connection:
        assert connection.execute(text("SELECT 1")).scalar_one() == 1
    yield value
    value.dispose()


@pytest.fixture(autouse=True)
def clean_database(engine):
    with engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE worker_interruptions,worker_heartbeats,gate1_local_latch,"
                "audit_records,idempotency_records,inbox,outbox,approvals,"
                "evidence,actions,runs,leases,tasks,goals CASCADE"
            )
        )
