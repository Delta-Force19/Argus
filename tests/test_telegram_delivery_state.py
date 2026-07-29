import json
import tempfile
import unittest
from pathlib import Path

from argus.delivery.state import JsonDeliveryCursorStore


class JsonDeliveryCursorStoreTests(unittest.TestCase):
    def test_missing_state_initializes_as_none_and_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "state.json"
            store = JsonDeliveryCursorStore(path)

            self.assertIsNone(store.load())
            store.save(42)

            self.assertEqual(store.load(), 42)
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")),
                {"version": 1, "last_article_id": 42},
            )
            self.assertFalse(path.with_suffix(".json.tmp").exists())

    def test_rejects_corrupt_or_negative_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text('{"version": 1}', encoding="utf-8")
            store = JsonDeliveryCursorStore(path)

            with self.assertRaisesRegex(ValueError, "Invalid"):
                store.load()
            with self.assertRaisesRegex(ValueError, "negative"):
                store.save(-1)


if __name__ == "__main__":
    unittest.main()
