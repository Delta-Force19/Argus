import unittest
from unittest.mock import Mock

from sqlalchemy.orm import Session

from argus.database import DatabaseSessionManager


class DatabaseSessionManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session = Mock(spec=Session)
        self.session_factory = Mock(return_value=self.session)

        self.manager = DatabaseSessionManager(
            session_factory=self.session_factory,
        )

    def test_session_yields_created_session(self) -> None:
        with self.manager.session() as session:
            self.assertIs(session, self.session)

        self.session_factory.assert_called_once_with()

    def test_session_closes_after_success(self) -> None:
        with self.manager.session():
            pass

        self.session.close.assert_called_once_with()
        self.session.rollback.assert_not_called()

    def test_session_rolls_back_and_closes_after_error(self) -> None:
        with self.assertRaisesRegex(
                RuntimeError,
                "database failure",
        ):
            with self.manager.session():
                raise RuntimeError("database failure")

        self.session.rollback.assert_called_once_with()
        self.session.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()