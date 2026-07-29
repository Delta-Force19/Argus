import unittest

import httpx

from argus.delivery.telegram import (
    TELEGRAM_MAX_MESSAGE_CHARS,
    TelegramAPIError,
    TelegramBotClient,
)


class TelegramBotClientTests(unittest.TestCase):
    token = "123456:secret-token"

    def test_get_updates_normalizes_messages_and_ignores_bad_rows(
            self,
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertTrue(request.url.path.endswith("/getUpdates"))
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": [
                        {
                            "update_id": 4,
                            "message": {
                                "chat": {"id": 77},
                                "text": "/latest",
                            },
                        },
                        {"update_id": 5, "edited_message": {}},
                        {"invalid": True},
                    ],
                },
            )

        http_client = httpx.Client(
            transport=httpx.MockTransport(handler)
        )
        client = TelegramBotClient(
            token=self.token,
            http_client=http_client,
        )

        updates = client.get_updates(
            offset=4,
            timeout_seconds=30,
        )

        self.assertEqual([update.update_id for update in updates], [4, 5])
        self.assertEqual(updates[0].message.chat_id, 77)
        self.assertEqual(updates[0].message.text, "/latest")
        self.assertIsNone(updates[1].message)

    def test_send_message_uses_html_and_disables_preview(self) -> None:
        captured: dict[str, object] = {}

        class CaptureTransport(httpx.BaseTransport):
            def handle_request(
                    self,
                    request: httpx.Request,
            ) -> httpx.Response:
                import json

                captured.update(json.loads(request.read()))
                return httpx.Response(
                    200,
                    json={"ok": True, "result": {}},
                )

        client = TelegramBotClient(
            token=self.token,
            http_client=httpx.Client(transport=CaptureTransport()),
        )

        client.send_message(chat_id=77, text="<b>News</b>")

        self.assertEqual(captured["chat_id"], 77)
        self.assertEqual(captured["parse_mode"], "HTML")
        self.assertIs(captured["disable_web_page_preview"], True)

    def test_rejects_oversized_message_before_request(self) -> None:
        client = TelegramBotClient(
            token=self.token,
            http_client=httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, json={"ok": True})
                )
            ),
        )

        with self.assertRaisesRegex(ValueError, "4096"):
            client.send_message(
                chat_id=77,
                text="x" * (TELEGRAM_MAX_MESSAGE_CHARS + 1),
            )

    def test_http_error_does_not_expose_token(self) -> None:
        client = TelegramBotClient(
            token=self.token,
            http_client=httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        401,
                        json={"ok": False},
                    )
                )
            ),
        )

        with self.assertRaises(TelegramAPIError) as raised:
            client.get_updates(offset=None, timeout_seconds=1)

        self.assertNotIn(self.token, str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)


if __name__ == "__main__":
    unittest.main()
