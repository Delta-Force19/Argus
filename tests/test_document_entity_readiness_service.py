import unittest
from unittest.mock import patch

from argus.knowledge import EntityType
from argus.services.document_entity_coverage_service import (
    DocumentEntityCoverageCount,
    DocumentEntityCoverageReport,
    DocumentEntityCoverageStatus,
)
from argus.services.document_entity_readiness_service import (
    DocumentEntityReadinessStatus,
    get_document_entity_readiness,
    require_document_entity_readiness,
)


class DocumentEntityReadinessServiceTests(unittest.TestCase):
    @patch(
        "argus.services.document_entity_readiness_service."
        "get_document_entity_coverage"
    )
    def test_all_candidates_must_be_safely_resolved(
            self,
            get_document_entity_coverage,
    ) -> None:
        get_document_entity_coverage.return_value = self._coverage(
            safe=3,
        )

        report = get_document_entity_readiness(
            document_version_id=21,
            entity_type=EntityType.ORGANIZATION,
            session_factory=object,
        )

        self.assertIs(report.status, DocumentEntityReadinessStatus.READY)
        self.assertTrue(report.ready_for_downstream_use)
        self.assertEqual(report.safe_resolved_count, 3)
        self.assertIs(report.entity_type, EntityType.ORGANIZATION)
        get_document_entity_coverage.assert_called_once_with(
            document_version_id=21,
            limit=1,
            entity_type=EntityType.ORGANIZATION,
            session_factory=object,
        )

    @patch(
        "argus.services.document_entity_readiness_service."
        "get_document_entity_coverage"
    )
    def test_empty_and_incomplete_documents_are_not_ready(
            self,
            get_document_entity_coverage,
    ) -> None:
        get_document_entity_coverage.return_value = self._coverage()
        empty = get_document_entity_readiness(document_version_id=21)

        get_document_entity_coverage.return_value = self._coverage(
            safe=2,
            unassigned=1,
        )
        incomplete = get_document_entity_readiness(
            document_version_id=21,
        )

        self.assertIs(
            empty.status,
            DocumentEntityReadinessStatus.NO_CANDIDATES,
        )
        self.assertFalse(empty.ready_for_downstream_use)
        self.assertIs(
            incomplete.status,
            DocumentEntityReadinessStatus.INCOMPLETE,
        )
        self.assertFalse(incomplete.ready_for_downstream_use)

    @patch(
        "argus.services.document_entity_readiness_service."
        "get_document_entity_coverage"
    )
    def test_failures_have_deterministic_conservative_precedence(
            self,
            get_document_entity_coverage,
    ) -> None:
        get_document_entity_coverage.return_value = self._coverage(
            safe=1,
            unassigned=1,
            blocked=1,
        )
        blocked = get_document_entity_readiness(
            document_version_id=21,
        )

        get_document_entity_coverage.return_value = self._coverage(
            safe=1,
            unassigned=1,
            blocked=1,
            invalid=1,
        )
        invalid = get_document_entity_readiness(
            document_version_id=21,
        )

        self.assertIs(
            blocked.status,
            DocumentEntityReadinessStatus.BLOCKED,
        )
        self.assertIs(
            invalid.status,
            DocumentEntityReadinessStatus.INVALID,
        )

    @patch(
        "argus.services.document_entity_readiness_service."
        "get_document_entity_coverage"
    )
    def test_required_boundary_rejects_non_ready_document(
            self,
            get_document_entity_coverage,
    ) -> None:
        get_document_entity_coverage.return_value = self._coverage(
            unassigned=1,
        )

        with self.assertRaisesRegex(
                ValueError,
                "document_version_id=21 status=incomplete",
        ):
            require_document_entity_readiness(
                document_version_id=21,
            )

        get_document_entity_coverage.return_value = self._coverage(
            safe=1,
        )
        report = require_document_entity_readiness(
            document_version_id=21,
        )
        self.assertTrue(report.ready_for_downstream_use)

    @staticmethod
    def _coverage(
            *,
            safe: int = 0,
            unassigned: int = 0,
            blocked: int = 0,
            invalid: int = 0,
    ) -> DocumentEntityCoverageReport:
        counts = {
            DocumentEntityCoverageStatus.SAFE_RESOLVED: safe,
            DocumentEntityCoverageStatus.UNASSIGNED: unassigned,
            DocumentEntityCoverageStatus.BLOCKED: blocked,
            DocumentEntityCoverageStatus.INVALID_PROVENANCE: invalid,
        }
        return DocumentEntityCoverageReport(
            document_version_id=21,
            document_id=20,
            version_number=2,
            candidate_count=sum(counts.values()),
            counts_by_status=tuple(
                DocumentEntityCoverageCount(
                    status=status,
                    count=counts[status],
                )
                for status in DocumentEntityCoverageStatus
            ),
            items=(),
        )
