import unittest
from unittest.mock import patch

from sqlalchemy import func, select

from argus.analysis_runs import AnalysisRunStatus
from argus.knowledge import AliasDecisionStatus, EntityType
from argus.models import AnalysisRun
from argus.services.analysis_run_service import prepare_analysis_run
from argus.services.software_provenance_service import SoftwareProvenance
from tests.test_document_analysis_input_service import (
    DocumentAnalysisInputServiceTests,
)


class AnalysisRunServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = DocumentAnalysisInputServiceTests(
            methodName="test_builds_complete_atomic_detached_input"
        )
        self.fixture.setUp()
        self.provenance_patcher = patch(
            "argus.services.analysis_run_service."
            "resolve_software_provenance",
            return_value=SoftwareProvenance(
                software_version="git:" + "a" * 40,
                kind="git",
                revision="a" * 40,
            ),
        )
        self.provenance_resolver = self.provenance_patcher.start()

    def tearDown(self) -> None:
        self.provenance_patcher.stop()
        self.fixture.tearDown()

    def test_prepares_complete_contract_and_is_idempotent(self) -> None:
        factory_calls = 0

        def counting_factory():
            nonlocal factory_calls
            factory_calls += 1
            return self.fixture.session_factory()

        first = prepare_analysis_run(
            document_version_id=self.fixture.version.id,
            entity_type=EntityType.ORGANIZATION,
            analysis_method="rhetoric-signals",
            analysis_method_version="1.2.0",
            configuration={
                "threshold": 0.75,
                "features": ["pronouns", "modality"],
            },
            session_factory=counting_factory,
        )
        second = prepare_analysis_run(
            document_version_id=self.fixture.version.id,
            entity_type=EntityType.ORGANIZATION,
            analysis_method=" rhetoric-signals ",
            analysis_method_version=" 1.2.0 ",
            configuration={
                "features": ["pronouns", "modality"],
                "threshold": 0.75,
            },
            session_factory=counting_factory,
        )

        self.assertEqual(factory_calls, 2)
        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(second.analysis_run_id, first.analysis_run_id)
        self.assertEqual(first.status, AnalysisRunStatus.PREPARED)
        self.assertEqual(first.entity_type_scope, "organization")
        self.assertEqual(first.candidate_count, 2)
        self.assertEqual(first.resolved_entity_count, 1)
        self.assertEqual(first.resolved_occurrence_count, 2)
        self.assertEqual(first.software_version, "git:" + "a" * 40)
        self.assertEqual(len(first.input_fingerprint), 64)
        self.assertEqual(len(first.configuration_hash), 64)

        row = self.fixture.session.get(
            AnalysisRun,
            first.analysis_run_id,
        )
        self.assertIsNotNone(row)
        self.assertEqual(
            row.input_manifest["schema_version"],
            "document-analysis-input@2",
        )
        self.assertEqual(
            row.input_manifest["text"]["content_hash"],
            self.fixture.text_artifact.content_hash,
        )
        self.assertNotIn("text", row.input_manifest["text"])
        entity = row.input_manifest["entities"][0]
        self.assertEqual(entity["entity_id"], self.fixture.entity.entity_id)
        self.assertEqual(len(entity["occurrences"]), 2)
        self.assertEqual(
            entity["active_alias_resolutions"][0][
                "latest_alias_decision_id"
            ],
            self.fixture.approval.id,
        )
        count = self.fixture.session.scalar(
            select(func.count()).select_from(AnalysisRun)
        )
        self.assertEqual(count, 1)

    def test_configuration_change_creates_distinct_run(self) -> None:
        first = self._prepare(configuration={"threshold": 0.5})
        second = self._prepare(configuration={"threshold": 0.8})

        self.assertNotEqual(
            first.analysis_run_id,
            second.analysis_run_id,
        )
        self.assertEqual(
            first.input_fingerprint,
            second.input_fingerprint,
        )
        self.assertNotEqual(
            first.configuration_hash,
            second.configuration_hash,
        )

    def test_verified_software_change_creates_distinct_run(self) -> None:
        first = self._prepare()
        self.provenance_resolver.return_value = SoftwareProvenance(
            software_version="git:" + "b" * 40,
            kind="git",
            revision="b" * 40,
        )

        second = self._prepare()

        self.assertNotEqual(
            first.analysis_run_id,
            second.analysis_run_id,
        )
        self.assertEqual(
            first.input_fingerprint,
            second.input_fingerprint,
        )
        self.assertNotEqual(
            first.software_version,
            second.software_version,
        )

    def test_rejects_non_json_or_ambiguous_configuration(self) -> None:
        for configuration, message in (
            ({1: "value"}, "keys must be strings"),
            ({"threshold": float("nan")}, "finite JSON values"),
            ({"invalid": object()}, "finite JSON values"),
        ):
            with self.subTest(configuration=configuration):
                with self.assertRaisesRegex(ValueError, message):
                    self._prepare(configuration=configuration)

        count = self.fixture.session.scalar(
            select(func.count()).select_from(AnalysisRun)
        )
        self.assertEqual(count, 0)

    def test_not_ready_bundle_does_not_persist_run(self) -> None:
        self.fixture._decide(
            self.fixture.proposal,
            AliasDecisionStatus.NEEDS_REVIEW,
        )
        self.fixture.session.commit()

        with self.assertRaisesRegex(ValueError, "status=blocked"):
            self._prepare()

        count = self.fixture.session.scalar(
            select(func.count()).select_from(AnalysisRun)
        )
        self.assertEqual(count, 0)

    def test_unverified_software_does_not_persist_run(self) -> None:
        with patch(
            "argus.services.analysis_run_service."
            "resolve_software_provenance",
            side_effect=ValueError("dirty Git worktree"),
        ):
            with self.assertRaisesRegex(
                ValueError,
                "dirty Git worktree",
            ):
                self._prepare()

        count = self.fixture.session.scalar(
            select(func.count()).select_from(AnalysisRun)
        )
        self.assertEqual(count, 0)

    def test_conflicting_stored_manifest_fails_closed(self) -> None:
        prepared = self._prepare()
        row = self.fixture.session.get(
            AnalysisRun,
            prepared.analysis_run_id,
        )
        row.input_manifest = {"corrupted": True}
        self.fixture.session.commit()

        with self.assertRaisesRegex(
            ValueError,
            "conflicts with its reproducible key",
        ):
            self._prepare()

    def _prepare(
            self,
            *,
            configuration=None,
    ):
        return prepare_analysis_run(
            document_version_id=self.fixture.version.id,
            entity_type=EntityType.ORGANIZATION,
            analysis_method="rhetoric-signals",
            analysis_method_version="1.2.0",
            configuration=configuration,
            session_factory=self.fixture.session_factory,
        )
