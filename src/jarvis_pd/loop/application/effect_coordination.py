from types import TracebackType
from uuid import UUID

from sqlalchemy import Connection, text


class Wp3EffectLock:
    """Session exclusion between one fixture effect and its reconciler.

    Acquisition is committed before returning, so no transaction remains open
    across filesystem I/O. PostgreSQL releases the session lock if the process
    or connection dies.
    """

    def __init__(self, uow_factory, operation_kind: str, operation_id: UUID) -> None:
        self.engine = uow_factory().engine
        self.lock_key = f"wp3:{operation_kind}:{operation_id}"
        self.connection: Connection | None = None

    def __enter__(self) -> "Wp3EffectLock":
        self.connection = self.engine.connect()
        try:
            self.connection.execute(
                text("SELECT pg_advisory_lock(hashtextextended(:key,0))"),
                {"key": self.lock_key},
            )
            self.connection.commit()
        except BaseException:
            self.connection.close()
            raise
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        assert self.connection is not None
        try:
            self.connection.execute(
                text("SELECT pg_advisory_unlock(hashtextextended(:key,0))"),
                {"key": self.lock_key},
            )
            self.connection.commit()
        finally:
            self.connection.close()


def wp3_effect_lock(uow_factory, operation_kind: str, operation_id: UUID) -> Wp3EffectLock:
    return Wp3EffectLock(uow_factory, operation_kind, operation_id)
