import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from argus.documents import DerivedArtifactType, DocumentType
from argus.knowledge import EntityType
from argus.services.document_analysis_input_service import (
    AnalysisInputDocument,
    AnalysisInputText,
    DocumentAnalysisInputBundle,
)
from argus.services.document_entity_projection_service import (
    DocumentEntityProjection,
    DocumentResolvedEntity,
)
from argus.services.document_entity_readiness_service import (
    DocumentEntityReadinessReport,
    DocumentEntityReadinessStatus,
)
from argus.services.document_pair_event_similarity_service import (
    EventSimilarityConfiguration,
    compare_document_pair_event_similarity,
    get_document_pair_event_similarity,
)


class DocumentPairEventSimilarityServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.published_at = datetime(2026, 8, 3, tzinfo=timezone.utc)
        self.left = self._bundle(
            document_id=1,
            document_version_id=11,
            published_at=self.published_at,
            text="alpha beta gamma delta " * 5,
            entity_ids=(1, 2),
        )
        self.right = self._bundle(
            document_id=2,
            document_version_id=22,
            published_at=self.published_at + timedelta(hours=36),
            text="alpha beta gamma delta " * 5,
            entity_ids=(2, 3),
        )

    def test_exposes_each_signal_and_weighted_contribution(self) -> None:
        result = compare_document_pair_event_similarity(
            self.left,
            self.right,
        )

        self.assertEqual(result.shared_entity_ids, (2,))
        self.assertEqual(result.available_weight, 1.0)
        signals = {item.name: item for item in result.signals}
        self.assertEqual(signals["temporal"].score, 0.5)
        self.assertAlmostEqual(signals["entities"].score, 1 / 3)
        self.assertEqual(signals["lexical"].score, 1.0)
        self.assertAlmostEqual(result.combined_score, 17 / 30)
        self.assertAlmostEqual(signals["temporal"].contribution, 0.1)
        self.assertAlmostEqual(signals["entities"].contribution, 1 / 6)
        self.assertAlmostEqual(signals["lexical"].contribution, 0.3)
        self.assertIn("not a probability", result.limitations[0])

    def test_unavailable_signals_are_not_treated_as_zero(self) -> None:
        left = self._bundle(
            document_id=1,
            document_version_id=11,
            published_at=None,
            text="short text",
            entity_ids=(1, 2),
        )
        right = self._bundle(
            document_id=2,
            document_version_id=22,
            published_at=self.published_at,
            text="other short text",
            entity_ids=(2, 3),
        )

        result = compare_document_pair_event_similarity(left, right)

        self.assertEqual(result.available_weight, 0.5)
        self.assertAlmostEqual(result.combined_score, 1 / 3)
        signals = {item.name: item for item in result.signals}
        self.assertFalse(signals["temporal"].available)
        self.assertFalse(signals["lexical"].available)
        self.assertEqual(signals["entities"].effective_weight, 1.0)
        self.assertTrue(
            any("temporal, lexical" in item for item in result.limitations)
        )

    def test_no_resolved_entities_makes_entity_signal_unavailable(self) -> None:
        left = self._bundle(
            document_id=1,
            document_version_id=11,
            published_at=self.published_at,
            text="alpha beta gamma delta " * 5,
            entity_ids=(),
        )
        right = self._bundle(
            document_id=2,
            document_version_id=22,
            published_at=self.published_at,
            text="alpha beta gamma delta " * 5,
            entity_ids=(),
        )

        result = compare_document_pair_event_similarity(left, right)

        entity = next(
            item for item in result.signals if item.name == "entities"
        )
        self.assertFalse(entity.available)
        self.assertEqual(result.available_weight, 0.5)
        self.assertEqual(result.combined_score, 1.0)

    def test_rejects_self_comparison_and_two_versions_of_one_document(
            self,
    ) -> None:
        with self.assertRaisesRegex(ValueError, "distinct document versions"):
            compare_document_pair_event_similarity(self.left, self.left)

        other_version = self._bundle(
            document_id=1,
            document_version_id=12,
            published_at=self.published_at,
            text="alpha beta gamma delta " * 5,
            entity_ids=(1, 2),
        )
        with self.assertRaisesRegex(ValueError, "distinct documents"):
            compare_document_pair_event_similarity(
                self.left,
                other_version,
            )

    def test_configuration_is_validated(self) -> None:
        with self.assertRaisesRegex(ValueError, "greater than zero"):
            compare_document_pair_event_similarity(
                self.left,
                self.right,
                configuration=EventSimilarityConfiguration(
                    temporal_window_hours=0,
                ),
            )

    def test_rejects_video_page_html_without_transcript(self) -> None:
        left = self._bundle(
            document_id=1,
            document_version_id=11,
            published_at=self.published_at,
            text="Generic bulletin description.",
            entity_ids=(1,),
            identifier_value="https://example.test/video/bulletin",
        )

        with self.assertRaisesRegex(ValueError, "not a transcript"):
            compare_document_pair_event_similarity(left, self.right)
        with self.assertRaisesRegex(ValueError, "cannot be negative"):
            compare_document_pair_event_similarity(
                self.left,
                self.right,
                configuration=EventSimilarityConfiguration(
                    entity_weight=-1,
                ),
            )

    def test_no_weighted_available_signal_withholds_combined_score(
            self,
    ) -> None:
        left = self._bundle(
            document_id=1,
            document_version_id=11,
            published_at=None,
            text="alpha beta gamma delta " * 5,
            entity_ids=(1, 2),
        )
        right = self._bundle(
            document_id=2,
            document_version_id=22,
            published_at=self.published_at,
            text="alpha beta gamma delta " * 5,
            entity_ids=(1, 2),
        )

        result = compare_document_pair_event_similarity(
            left,
            right,
            configuration=EventSimilarityConfiguration(
                temporal_weight=1.0,
                entity_weight=0.0,
                lexical_weight=0.0,
            ),
        )

        self.assertEqual(result.available_weight, 0.0)
        self.assertIsNone(result.combined_score)
        self.assertEqual(
            tuple(item.contribution for item in result.signals),
            (None, 0.0, 0.0),
        )
        self.assertTrue(
            any("score was withheld" in item for item in result.limitations)
        )

    @patch(
        "argus.services.document_pair_event_similarity_service."
        "build_document_analysis_input"
    )
    def test_loads_both_fail_closed_bundles_in_one_session(
            self,
            build_document_analysis_input,
    ) -> None:
        build_document_analysis_input.side_effect = (
            self.left,
            self.right,
        )
        session = _ContextSession()
        factory_calls = 0

        def factory():
            nonlocal factory_calls
            factory_calls += 1
            return session

        result = get_document_pair_event_similarity(
            left_document_version_id=11,
            right_document_version_id=22,
            session_factory=factory,
        )

        self.assertEqual(factory_calls, 1)
        self.assertEqual(result.left_document_version_id, 11)
        self.assertEqual(result.right_document_version_id, 22)
        self.assertEqual(
            build_document_analysis_input.call_args_list[0].args[0],
            session,
        )
        self.assertEqual(
            build_document_analysis_input.call_args_list[1].args[0],
            session,
        )
        self.assertTrue(session.exited)

    def _bundle(
            self,
            *,
            document_id: int,
            document_version_id: int,
            published_at: datetime | None,
            text: str,
            entity_ids: tuple[int, ...],
            identifier_value: str | None = None,
    ) -> DocumentAnalysisInputBundle:
        entities = tuple(
            DocumentResolvedEntity(
                entity_id=entity_id,
                entity_type=EntityType.ORGANIZATION,
                canonical_name=f"entity-{entity_id}",
                canonical_entity_candidate_id=100 + entity_id,
                occurrences=(),
                active_resolutions=(),
            )
            for entity_id in entity_ids
        )
        return DocumentAnalysisInputBundle(
            entity_type=None,
            document=AnalysisInputDocument(
                document_id=document_id,
                document_version_id=document_version_id,
                version_number=1,
                document_type=DocumentType.ARTICLE,
                identifier_scheme="url",
                identifier_value=(
                    identifier_value
                    or f"https://example.test/{document_id}"
                ),
                title=f"Document {document_id}",
                language="en",
                source_id=document_id,
                raw_artifact_id=200 + document_id,
                raw_content_hash=f"{document_id:064x}",
                raw_hash_algorithm="sha256",
                media_type="text/html",
                published_at=published_at,
            ),
            text=AnalysisInputText(
                derived_artifact_id=300 + document_id,
                artifact_type=DerivedArtifactType.EXTRACTED_TEXT,
                method="test",
                method_version="1",
                schema_version="1",
                content_hash=f"{document_version_id:064x}",
                text=text,
                character_count=len(text),
                quality_limitations=(),
            ),
            readiness=DocumentEntityReadinessReport(
                document_version_id=document_version_id,
                document_id=document_id,
                version_number=1,
                entity_type=None,
                status=DocumentEntityReadinessStatus.READY,
                ready_for_downstream_use=True,
                candidate_count=max(len(entity_ids), 1),
                safe_resolved_count=len(entity_ids),
                unassigned_count=0,
                blocked_count=0,
                invalid_provenance_count=0,
                not_entity_count=0 if entity_ids else 1,
            ),
            entities=DocumentEntityProjection(
                document_version_id=document_version_id,
                document_id=document_id,
                version_number=1,
                resolved_entity_count=len(entities),
                resolved_occurrence_count=0,
                items=entities,
            ),
        )


class _ContextSession:
    def __init__(self) -> None:
        self.exited = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.exited = True
