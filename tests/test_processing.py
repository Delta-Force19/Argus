import unittest

from argus.processing import (
    ProcessingStage,
    ProcessingStatus,
)


class ProcessingEnumTests(unittest.TestCase):
    def test_processing_status_values_are_stable(self) -> None:
        self.assertEqual(
            [status.value for status in ProcessingStatus],
            [
                "pending",
                "running",
                "done",
                "failed",
            ],
        )

    def test_processing_stage_values_are_stable(self) -> None:
        self.assertEqual(
            [stage.value for stage in ProcessingStage],
            [
                "parsing",
                "discourse",
            ],
        )

    def test_processing_status_is_string_compatible(
            self,
    ) -> None:
        self.assertEqual(
            ProcessingStatus.PENDING,
            "pending",
        )
        self.assertEqual(
            ProcessingStatus.FAILED,
            "failed",
        )

    def test_processing_stage_is_string_compatible(
            self,
    ) -> None:
        self.assertEqual(
            ProcessingStage.PARSING,
            "parsing",
        )
        self.assertEqual(
            ProcessingStage.DISCOURSE,
            "discourse",
        )


if __name__ == "__main__":
    unittest.main()