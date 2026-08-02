from hashlib import sha256
import json
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from argus.analysis.methods import (
    AnalysisMethodOutput,
    AnalysisMethodRegistry,
)
from argus.analysis_runs import AnalysisAttemptStatus, AnalysisRunStatus
from argus.knowledge import EntityType
from argus.models import (
    AnalysisExecutionAttempt,
    AnalysisResult,
    AnalysisRun,
)
from argus.services.analysis_execution_service import (
    AnalysisExecutionFailed,
    execute_analysis_run,
    get_analysis_attempt_history,
    get_analysis_run_result,
    recover_stale_analysis_run,
)
from argus.services.analysis_run_service import prepare_analysis_run
from argus.services.software_provenance_service import SoftwareProvenance
from argus.storage.analysis_run_repository import AnalysisRunRepository
from tests.test_document_analysis_input_service import (
    DocumentAnalysisInputServiceTests,
)


class StubMethod:
    name = "test-method"
    version = "1.0"

    def __init__(self) -> None:
        self.calls = 0
        self.error: Exception | None = None

    def validate(self, *, input_manifest, configuration) -> None:
        return None

    def execute(self, *, text, input_manifest, configuration):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return AnalysisMethodOutput(
            result_schema_version="test-result@1",
            payload={
                "text_length": len(text),
                "entity_count": len(input_manifest["entities"]),
                "option": configuration.get("option"),
            },
            warnings=("Test warning.",),
        )


