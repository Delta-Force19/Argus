import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from argus.database import Base
from argus.services.telegram_subscriber_service import (
    TelegramSubscriberService,
)


class TelegramSubscriberServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine)
        self.service = TelegramSubscriberService(
            session_factory=self.session_factory
        )

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_admin_bootstrap_is_idempotent_and_preserves_unsubscribe(
            self,
    ) -> None:
        created = self.service.bootstrap_admin(
            chat_id=77,
            initial_cursor=10,
        )
        self.service.unsubscribe(chat_id=77)
        existing = self.service.bootstrap_admin(
            chat_id=77,
            initial_cursor=99,
        )

        self.assertTrue(created.is_approved)
        self.assertTrue(created.is_subscribed)
        self.assertFalse(existing.is_subscribed)
        self.assertEqual(existing.last_delivered_article_id, 10)

    def test_request_approval_and_subscription_lifecycle(self) -> None:
        pending, created = self.service.request_access(chat_id=99)
        repeated, repeated_created = self.service.request_access(
            chat_id=99
        )
        approved = self.service.approve(
            chat_id=99,
            initial_cursor=20,
        )
        subscribed = self.service.subscribe(
            chat_id=99,
            initial_cursor=25,
        )
        self.service.advance_cursor(chat_id=99, article_id=27)
        repeated_subscription = self.service.subscribe(
            chat_id=99,
            initial_cursor=99,
        )
        unsubscribed = self.service.unsubscribe(chat_id=99)

        self.assertTrue(created)
        self.assertFalse(repeated_created)
        self.assertFalse(pending.is_approved)
        self.assertEqual(pending, repeated)
        self.assertIsNotNone(approved)
        self.assertFalse(approved.is_subscribed)
        self.assertTrue(subscribed.is_subscribed)
        self.assertEqual(
            repeated_subscription.last_delivered_article_id,
            27,
        )
        self.assertEqual(
            self.service.get(chat_id=99).last_delivered_article_id,
            27,
        )
        self.assertFalse(unsubscribed.is_subscribed)

    def test_public_registration_approves_new_and_pending_users(self) -> None:
        created, was_created = self.service.register(
            chat_id=88,
            initial_cursor=20,
        )
        self.service.request_access(chat_id=99)
        upgraded, was_upgraded_created = self.service.register(
            chat_id=99,
            initial_cursor=21,
        )
        repeated, repeated_created = self.service.register(
            chat_id=88,
            initial_cursor=99,
        )

        self.assertTrue(was_created)
        self.assertTrue(created.is_approved)
        self.assertEqual(created.last_delivered_article_id, 20)
        self.assertFalse(was_upgraded_created)
        self.assertTrue(upgraded.is_approved)
        self.assertEqual(upgraded.last_delivered_article_id, 21)
        self.assertFalse(repeated_created)
        self.assertEqual(repeated.last_delivered_article_id, 20)

    def test_only_approved_subscribed_users_are_returned(self) -> None:
        self.service.bootstrap_admin(chat_id=77, initial_cursor=10)
        self.service.request_access(chat_id=88)
        self.service.request_access(chat_id=99)
        self.service.approve(chat_id=99, initial_cursor=10)
        self.service.subscribe(chat_id=99, initial_cursor=10)

        subscribers = self.service.get_subscribed()

        self.assertEqual(
            {subscriber.chat_id for subscriber in subscribers},
            {77, 99},
        )

    def test_forget_deletes_all_persisted_subscriber_state(self) -> None:
        self.service.bootstrap_admin(chat_id=77, initial_cursor=10)

        self.assertTrue(self.service.forget(chat_id=77))
        self.assertIsNone(self.service.get(chat_id=77))
        self.assertFalse(self.service.forget(chat_id=77))

    def test_cursor_cannot_move_backwards(self) -> None:
        self.service.bootstrap_admin(chat_id=77, initial_cursor=10)

        with self.assertRaisesRegex(ValueError, "backwards"):
            self.service.advance_cursor(chat_id=77, article_id=9)


if __name__ == "__main__":
    unittest.main()
