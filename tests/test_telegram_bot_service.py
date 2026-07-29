import unittest
from datetime import datetime
from unittest.mock import Mock
from zoneinfo import ZoneInfo

from argus.delivery.telegram import (
    TELEGRAM_MAX_MESSAGE_CHARS,
    TelegramMessage,
    TelegramUpdate,
)
from argus.services.latest_news_service import (
    LatestNewsItem,
    LatestNewsReport,
)
from argus.services.telegram_bot_service import (
    TelegramAutomaticDelivery,
    TelegramBotSettings,
    TelegramNewsBot,
    format_automatic_news_batches,
    format_latest_news_messages,
)
from argus.services.telegram_subscriber_service import (
    TelegramSubscriberView,
)
from argus.telegram_subscriptions import TelegramSubscriberStatus


class FakeGateway:
    def __init__(
            self,
            batches: tuple[tuple[TelegramUpdate, ...], ...] = (),
    ) -> None:
        self._batches = iter(batches)
        self.offsets: list[int | None] = []
        self.sent: list[tuple[int, str]] = []

    def get_updates(
            self,
            *,
            offset: int | None,
            timeout_seconds: int,
    ) -> tuple[TelegramUpdate, ...]:
        self.offsets.append(offset)
        return next(self._batches)

    def send_message(self, *, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))


class FakeSubscribers:
    def __init__(
            self,
            subscribers: tuple[TelegramSubscriberView, ...] = (),
    ) -> None:
        self.items = {item.chat_id: item for item in subscribers}
        self.advanced: list[tuple[int, int]] = []

    def get(self, *, chat_id: int) -> TelegramSubscriberView | None:
        return self.items.get(chat_id)

    def request_access(
            self,
            *,
            chat_id: int,
    ) -> tuple[TelegramSubscriberView, bool]:
        existing = self.items.get(chat_id)
        if existing is not None:
            return existing, False
        item = subscriber_view(chat_id=chat_id, approved=False)
        self.items[chat_id] = item
        return item, True

    def approve(
            self,
            *,
            chat_id: int,
            initial_cursor: int,
    ) -> TelegramSubscriberView | None:
        if chat_id not in self.items:
            return None
        item = subscriber_view(
            chat_id=chat_id,
            approved=True,
            cursor=initial_cursor,
        )
        self.items[chat_id] = item
        return item

    def register(
            self,
            *,
            chat_id: int,
            initial_cursor: int,
    ) -> tuple[TelegramSubscriberView, bool]:
        current = self.items.get(chat_id)
        created = current is None
        if current is None or not current.is_approved:
            current = subscriber_view(
                chat_id=chat_id,
                cursor=initial_cursor,
            )
            self.items[chat_id] = current
        return current, created

    def subscribe(
            self,
            *,
            chat_id: int,
            initial_cursor: int,
    ) -> TelegramSubscriberView | None:
        current = self.items.get(chat_id)
        if current is None or not current.is_approved:
            return None
        item = subscriber_view(
            chat_id=chat_id,
            subscribed=True,
            cursor=initial_cursor,
        )
        self.items[chat_id] = item
        return item

    def unsubscribe(
            self,
            *,
            chat_id: int,
    ) -> TelegramSubscriberView | None:
        current = self.items.get(chat_id)
        if current is None or not current.is_approved:
            return None
        item = subscriber_view(
            chat_id=chat_id,
            subscribed=False,
            cursor=current.last_delivered_article_id,
        )
        self.items[chat_id] = item
        return item

    def forget(self, *, chat_id: int) -> bool:
        return self.items.pop(chat_id, None) is not None

    def get_subscribed(self) -> tuple[TelegramSubscriberView, ...]:
        return tuple(
            item for item in self.items.values()
            if item.is_approved and item.is_subscribed
        )

    def advance_cursor(
            self,
            *,
            chat_id: int,
            article_id: int,
    ) -> None:
        current = self.items[chat_id]
        self.items[chat_id] = subscriber_view(
            chat_id=chat_id,
            subscribed=current.is_subscribed,
            cursor=article_id,
        )
        self.advanced.append((chat_id, article_id))


