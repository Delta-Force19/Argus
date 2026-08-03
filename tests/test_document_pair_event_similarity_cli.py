import unittest
from unittest.mock import patch

from typer.testing import CliRunner

from argus.interface.cli import app
from argus.services.document_pair_event_similarity_service import (
    DocumentPairEventSimilarity,
    EventSimilarityConfiguration,
    EventSimilaritySignal,
)


class DocumentPairEventSimilarityCliTests(unittest.TestCase):
    @patch("argus.interface.cli.get_document_pair_event_similarity")
    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    def test_prints_signals_and_explicitly_withholds_event_decision(
            self,
            upgrade_database,
            configure_logging,
            get_document_pair_event_similarity,
    ) -> None:
        get_document_pair_event_similarity.return_value = (
            DocumentPairEventSimilarity(
                left_document_id=1,
                left_document_version_id=11,
                right_document_id=2,
                right_document_version_id=22,
                combined_score=0.625,
                available_weight=0.8,
                signals=(
                    EventSimilaritySignal(
                        name="temporal",
                        available=False,
                        score=None,
                        configured_weight=0.2,
                        effective_weight=0.0,
                        contribution=None,
                        explanation=(
                            "Both publication timestamps are required."
                        ),
                    ),
                    EventSimilaritySignal(
                        name="entities",
                        available=True,
                        score=0.5,
                        configured_weight=0.5,
                        effective_weight=0.625,
                        contribution=0.3125,
                        explanation=(
                            "shared=1 union=2; set_similarity=jaccard"
                        ),
                    ),
                    EventSimilaritySignal(
                        name="lexical",
                        available=True,
                        score=1.0,
                        configured_weight=0.3,
                        effective_weight=0.375,
                        contribution=0.375,
                        explanation=(
                            "token_counts=20,20; "
                            "similarity=term_frequency_cosine"
                        ),
                    ),
                ),
                shared_entity_ids=(7,),
                limitations=(
                    "The combined score is not a decision.",
                ),
            )
        )

        result = CliRunner().invoke(
            app,
            [
                "compare-document-event-similarity",
                "--left-document-version-id",
                "11",
                "--right-document-version-id",
                "22",
                "--temporal-window-hours",
                "48",
            ],
        )

        self.assertEqual(result.exit_code, 0, result.output)
        upgrade_database.assert_called_once_with()
        configure_logging.assert_called_once_with()
        get_document_pair_event_similarity.assert_called_once_with(
            left_document_version_id=11,
            right_document_version_id=22,
            configuration=EventSimilarityConfiguration(
                temporal_window_hours=48.0,
            ),
        )
        self.assertIn(
            "combined_score=0.625 available_weight=0.8 "
            "same_event_decision=none",
            result.output,
        )
        self.assertIn("shared_entity_ids=7", result.output)
        self.assertIn(
            "signal='temporal' available=false score=none",
            result.output,
        )
        self.assertIn(
            "signal='entities' available=true score=0.5",
            result.output,
        )
        self.assertIn(
            "limitation='The combined score is not a decision.'",
            result.output,
        )

    @patch("argus.interface.cli.get_document_pair_event_similarity")
    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    def test_reports_fail_closed_input_error_without_traceback(
            self,
            upgrade_database,
            configure_logging,
            get_document_pair_event_similarity,
    ) -> None:
        get_document_pair_event_similarity.side_effect = ValueError(
            "Document entity resolution is not ready."
        )

        result = CliRunner().invoke(
            app,
            [
                "compare-document-event-similarity",
                "--left-document-version-id",
                "11",
                "--right-document-version-id",
                "22",
            ],
        )

        self.assertEqual(result.exit_code, 2)
        self.assertIn(
            "Document entity resolution is not ready.",
            result.output,
        )
        self.assertNotIn("Traceback", result.output)


if __name__ == "__main__":
    unittest.main()