class AnalysisExecutionServiceTests(unittest.TestCase):
    software_version = "git:" + "a" * 40

    def setUp(self) -> None:
        self.fixture = DocumentAnalysisInputServiceTests(
            methodName="test_builds_complete_atomic_detached_input"
        )
        self.fixture.setUp()
        self._repair_artifact_hash_chain()
        self.provenance = SoftwareProvenance(
            software_version=self.software_version,
            kind="git",
            revision="a" * 40,
        )
        self.prepare_patcher = patch(
            "argus.services.analysis_run_service."
            "resolve_software_provenance",
            return_value=self.provenance,
        )
        self.execute_patcher = patch(
            "argus.services.analysis_execution_service."
            "resolve_software_provenance",
            return_value=self.provenance,
        )
        self.prepare_patcher.start()
        self.execute_patcher.start()
        self.method = StubMethod()
        self.registry = AnalysisMethodRegistry((self.method,))
        self.prepared = prepare_analysis_run(
            document_version_id=self.fixture.version.id,
            entity_type=EntityType.ORGANIZATION,
            analysis_method=self.method.name,
            analysis_method_version=self.method.version,
            configuration={"option": "value"},
            registry=self.registry,
            session_factory=self.fixture.session_factory,
        )

    def tearDown(self) -> None:
        self.execute_patcher.stop()
        self.prepare_patcher.stop()
        self.fixture.tearDown()

    def test_executes_once_and_reuses_verified_completed_result(self) -> None:
        first = self._execute()
        second = self._execute()

        self.assertTrue(first.executed)
        self.assertFalse(second.executed)
        self.assertEqual(second.analysis_result_id, first.analysis_result_id)
        self.assertEqual(first.status, AnalysisRunStatus.COMPLETED)
        self.assertEqual(first.attempt_count, 1)
        self.assertEqual(first.warning_count, 1)
        self.assertEqual(len(first.output_hash), 64)
        self.assertEqual(self.method.calls, 1)

        history = get_analysis_attempt_history(
            analysis_run_id=first.analysis_run_id,
            session_factory=self.fixture.session_factory,
        )
        self.assertEqual(history.attempt_count, 1)
        self.assertEqual(
            history.attempts[0].status,
            AnalysisAttemptStatus.COMPLETED,
        )

        view = get_analysis_run_result(
            analysis_run_id=first.analysis_run_id,
            session_factory=self.fixture.session_factory,
        )
        self.assertEqual(view.analysis_result_id, first.analysis_result_id)
        self.assertEqual(view.payload["option"], "value")
        self.assertEqual(view.warnings, ("Test warning.",))

        session = self.fixture.session_factory()
        try:
            run = session.get(AnalysisRun, first.analysis_run_id)
            result = session.get(AnalysisResult, first.analysis_result_id)
            self.assertEqual(run.status, AnalysisRunStatus.COMPLETED)
            self.assertIsNotNone(run.started_at)
            self.assertIsNotNone(run.finished_at)
            self.assertIsNone(run.last_error)
            self.assertEqual(result.payload["option"], "value")
            count = session.scalar(
                select(func.count()).select_from(AnalysisResult)
            )
            self.assertEqual(count, 1)
            attempt = session.scalar(select(AnalysisExecutionAttempt))
            self.assertEqual(
                attempt.status,
                AnalysisAttemptStatus.COMPLETED,
            )
            self.assertFalse(attempt.migrated)
        finally:
            session.close()

    def test_failure_is_audited_and_requires_explicit_retry(self) -> None:
        self.method.error = RuntimeError("model unavailable")

        with self.assertRaisesRegex(
            AnalysisExecutionFailed,
            "model unavailable",
        ):
            self._execute()

        session = self.fixture.session_factory()
        try:
            run = session.get(AnalysisRun, self.prepared.analysis_run_id)
            self.assertEqual(run.status, AnalysisRunStatus.FAILED)
            self.assertEqual(run.attempt_count, 1)
            self.assertIn("RuntimeError: model unavailable", run.last_error)
            self.assertIsNotNone(run.finished_at)
        finally:
            session.close()

        with self.assertRaisesRegex(ValueError, "retry_failed=True"):
            self._execute()

        self.method.error = None
        result = self._execute(retry_failed=True)
        self.assertTrue(result.executed)
        self.assertEqual(result.status, AnalysisRunStatus.COMPLETED)
        self.assertEqual(result.attempt_count, 2)
        self.assertEqual(self.method.calls, 2)

        session = self.fixture.session_factory()
        try:
            attempts = list(session.scalars(
                select(AnalysisExecutionAttempt).order_by(
                    AnalysisExecutionAttempt.attempt_number
                )
            ))
            self.assertEqual(
                [attempt.status for attempt in attempts],
                [
                    AnalysisAttemptStatus.FAILED,
                    AnalysisAttemptStatus.COMPLETED,
                ],
            )
            self.assertIn("model unavailable", attempts[0].error)
        finally:
            session.close()

    def test_stale_running_attempt_is_explicitly_abandoned(self) -> None:
        started_at = datetime(2026, 7, 31, 10, tzinfo=timezone.utc)
        session = self.fixture.session_factory()
        try:
            run = session.get(AnalysisRun, self.prepared.analysis_run_id)
            repository = AnalysisRunRepository(session)
            self.assertTrue(repository.claim_execution(
                run,
                started_at=started_at,
                retry_failed=False,
            ))
            session.commit()
        finally:
            session.close()

        with self.assertRaisesRegex(ValueError, "not stale"):
            recover_stale_analysis_run(
                analysis_run_id=self.prepared.analysis_run_id,
                stale_after_minutes=60,
                operator="Victor",
                reason="Worker process terminated unexpectedly.",
                now=started_at + timedelta(minutes=59),
                session_factory=self.fixture.session_factory,
            )

        recovered = recover_stale_analysis_run(
            analysis_run_id=self.prepared.analysis_run_id,
            stale_after_minutes=60,
            operator="Victor",
            reason="Worker process terminated unexpectedly.",
            now=started_at + timedelta(minutes=61),
            session_factory=self.fixture.session_factory,
        )
        self.assertEqual(recovered.status, AnalysisRunStatus.FAILED)
        self.assertEqual(recovered.attempt_number, 1)

        session = self.fixture.session_factory()
        try:
            run = session.get(AnalysisRun, self.prepared.analysis_run_id)
            attempt = session.scalar(select(AnalysisExecutionAttempt))
            self.assertEqual(run.status, AnalysisRunStatus.FAILED)
            self.assertEqual(
                attempt.status,
                AnalysisAttemptStatus.ABANDONED,
            )
            self.assertEqual(attempt.recovery_operator, "Victor")
            self.assertEqual(
                attempt.recovery_reason,
                "Worker process terminated unexpectedly.",
            )
        finally:
            session.close()

        completed = self._execute(retry_failed=True)
        self.assertEqual(completed.attempt_count, 2)

    def test_unregistered_method_does_not_claim_run(self) -> None:
        with self.assertRaisesRegex(ValueError, "not registered"):
            execute_analysis_run(
                analysis_run_id=self.prepared.analysis_run_id,
                registry=AnalysisMethodRegistry(),
                session_factory=self.fixture.session_factory,
            )
        self._assert_run_is_prepared()

    def test_software_mismatch_does_not_claim_run(self) -> None:
        with patch(
            "argus.services.analysis_execution_service."
            "resolve_software_provenance",
            return_value=SoftwareProvenance(
                software_version="git:" + "b" * 40,
                kind="git",
                revision="b" * 40,
            ),
        ):
            with self.assertRaisesRegex(ValueError, "does not match"):
                self._execute()
        self._assert_run_is_prepared()

    def test_corrupt_input_manifest_does_not_claim_run(self) -> None:
        session = self.fixture.session_factory()
        try:
            run = session.get(AnalysisRun, self.prepared.analysis_run_id)
            run.input_manifest = {"corrupt": True}
            session.commit()
        finally:
            session.close()

        with self.assertRaisesRegex(ValueError, "schema is inconsistent"):
            self._execute()
        self._assert_run_is_prepared()

    def test_corrupt_completed_result_fails_closed(self) -> None:
        result = self._execute()
        session = self.fixture.session_factory()
        try:
            row = session.get(AnalysisResult, result.analysis_result_id)
            row.payload = {"corrupt": True}
            session.commit()
        finally:
            session.close()

        with self.assertRaisesRegex(ValueError, "hash is inconsistent"):
            self._execute()
        self.assertEqual(self.method.calls, 1)

    def test_result_view_requires_completed_run(self) -> None:
        with self.assertRaisesRegex(ValueError, "status is prepared"):
            get_analysis_run_result(
                analysis_run_id=self.prepared.analysis_run_id,
                session_factory=self.fixture.session_factory,
            )

    def _execute(self, *, retry_failed: bool = False):
        return execute_analysis_run(
            analysis_run_id=self.prepared.analysis_run_id,
            retry_failed=retry_failed,
            registry=self.registry,
            session_factory=self.fixture.session_factory,
        )

    def _assert_run_is_prepared(self) -> None:
        session = self.fixture.session_factory()
        try:
            run = session.get(AnalysisRun, self.prepared.analysis_run_id)
            self.assertEqual(run.status, AnalysisRunStatus.PREPARED)
            self.assertEqual(run.attempt_count, 0)
        finally:
            session.close()

    def _repair_artifact_hash_chain(self) -> None:
        self.fixture.text_artifact.content_hash = self._hash(
            self.fixture.text_artifact.payload
        )
        self.fixture.mention_artifact.payload = {
            **self.fixture.mention_artifact.payload,
            "input_content_hash": self.fixture.text_artifact.content_hash,
        }
        self.fixture.mention_artifact.content_hash = self._hash(
            self.fixture.mention_artifact.payload
        )
        self.fixture.candidate_artifact.payload = {
            **self.fixture.candidate_artifact.payload,
            "input_content_hash": self.fixture.mention_artifact.content_hash,
        }
        self.fixture.candidate_artifact.content_hash = self._hash(
            self.fixture.candidate_artifact.payload
        )
        self.fixture.session.commit()

    @staticmethod
    def _hash(payload: dict[str, object]) -> str:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return sha256(encoded).hexdigest()
