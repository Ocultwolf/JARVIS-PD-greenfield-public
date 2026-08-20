from types import TracebackType

from sqlalchemy import Connection, Engine
from sqlalchemy.engine import RootTransaction


class UnitOfWork:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.connection: Connection | None = None
        self._transaction: RootTransaction | None = None

    def __enter__(self) -> "UnitOfWork":
        self.connection = self.engine.connect()
        self._transaction = self.connection.begin()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        assert self.connection is not None and self._transaction is not None
        if exc_type is None:
            self._transaction.commit()
        else:
            self._transaction.rollback()
        self.connection.close()

    @property
    def conn(self) -> Connection:
        if self.connection is None:
            raise RuntimeError("UnitOfWork is not active")
        return self.connection
