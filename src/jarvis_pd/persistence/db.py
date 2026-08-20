from sqlalchemy import Engine, create_engine


def build_engine(dsn: str) -> Engine:
    return create_engine(dsn, pool_pre_ping=True)
