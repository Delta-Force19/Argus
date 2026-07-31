import unittest
from unittest.mock import patch

from argus.knowledge import EntityType
from argus.services.corpus_entity_readiness_service import (
    get_corpus_entity_readiness,
)
from argus.services.document_entity_coverage_service import (
    DocumentEntityCoverageCount,
    DocumentEntityCoverageReport,
    DocumentEntityCoverageStatus,
)
from argus.services.document_entity_readiness_service import (
    DocumentEntityReadinessStatus,
)


class CorpusEntityReadinessServiceTests(unittest.TestCase):
    @patch(
        "argus.services.corpus_entity_readiness_service."
        "get_document_entity_coverage_batch"
    )
    def test_reports_complete_corpus_counts(
            self,
            get_document_entity_coverage_batch,
    ) -> None:
        get_document_entity_coverage_batch.return_value = (
            self._coverage(1, safe=2),
            self._coverage(2),
            self._coverage(3, safe=1, unassigned=1),
            self._coverage(4, blocked=2),
            self._coverage(5, invalid=1),
        )

        report = get_corpus_entity_readiness(
            limit=3,
            entity_type=EntityType.ORGANIZATION,
            session_factory=object,
        )

        get_document_entity_coverage_batch.assert_called_once_with(
            item_limit=1,
            entity_type=EntityType.ORGANIZATION,
            session_factory=object,
        )
        self.assertEqual(report.document_version_count, 5)
        self.assertEqual(report.ready_document_version_count, 1)
        self.assertEqual(report.unsafe_document_version_count, 4)
        self.assertEqual(report.matched_document_version_count, 5)
        self.assertEqual(report.candidate_count, 7)
        self.assertEqual(report.safe_resolved_count, 3)
        self.assertEqual(report.unassigned_count, 1)
        self.assertEqual(report.blocked_count, 2)
        self.assertEqual(report.invalid_provenance_count, 1)
        self.assertEqual(len(report.items), 3)
        self.assertEqual(
            {
                item.status: item.count
                for item in report.counts_by_status
            },
            {
                DocumentEntityReadinessStatus.READY: 1,
                DocumentEntityReadinessStatus.NO_CANDIDATES: 1,
                DocumentEntityReadinessStatus.INCOMPLETE: 1,
                DocumentEntityReadinessStatus.BLOCKED: 1,
                DocumentEntityReadinessStatus.INVALID: 1,
            },
        )

    @patch(
        "argus.services.corpus_entity_readiness_service."
        "get_document_entity_coverage_batch"
    )
    def test_status_filter_only_bounds_detailed_rows(
            self,
            get_document_entity_coverage_batch,
    ) -> None:
        get_document_entity_coverage_batch.return_value = (
            self._coverage(1, safe=1),
            self._coverage(2, unassigned=1),
            self._coverage(3, unassigned=2),
        )

        report = get_corpus_entity_readiness(
            limit=1,
            status=DocumentEntityReadinessStatus.INCOMPLETE,
        )

        self.assertEqual(report.document_version_count, 3)
        self.assertEqual(report.ready_document_version_count, 1)
        self.assertEqual(report.matched_document_version_count, 2)
        self.assertEqual(len(report.items), 1)
        self.assertEqual(report.items[0].document_version_id, 2)

    def test_invalid_limit_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "limit"):
            get_corpus_entity_readiness(limit=0)

    @staticmethod
    def _coverage(
            document_version_id: int,
            *,
            safe: int = 0,
            not_entity: int = 0,
            unassigned: int = 0,
            blocked: int = 0,
            invalid: int = 0,
    ) -> DocumentEntityCoverageReport:
        values = {
            DocumentEntityCoverageStatus.SAFE_RESOLVED: safe,
            DocumentEntityCoverageStatus.NOT_ENTITY: not_entity,
            DocumentEntityCoverageStatus.UNASSIGNED: unassigned,
            DocumentEntityCoverageStatus.BLOCKED: blocked,
            DocumentEntityCoverageStatus.INVALID_PROVENANCE: invalid,
        }
        return DocumentEntityCoverageReport(
            document_version_id=document_version_id,
            document_id=document_version_id + 100,
            version_number=1,
            candidate_count=sum(values.values()),
            counts_by_status=tuple(
                DocumentEntityCoverageCount(
                    status=status,
                    count=values[status],
                )
                for status in DocumentEntityCoverageStatus
            ),
            items=(),
        )
