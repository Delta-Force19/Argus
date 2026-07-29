from collections.abc import Callable, Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from argus.config import DATABASE_PATH


DATABASE_PATH.parent.mkdir(
    parents=True,
    exist_ok=True,
)

engine = create_engine(
    f"sqlite:///{DATABASE_PATH}",
    echo=False,
)

SessionLocal: sessionmaker[Session] = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
)


class DatabaseSessionManager:
    """Manage the lifecycle of SQLAlchemy sessions used by Argus services."""

    def __init__(
            self,
            session_factory: Callable[[], Session],
    ) -> None:
        self._session_factory = session_factory

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Provide a session and guarantee rollback and cleanup on failure."""

        session = self._session_factory()

        try:
            yield session
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


session_manager = DatabaseSessionManager(SessionLocal)


class Base(DeclarativeBase):
    pass