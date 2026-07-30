import unittest
from dataclasses import replace
from unittest.mock import patch

from argus.knowledge import EntityType
from argus.services.corpus_entity_readiness_service import (
    CorpusEntityReadinessReport,
)
from argus.services.document_entity_readiness_service import (
    DocumentEntityReadinessReport,
    DocumentEntityReadinessStatus,
)
from argus.services.ready_document_selector_service import (
    select_ready_document_versions,
)


class ReadyDocumentSelectorServiceTests(unittest.TestCase):
    @patch(
        "argus.services.ready_document_selector_service."
        "get_corpus_entity_readiness"
    )
    def test_selects_only_ready_versions_with_complete_count(
            self,
            get_corpus_entity_readiness,
    ) -> None:
        get_corpus_entity_readiness.return_value = self._corpus(
            ready_count=3,
            matched_count=3,
            items=(
                self._ready_report(11),
                self._ready_report(12),
            ),
        )

        selection = select_ready_document_versions(
            limit=2,
            entity_type=EntityType.ORGANIZATION,
            session_factory=object,
        )

        get_corpus_entity_readiness.assert_called_once_with(
            limit=2,
            status=DocumentEntityReadinessStatus.READY,
            entity_type=EntityType.ORGANIZATION,
            session_factory=object,
        )
        self.assertEqual(selection.ready_document_version_count, 3)
        self.assertEqual(selection.selected_document_version_count, 2)
        self.assertEqual(
            tuple(item.document_version_id for item in selection.items),
            (11, 12),
        )
        self.assertTrue(
            all(
                item.candidate_count == item.safe_resolved_count
                for item in selection.items
            )
        )

    @patch(
        "argus.services.ready_document_selector_service."
        "get_corpus_entity_readiness"
    )
    def test_rejects_unsafe_or_inconsistent_corpus_results(
            self,
            get_corpus_entity_readiness,
    ) -> None:
        unsafe = replace(
            self._ready_report(11),
            status=DocumentEntityReadinessStatus.BLOCKED,
            ready_for_downstream_use=False,
            blocked_count=1,
        )

        get_corpus_entity_readiness.return_value = self._corpus(
            ready_count=1,
            matched_count=1,
            items=(unsafe,),
        )
        with self.assertRaisesRegex(ValueError, "unsafe"):
            select_ready_document_versions(
                entity_type=EntityType.ORGANIZATION,
            )

        get_corpus_entity_readiness.return_value = self._corpus(
            ready_count=2,
            matched_count=1,
            items=(self._ready_report(11),),
        )
        with self.assertRaisesRegex(ValueError, "inconsistent"):
            select_ready_document_versions(
                entity_type=EntityType.ORGANIZATION,
            )

    def test_invalid_limit_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "limit"):
            select_ready_document_versions(limit=0)

    @staticmethod
    def _ready_report(
            document_version_id: int,
    ) -> DocumentEntityReadinessReport:
        return DocumentEntityReadinessReport(
            document_version_id=document_version_id,
            document_id=document_version_id + 100,
            version_number=1,
            entity_type=EntityType.ORGANIZATION,
            status=DocumentEntityReadinessStatus.READY,
            ready_for_downstream_use=True,
            candidate_count=2,
            safe_resolved_count=2,
            unassigned_count=0,
            blocked_count=0,
            invalid_provenance_count=0,
        )

    @staticmethod
    def _corpus(
            *,
            ready_count: int,
            matched_count: int,
            items: tuple[DocumentEntityReadinessReport, ...],
    ) -> CorpusEntityReadinessReport:
        return CorpusEntityReadinessReport(
            entity_type=EntityType.ORGANIZATION,
            document_version_count=4,
            ready_document_version_count=ready_count,
            unsafe_document_version_count=4 - ready_count,
            matched_document_version_count=matched_count,
            candidate_count=8,
            safe_resolved_count=4,
            unassigned_count=2,
            blocked_count=1,
            invalid_provenance_count=1,
            counts_by_status=(),
            items=items,
        )