def subscriber_view(
        *,
        chat_id: int,
        approved: bool = True,
        subscribed: bool = False,
        cursor: int | None = None,
) -> TelegramSubscriberView:
    return TelegramSubscriberView(
        chat_id=chat_id,
        status=(
            TelegramSubscriberStatus.APPROVED
            if approved
            else TelegramSubscriberStatus.PENDING
        ),
        is_subscribed=subscribed,
        last_delivered_article_id=cursor,
    )


class TelegramNewsBotTests(unittest.TestCase):
    def test_new_start_activates_public_access_immediately(self) -> None:
        subscribers = FakeSubscribers()
        gateway = FakeGateway(
            batches=(
                (
                    TelegramUpdate(
                        update_id=10,
                        message=TelegramMessage(
                            chat_id=99,
                            text="/start",
                        ),
                    ),
                ),
            )
        )
        bot = self._bot(
            gateway=gateway,
            subscribers=subscribers,
        )

        bot.poll(timeout_seconds=5, max_polls=1)

        self.assertEqual(gateway.offsets, [None])
        self.assertEqual(gateway.sent[0][0], 99)
        self.assertIn("Access is active", gateway.sent[0][1])
        self.assertTrue(subscribers.items[99].is_approved)

    def test_user_can_load_latest_without_start_or_approval(self) -> None:
        subscribers = FakeSubscribers(
            (subscriber_view(chat_id=99, approved=False),)
        )
        gateway = FakeGateway(
            batches=(
                (
                    TelegramUpdate(
                        update_id=10,
                        message=TelegramMessage(
                            chat_id=99,
                            text="/latest@argus_bot",
                        ),
                    ),
                ),
            )
        )
        news_loader = Mock(return_value=self._report())
        bot = self._bot(
            gateway=gateway,
            subscribers=subscribers,
            news_loader=news_loader,
        )

        bot.poll(timeout_seconds=5, max_polls=1)

        self.assertTrue(subscribers.items[99].is_approved)
        news_loader.assert_called_once_with(
            limit=3,
            excerpt_chars=120,
        )
        self.assertTrue(
            any(
                chat_id == 99 and "Example &amp; News" in text
                for chat_id, text in gateway.sent
            )
        )

    def test_latest_is_rate_limited_per_user(self) -> None:
        subscribers = FakeSubscribers()
        gateway = FakeGateway(
            batches=(
                (
                    TelegramUpdate(
                        update_id=1,
                        message=TelegramMessage(
                            chat_id=99,
                            text="/latest",
                        ),
                    ),
                    TelegramUpdate(
                        update_id=2,
                        message=TelegramMessage(
                            chat_id=99,
                            text="/latest",
                        ),
                    ),
                ),
            )
        )
        news_loader = Mock(return_value=self._report())
        bot = self._bot(
            gateway=gateway,
            subscribers=subscribers,
            news_loader=news_loader,
            monotonic=Mock(side_effect=(0.0, 100.0, 101.0)),
        )

        bot.poll(timeout_seconds=5, max_polls=1)

        news_loader.assert_called_once_with(
            limit=3,
            excerpt_chars=120,
        )
        self.assertIn("Please wait", gateway.sent[-1][1])

    def test_subscribe_and_unsubscribe_are_per_user(self) -> None:
        subscribers = FakeSubscribers((subscriber_view(chat_id=99),))
        gateway = FakeGateway(
            batches=(
                (
                    TelegramUpdate(
                        update_id=1,
                        message=TelegramMessage(
                            chat_id=99,
                            text="/subscribe",
                        ),
                    ),
                    TelegramUpdate(
                        update_id=2,
                        message=TelegramMessage(
                            chat_id=99,
                            text="/unsubscribe",
                        ),
                    ),
                ),
            )
        )
        bot = self._bot(
            gateway=gateway,
            subscribers=subscribers,
        )

        bot.poll(timeout_seconds=5, max_polls=1)

        self.assertFalse(subscribers.items[99].is_subscribed)
        self.assertEqual(
            subscribers.items[99].last_delivered_article_id,
            50,
        )
        self.assertIn("enabled", gateway.sent[0][1])
        self.assertIn("disabled", gateway.sent[1][1])

    def test_forgetme_deletes_state_without_registering_again(self) -> None:
        subscribers = FakeSubscribers(
            (
                subscriber_view(
                    chat_id=99,
                    subscribed=True,
                    cursor=50,
                ),
            )
        )
        gateway = FakeGateway(
            batches=(
                (
                    TelegramUpdate(
                        update_id=1,
                        message=TelegramMessage(
                            chat_id=99,
                            text="/forgetme",
                        ),
                    ),
                ),
            )
        )
        bot = self._bot(
            gateway=gateway,
            subscribers=subscribers,
        )

        bot.poll(timeout_seconds=5, max_polls=1)

        self.assertNotIn(99, subscribers.items)
        self.assertIn("has been deleted", gateway.sent[0][1])

    def test_poll_runs_automatic_cycle_when_due(self) -> None:
        gateway = FakeGateway(batches=((), ()))
        automatic_delivery = Mock()
        monotonic = Mock(side_effect=(0.0, 0.0, 0.0, 10.0))
        bot = TelegramNewsBot(
            gateway=gateway,
            subscribers=FakeSubscribers(),
            output_timezone=ZoneInfo("UTC"),
            automatic_delivery=automatic_delivery,
            automatic_interval_seconds=60,
            monotonic=monotonic,
        )

        bot.poll(timeout_seconds=5, max_polls=2)

        automatic_delivery.run_cycle.assert_called_once_with()

    def test_settings_accept_admin_and_legacy_chat_id(self) -> None:
        with self.assertRaisesRegex(
                ValueError,
                "ARGUS_TELEGRAM_BOT_TOKEN",
        ):
            TelegramBotSettings.from_environment({})
        with self.assertRaisesRegex(ValueError, "must be an integer"):
            TelegramBotSettings.from_environment(
                {
                    "ARGUS_TELEGRAM_BOT_TOKEN": "token",
                    "ARGUS_TELEGRAM_ADMIN_CHAT_ID": "not-an-id",
                }
            )

        preferred = TelegramBotSettings.from_environment(
            {
                "ARGUS_TELEGRAM_BOT_TOKEN": " token ",
                "ARGUS_TELEGRAM_ADMIN_CHAT_ID": "77",
                "ARGUS_TELEGRAM_ALLOWED_CHAT_ID": "88",
            }
        )
        legacy = TelegramBotSettings.from_environment(
            {
                "ARGUS_TELEGRAM_BOT_TOKEN": "token",
                "ARGUS_TELEGRAM_ALLOWED_CHAT_ID": "88",
            }
        )
        token_only = TelegramBotSettings.from_environment(
            {"ARGUS_TELEGRAM_BOT_TOKEN": "token"}
        )

        self.assertEqual(preferred.admin_chat_id, 77)
        self.assertEqual(legacy.admin_chat_id, 88)
        self.assertIsNone(token_only.admin_chat_id)

    def test_automatic_delivery_tracks_each_subscriber(self) -> None:
        subscribers = FakeSubscribers(
            (
                subscriber_view(
                    chat_id=77,
                    subscribed=True,
                    cursor=10,
                ),
                subscriber_view(
                    chat_id=99,
                    subscribed=True,
                    cursor=11,
                ),
            )
        )
        gateway = FakeGateway()
        parser = Mock()

        def load_news(
                *,
                after_article_id: int,
                limit: int,
                excerpt_chars: int,
        ) -> LatestNewsReport:
            return LatestNewsReport(
                items=tuple(
                    self._item(article_id=article_id)
                    for article_id in range(after_article_id + 1, 13)
                )
            )

        delivery = TelegramAutomaticDelivery(
            gateway=gateway,
            subscribers=subscribers,
            output_timezone=ZoneInfo("UTC"),
            collector=Mock(),
            parser=parser,
            news_loader=load_news,
        )

        delivered = delivery.run_cycle()

        self.assertEqual(delivered, 3)
        parser.assert_called_once_with(
            limit=20,
            retry_failed=False,
            newest_first=False,
            after_article_id=10,
        )
        self.assertEqual(subscribers.items[77].last_delivered_article_id, 12)
        self.assertEqual(subscribers.items[99].last_delivered_article_id, 12)
        self.assertEqual({chat_id for chat_id, _ in gateway.sent}, {77, 99})

    def test_failed_send_does_not_advance_recipient_cursor(self) -> None:
        class FailingGateway(FakeGateway):
            def send_message(self, *, chat_id: int, text: str) -> None:
                if chat_id == 77:
                    raise RuntimeError("network failure")
                super().send_message(chat_id=chat_id, text=text)

        subscribers = FakeSubscribers(
            (
                subscriber_view(
                    chat_id=77,
                    subscribed=True,
                    cursor=10,
                ),
                subscriber_view(
                    chat_id=99,
                    subscribed=True,
                    cursor=10,
                ),
            )
        )
        gateway = FailingGateway()
        delivery = TelegramAutomaticDelivery(
            gateway=gateway,
            subscribers=subscribers,
            output_timezone=ZoneInfo("UTC"),
            collector=Mock(),
            parser=Mock(),
            news_loader=Mock(
                return_value=LatestNewsReport(
                    items=(self._item(article_id=11),)
                )
            ),
        )

        with self.assertLogs(
                "argus.services.telegram_bot_service",
                level="ERROR",
        ) as captured:
            delivered = delivery.run_cycle()

        self.assertEqual(delivered, 1)
        log_output = "\n".join(captured.output)
        self.assertIn("failed for one recipient", log_output)
        self.assertNotIn("77", log_output)
        self.assertEqual(subscribers.advanced, [(99, 11)])
        self.assertEqual(
            subscribers.items[77].last_delivered_article_id,
            10,
        )
        self.assertEqual(
            subscribers.items[99].last_delivered_article_id,
            11,
        )

    def test_formatters_split_messages_and_expose_batch_cursor(self) -> None:
        report = LatestNewsReport(
            items=tuple(
                self._item(
                    article_id=index,
                    title=f"Headline {index}",
                    excerpt="x" * 1000,
                )
                for index in range(1, 11)
            )
        )

        messages = format_latest_news_messages(
            report,
            output_timezone=ZoneInfo("UTC"),
        )
        batches = format_automatic_news_batches(
            report,
            output_timezone=ZoneInfo("UTC"),
        )

        self.assertGreater(len(messages), 1)
        self.assertTrue(
            all(
                len(message) <= TELEGRAM_MAX_MESSAGE_CHARS
                for message in messages
            )
        )
        self.assertEqual(batches[-1].last_article_id, 10)
        self.assertEqual(
            sorted(batch.last_article_id for batch in batches),
            [batch.last_article_id for batch in batches],
        )

    def _bot(
            self,
            *,
            gateway: FakeGateway,
            subscribers: FakeSubscribers,
            news_loader=Mock,
            monotonic=Mock,
    ) -> TelegramNewsBot:
        if news_loader is Mock:
            news_loader = Mock(return_value=self._report())
        if monotonic is Mock:
            monotonic = Mock(return_value=100.0)
        return TelegramNewsBot(
            gateway=gateway,
            subscribers=subscribers,
            output_timezone=ZoneInfo("Europe/Amsterdam"),
            news_limit=3,
            excerpt_chars=120,
            news_loader=news_loader,
            highest_article_id_loader=Mock(return_value=50),
            monotonic=monotonic,
        )

    @staticmethod
    def _report() -> LatestNewsReport:
        return LatestNewsReport(
            items=(
                LatestNewsItem(
                    article_id=1,
                    published_at=datetime(2026, 7, 28, 18, 30),
                    fetched_at=datetime(2026, 7, 28, 18, 35),
                    source="Example & News",
                    title="First <headline>",
                    url="https://example.test/story?a=1&b=2",
                    language="en",
                    parsing_status="done",
                    excerpt_source="content",
                    excerpt="First paragraph.",
                ),
            )
        )

    @staticmethod
    def _item(
            *,
            article_id: int,
            title: str = "Headline",
            excerpt: str = "Excerpt",
    ) -> LatestNewsItem:
        return LatestNewsItem(
            article_id=article_id,
            published_at=datetime(2026, 7, 28, 18, 30),
            fetched_at=datetime(2026, 7, 28, 18, 35),
            source="Example",
            title=title,
            url=f"https://example.test/{article_id}",
            language="en",
            parsing_status="done",
            excerpt_source="content",
            excerpt=excerpt,
        )


if __name__ == "__main__":
    unittest.main()
