from collections.abc import Iterable
from typing import Generic, TypeVar

from sqlalchemy.orm import DeclarativeBase, Session


ModelT = TypeVar(
    "ModelT",
    bound=DeclarativeBase,
)


class BaseRepository(Generic[ModelT]):
    """Provide shared persistence operations for model repositories.

    Transaction boundaries are intentionally left to the calling code.
    BaseRepository never commits or rolls back implicitly.
    """

    def __init__(
            self,
            session: Session,
            model_type: type[ModelT],
    ) -> None:
        self.session = session
        self.model_type = model_type

    def get_by_id(
            self,
            model_id: int,
    ) -> ModelT | None:
        return self.session.get(
            self.model_type,
            model_id,
        )

    def add(
            self,
            model: ModelT,
    ) -> ModelT:
        self.session.add(model)
        return model

    def add_all(
            self,
            models: Iterable[ModelT],
    ) -> None:
        self.session.add_all(
            list(models)
        )

    def delete(
            self,
            model: ModelT,
    ) -> None:
        self.session.delete(model)

    def flush(self) -> None:
        self.session.flush()

    def refresh(
            self,
            model: ModelT,
    ) -> None:
        self.session.refresh(model)