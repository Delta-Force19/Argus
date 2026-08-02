import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from typer.testing import CliRunner

from argus.interface.cli import app
from argus.services.acquisition_batch_runner import (
    AcquisitionBatchItemResult,
    AcquisitionBatchItemStatus,
    AcquisitionBatchReport,
)
from argus.services.acquisition_diagnostics import AcquisitionStage
from argus.services.acquisition_status_service import (
    AcquisitionStatusReport,
)
from argus.services.entity_mention_batch_runner import (
    EntityMentionBatchItemResult,
    EntityMentionBatchItemStatus,
    EntityMentionBatchReport,
)
from argus.services.entity_mention_audit_service import (
    EntityMentionAuditReport,
    FrequentMention,
    MentionCount,
    MentionExample,
    MentionRunSummary,
)
from argus.services.entity_candidate_batch_runner import (
    EntityCandidateBatchItemResult,
    EntityCandidateBatchItemStatus,
    EntityCandidateBatchReport,
)
from argus.services.entity_candidate_audit_service import (
    AliasSignal,
    CandidateCount,
    CandidateExample,
    CandidateRunSummary,
    EntityCandidateAuditReport,
    FrequentCandidate,
)
from argus.services.alias_proposal_batch_runner import (
    AliasProposalBatchItemResult,
    AliasProposalBatchItemStatus,
    AliasProposalBatchReport,
)
from argus.services.alias_proposal_audit_service import (
    AliasProposalAuditReport,
    ProposalCount,
    ProposalExample,
    ProposalRunSummary,
)
from argus.services.alias_review_service import (
    AliasReviewQueueItem,
    AliasReviewQueueReport,
    RecordedAliasDecision,
)
from argus.services.entity_resolution_service import (
    EntityResolutionResult,
)
from argus.services.candidate_resolution_service import (
    CandidateResolutionResult,
)
from argus.services.candidate_resolution_queue_service import (
    CandidateResolutionContext,
    CandidateResolutionQueue,
    CandidateResolutionQueueGroup,
    ExactCanonicalScopeState,
)
from argus.services.entity_registry_audit_service import (
    EntityRegistryAuditItem,
    EntityRegistryAuditReport,
    EntityResolutionValidity,
    ResolutionValidityCount,
)
from argus.services.safe_entity_projection_service import (
    ActiveEntityResolution,
    SafeEntity,
    SafeEntityCandidate,
    SafeEntityProjection,
)
from argus.services.document_entity_projection_service import (
    DocumentEntityProjection,
    DocumentResolvedEntity,
    ResolvedEntityOccurrence,
)
from argus.services.document_entity_coverage_service import (
    DocumentEntityCoverageCount,
    DocumentEntityCoverageItem,
    DocumentEntityCoverageReport,
    DocumentEntityCoverageStatus,
)
from argus.services.document_entity_readiness_service import (
    DocumentEntityReadinessReport,
    DocumentEntityReadinessStatus,
)
from argus.services.corpus_entity_readiness_service import (
    CorpusEntityReadinessCount,
    CorpusEntityReadinessReport,
)
from argus.services.ready_document_selector_service import (
    ReadyDocumentSelection,
    ReadyDocumentVersion,
)
from argus.services.document_analysis_input_service import (
    AnalysisInputDocument,
    AnalysisInputText,
    DocumentAnalysisInputBundle,
)
from argus.analysis_runs import AnalysisAttemptStatus, AnalysisRunStatus
from argus.services.analysis_run_service import PreparedAnalysisRun
from argus.services.analysis_execution_service import (
    AnalysisAttemptHistory,
    AnalysisAttemptView,
    AnalysisRunResultView,
    ExecutedAnalysisRun,
    RecoveredAnalysisRun,
)
from argus.documents import DerivedArtifactType, DocumentType
from argus.services.latest_news_service import (
    LatestNewsItem,
    LatestNewsReport,
)
from argus.knowledge import (
    AliasDecisionStatus,
    AliasSignalType,
    CandidateResolutionScope,
    CandidateResolutionStatus,
    EntityType,
    ManualCandidateResolutionDecision,
)
from argus.services.operational_pipeline_service import (
    OperationalPipelineReport,
)


runner = CliRunner()


class CLITests(unittest.TestCase):
    @patch("argus.interface.cli.get_analysis_attempt_history")
    def test_analysis_attempts_prints_ordered_audit(
            self,
            get_analysis_attempt_history,
    ) -> None:
        get_analysis_attempt_history.return_value = AnalysisAttemptHistory(
            analysis_run_id=92,
            status=AnalysisRunStatus.FAILED,
            attempt_count=1,
            attempts=(AnalysisAttemptView(
                attempt_number=1,
                status=AnalysisAttemptStatus.ABANDONED,
                started_at=datetime(2026, 7, 31, 10),
                finished_at=datetime(2026, 7, 31, 12),
                error="Abandoned by Victor: Worker terminated.",
                recovery_operator="Victor",
                recovery_reason="Worker terminated.",
                migrated=False,
            ),),
        )

        result = runner.invoke(
            app,
            ["analysis-attempts", "--analysis-run-id", "92"],
        )

        self.assertEqual(result.exit_code, 0)
        self.assertIn(
            "analysis_run_id=92 status=failed attempts=1 shown=1",
            result.stdout,
        )
        self.assertIn("attempt=1 status=abandoned", result.stdout)

    @patch("argus.interface.cli.recover_stale_analysis_run")
    def test_recover_analysis_prints_abandoned_attempt(
            self,
            recover_stale_analysis_run,
    ) -> None:
        recover_stale_analysis_run.return_value = RecoveredAnalysisRun(
            analysis_run_id=92,
            attempt_number=1,
            status=AnalysisRunStatus.FAILED,
            operator="Victor",
            reason="Worker terminated.",
            started_at=datetime(2026, 7, 31, 10),
            recovered_at=datetime(2026, 7, 31, 12),
        )

        result = runner.invoke(
            app,
            [
                "recover-analysis",
                "--analysis-run-id",
                "92",
                "--stale-after-minutes",
                "60",
                "--operator",
                "Victor",
                "--reason",
                "Worker terminated.",
            ],
        )

        self.assertEqual(result.exit_code, 0)
        recover_stale_analysis_run.assert_called_once_with(
            analysis_run_id=92,
            stale_after_minutes=60,
            operator="Victor",
            reason="Worker terminated.",
        )
        self.assertIn(
            "analysis_run_id=92 attempt=1 status=failed recovered=true",
            result.stdout,
        )

    @patch("argus.interface.cli.get_analysis_run_result")
    def test_analysis_result_prints_hash_verified_payload(
            self,
            get_analysis_run_result,
    ) -> None:
        get_analysis_run_result.return_value = AnalysisRunResultView(
            analysis_run_id=92,
            analysis_result_id=17,
            status=AnalysisRunStatus.COMPLETED,
            attempt_count=1,
            analysis_method="lexical-discourse",
            analysis_method_version="lexical-en-v0.1",
            software_version="git:" + "a" * 40,
            result_schema_version="lexical-discourse-result@1",
            output_hash="b" * 64,
            payload={"metrics": {"word_count": 4}},
            warnings=("Input limitation.",),
        )

        result = runner.invoke(
            app,
            ["analysis-result", "--analysis-run-id", "92"],
        )

        self.assertEqual(result.exit_code, 0)
        get_analysis_run_result.assert_called_once_with(
            analysis_run_id=92,
        )
        self.assertIn(
            'payload={"metrics":{"word_count":4}}',
            result.stdout,
        )
        self.assertIn("warning='Input limitation.'", result.stdout)

    @patch("argus.interface.cli.execute_analysis_run")
    def test_execute_analysis_prints_immutable_result(
            self,
            execute_analysis_run,
    ) -> None:
        execute_analysis_run.return_value = ExecutedAnalysisRun(
            analysis_run_id=92,
            analysis_result_id=17,
            executed=True,
            status=AnalysisRunStatus.COMPLETED,
            attempt_count=1,
            analysis_method="lexical-discourse",
            analysis_method_version="lexical-en-v0.1",
            software_version="git:" + "a" * 40,
            result_schema_version="lexical-discourse-result@1",
            output_hash="b" * 64,
            warning_count=0,
        )

        result = runner.invoke(
            app,
            ["execute-analysis", "--analysis-run-id", "92"],
        )

        self.assertEqual(result.exit_code, 0)
        execute_analysis_run.assert_called_once_with(
            analysis_run_id=92,
            retry_failed=False,
        )
        self.assertIn(
            "analysis_run_id=92 analysis_result_id=17 "
            "executed=true status=completed attempts=1",
            result.stdout,
        )
        self.assertIn(
            f"result_schema=lexical-discourse-result@1 "
            f"output_hash={'b' * 64} warnings=0",
            result.stdout,
        )

    @patch("argus.interface.cli.execute_analysis_run")
    def test_execute_analysis_passes_explicit_retry(
            self,
            execute_analysis_run,
    ) -> None:
        execute_analysis_run.return_value = ExecutedAnalysisRun(
            analysis_run_id=92,
            analysis_result_id=17,
            executed=False,
            status=AnalysisRunStatus.COMPLETED,
            attempt_count=2,
            analysis_method="lexical-discourse",
            analysis_method_version="lexical-en-v0.1",
            software_version="git:" + "a" * 40,
            result_schema_version="lexical-discourse-result@1",
            output_hash="b" * 64,
            warning_count=1,
        )

        result = runner.invoke(
            app,
            [
                "execute-analysis",
                "--analysis-run-id",
                "92",
                "--retry-failed",
            ],
        )

        self.assertEqual(result.exit_code, 0)
        execute_analysis_run.assert_called_once_with(
            analysis_run_id=92,
            retry_failed=True,
        )

    @patch("argus.interface.cli.prepare_analysis_run")
    def test_prepare_analysis_persists_reproducible_contract(
            self,
            prepare_analysis_run,
    ) -> None:
        prepare_analysis_run.return_value = PreparedAnalysisRun(
            analysis_run_id=91,
            created=True,
            status=AnalysisRunStatus.PREPARED,
            document_version_id=21,
            entity_type_scope="organization",
            analysis_method="rhetoric-signals",
            analysis_method_version="1.2.0",
            software_version="git:" + "a" * 40,
            configuration={"threshold": 0.75},
            configuration_hash="a" * 64,
            input_schema_version="document-analysis-input@1",
            input_fingerprint="b" * 64,
            candidate_count=2,
            resolved_entity_count=1,
            resolved_occurrence_count=2,
        )

        result = runner.invoke(
            app,
            [
                "prepare-analysis",
                "--document-version-id",
                "21",
                "--type",
                "organization",
                "--method",
                "rhetoric-signals",
                "--method-version",
                "1.2.0",
                "--configuration-json",
                '{"threshold": 0.75}',
            ],
        )

        self.assertEqual(result.exit_code, 0)
        prepare_analysis_run.assert_called_once_with(
            document_version_id=21,
            analysis_method="rhetoric-signals",
            analysis_method_version="1.2.0",
            configuration={"threshold": 0.75},
            entity_type=EntityType.ORGANIZATION,
        )
        self.assertIn(
            "analysis_run_id=91 created=true status=prepared "
            "document_version_id=21 type=organization",
            result.stdout,
        )
        self.assertIn(
            f"input_fingerprint={'b' * 64}",
            result.stdout,
        )

    def test_prepare_analysis_rejects_non_object_configuration(
            self,
    ) -> None:
        result = runner.invoke(
            app,
            [
                "prepare-analysis",
                "--document-version-id",
                "21",
                "--method",
                "rhetoric-signals",
                "--method-version",
                "1",
                "--configuration-json",
                "[]",
            ],
        )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn(
            "configuration-json must contain one JSON object",
            result.output,
        )

    @patch("argus.interface.cli.prepare_analysis_run")
    def test_prepare_analysis_rejects_manual_software_version(
            self,
            prepare_analysis_run,
    ) -> None:
        result = runner.invoke(
            app,
            [
                "prepare-analysis",
                "--document-version-id",
                "21",
                "--method",
                "rhetoric-signals",
                "--method-version",
                "1",
                "--software-version",
                "spoofed",
            ],
        )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("No such option", result.output)
        prepare_analysis_run.assert_not_called()

    @patch("argus.interface.cli.resolve_candidate_identity")
    def test_resolve_candidate_records_explicit_scoped_decision(
            self,
            resolve_candidate_identity,
    ) -> None:
        resolve_candidate_identity.return_value = (
            CandidateResolutionResult(
                decision_id=81,
                revision=1,
                supersedes_decision_id=None,
                status=CandidateResolutionStatus.ASSIGNED,
                scope=CandidateResolutionScope.EXACT_CANONICAL,
                seed_entity_candidate_id=11,
                entity_id=61,
                entity_type="organization",
                canonical_name="un",
                entity_created=True,
                matched_candidate_ids=(11, 15, 22),
                newly_assigned_candidate_ids=(11, 15, 22),
            )
        )

        result = runner.invoke(
            app,
            [
                "resolve-candidate",
                "--candidate-id",
                "11",
                "--status",
                "assigned",
                "--scope",
                "exact_canonical",
                "--reason",
                "Reviewed exact normalized form.",
                "--reviewer",
                "Victor",
            ],
        )

        self.assertEqual(result.exit_code, 0)
        resolve_candidate_identity.assert_called_once_with(
            candidate_id=11,
            entity_id=None,
            decision=ManualCandidateResolutionDecision(
                status=CandidateResolutionStatus.ASSIGNED,
                scope=CandidateResolutionScope.EXACT_CANONICAL,
                reason="Reviewed exact normalized form.",
                reviewer="Victor",
            ),
        )
        self.assertIn(
            "decision_id=81 revision=1 supersedes=None "
            "status=assigned scope=exact_canonical "
            "seed_candidate_id=11 entity_id=61 "
            "entity_created=true type=organization "
            "canonical_name='un' matched_candidate_ids=11,15,22 "
            "newly_assigned_candidate_ids=11,15,22",
            result.stdout,
        )

    @patch("argus.interface.cli.resolve_candidate_identity")
    def test_resolve_candidate_records_not_entity_without_entity(
            self,
            resolve_candidate_identity,
    ) -> None:
        resolve_candidate_identity.return_value = CandidateResolutionResult(
            decision_id=82,
            revision=1,
            supersedes_decision_id=None,
            status=CandidateResolutionStatus.NOT_ENTITY,
            scope=CandidateResolutionScope.SINGLE,
            seed_entity_candidate_id=15,
            entity_id=None,
            entity_type="group",
            canonical_name="european",
            entity_created=False,
            matched_candidate_ids=(15,),
            newly_assigned_candidate_ids=(),
        )

        result = runner.invoke(
            app,
            [
                "resolve-candidate",
                "--candidate-id",
                "15",
                "--status",
                "not_entity",
                "--scope",
                "single",
                "--reason",
                "Adjectival modifier, not a group identity.",
                "--reviewer",
                "Victor",
            ],
        )

        self.assertEqual(result.exit_code, 0)
        resolve_candidate_identity.assert_called_once_with(
            candidate_id=15,
            entity_id=None,
            decision=ManualCandidateResolutionDecision(
                status=CandidateResolutionStatus.NOT_ENTITY,
                scope=CandidateResolutionScope.SINGLE,
                reason="Adjectival modifier, not a group identity.",
                reviewer="Victor",
            ),
        )
        self.assertIn(
            "status=not_entity scope=single seed_candidate_id=15 "
            "entity_id=none entity_created=false type=group "
            "canonical_name='european' matched_candidate_ids=15 "
            "newly_assigned_candidate_ids=none",
            result.stdout,
        )

    @patch("argus.interface.cli.get_candidate_resolution_queue")
    def test_candidate_resolution_queue_prints_actionable_groups(
            self,
            get_candidate_resolution_queue,
    ) -> None:
        get_candidate_resolution_queue.return_value = (
            CandidateResolutionQueue(
                document_version_id=21,
                document_id=20,
                version_number=2,
                title="UN article",
                language="en",
                identifier_value="https://example.test/article",
                readiness=DocumentEntityReadinessReport(
                    document_version_id=21,
                    document_id=20,
                    version_number=2,
                    entity_type=None,
                    status=DocumentEntityReadinessStatus.INCOMPLETE,
                    ready_for_downstream_use=False,
                    candidate_count=5,
                    safe_resolved_count=2,
                    unassigned_count=3,
                    blocked_count=0,
                    invalid_provenance_count=0,
                ),
                unresolved_group_count=1,
                shown_group_count=1,
                groups=(
                    CandidateResolutionQueueGroup(
                        entity_type=EntityType.ORGANIZATION,
                        canonical_text="un",
                        seed_entity_candidate_id=11,
                        document_candidate_count=2,
                        corpus_candidate_count=61,
                        corpus_unassigned_count=59,
                        corpus_invalid_provenance_count=0,
                        surface_variants=("UN",),
                        exact_scope_state=(
                            ExactCanonicalScopeState.EXTENDS_ENTITY
                        ),
                        assigned_entity_ids=(61,),
                        contexts=(
                            CandidateResolutionContext(
                                entity_candidate_id=11,
                                entity_mention_id=12,
                                surface_text="UN",
                                start_char=0,
                                end_char=2,
                                context_text="UN officials met.",
                            ),
                        ),
                    ),
                ),
            )
        )

        result = runner.invoke(
            app,
            [
                "candidate-resolution-queue",
                "--document-version-id",
                "21",
                "--limit",
                "10",
                "--contexts",
                "1",
            ],
        )

        self.assertEqual(result.exit_code, 0)
        get_candidate_resolution_queue.assert_called_once_with(
            document_version_id=21,
            limit=10,
            contexts_per_group=1,
            entity_type=None,
        )
        self.assertIn(
            "document_version_id=21 document_id=20 version=2 "
            "type=all status=incomplete candidates=5 "
            "safe_resolved=2 not_entity=0 unassigned=3 blocked=0 "
            "invalid_provenance=0 groups=1 shown=1",
            result.stdout,
        )
        self.assertIn(
            "group seed_candidate_id=11 type=organization "
            "canonical='un' document_candidates=2 "
            "corpus_candidates=61 corpus_unassigned=59 "
            "corpus_not_entity=0 "
            "corpus_invalid_provenance=0 "
            "exact_scope=extends_entity assigned_entity_ids=61",
            result.stdout,
        )
        self.assertIn(
            "context candidate_id=11 mention_id=12 span=0:2 "
            "surface='UN' text='UN officials met.'",
            result.stdout,
        )

    @patch("argus.interface.cli.get_document_analysis_input")
    def test_document_analysis_input_prints_atomic_bundle(
            self,
            get_document_analysis_input,
    ) -> None:
        readiness = DocumentEntityReadinessReport(
            document_version_id=21,
            document_id=20,
            version_number=2,
            entity_type=EntityType.ORGANIZATION,
            status=DocumentEntityReadinessStatus.READY,
            ready_for_downstream_use=True,
            candidate_count=2,
            safe_resolved_count=2,
            unassigned_count=0,
            blocked_count=0,
            invalid_provenance_count=0,
        )
        entities = DocumentEntityProjection(
            document_version_id=21,
            document_id=20,
            version_number=2,
            resolved_entity_count=1,
            resolved_occurrence_count=2,
            items=(
                DocumentResolvedEntity(
                    entity_id=31,
                    entity_type=EntityType.ORGANIZATION,
                    canonical_name="united nations",
                    canonical_entity_candidate_id=42,
                    occurrences=tuple(
                        ResolvedEntityOccurrence(
                            entity_candidate_id=40 + index,
                            entity_mention_id=50 + index,
                            derived_artifact_id=60,
                            canonical_text=canonical_text,
                            surface_text=surface_text,
                            normalized_text=surface_text.casefold(),
                            source_label="ORG",
                            start_char=start_char,
                            end_char=start_char + len(surface_text),
                            assigned_by_alias_decision_id=70,
                        )
                        for index, (
                            canonical_text,
                            surface_text,
                            start_char,
                        ) in enumerate(
                            (
                                ("un", "UN", 0),
                                (
                                    "united nations",
                                    "United Nations",
                                    11,
                                ),
                            )
                        )
                    ),
                    active_resolutions=(),
                ),
            ),
        )
        get_document_analysis_input.return_value = (
            DocumentAnalysisInputBundle(
                entity_type=EntityType.ORGANIZATION,
                document=AnalysisInputDocument(
                    document_id=20,
                    document_version_id=21,
                    version_number=2,
                    document_type=DocumentType.ARTICLE,
                    identifier_scheme="url",
                    identifier_value="https://example.test/article",
                    title="UN article",
                    language="en",
                    source_id=3,
                    raw_artifact_id=10,
                    raw_content_hash="a" * 64,
                    raw_hash_algorithm="sha256",
                    media_type="text/html",
                    published_at=None,
                ),
                text=AnalysisInputText(
                    derived_artifact_id=11,
                    artifact_type=DerivedArtifactType.EXTRACTED_TEXT,
                    method="trafilatura",
                    method_version="1",
                    schema_version="1",
                    content_hash="b" * 64,
                    text="UN and the United Nations.",
                    character_count=26,
                    quality_limitations=(),
                ),
                readiness=readiness,
                entities=entities,
            )
        )

        result = runner.invoke(
            app,
            [
                "document-analysis-input",
                "--document-version-id",
                "21",
                "--type",
                "organization",
            ],
        )

        self.assertEqual(result.exit_code, 0)
        get_document_analysis_input.assert_called_once_with(
            document_version_id=21,
            entity_type=EntityType.ORGANIZATION,
        )
        self.assertIn(
            "document_version_id=21 document_id=20 version=2 "
            "type=organization status=ready candidates=2 "
            "entities=1 occurrences=2 not_entity=0",
            result.stdout,
        )
        self.assertIn(
            "text_artifact_id=11 text_type=extracted_text",
            result.stdout,
        )
        self.assertIn(
            "entity entity_id=31 entity_type=organization "
            "canonical_name='united nations'",
            result.stdout,
        )

    @patch("argus.interface.cli.select_ready_document_versions")
    def test_ready_document_versions_prints_safe_selection(
            self,
            select_ready_document_versions,
    ) -> None:
        select_ready_document_versions.return_value = (
            ReadyDocumentSelection(
                entity_type=EntityType.ORGANIZATION,
                ready_document_version_count=3,
                selected_document_version_count=1,
                items=(
                    ReadyDocumentVersion(
                        document_version_id=21,
                        document_id=20,
                        version_number=2,
                        entity_type=EntityType.ORGANIZATION,
                        candidate_count=3,
                        safe_resolved_count=3,
                    ),
                ),
            )
        )

        result = runner.invoke(
            app,
            [
                "ready-document-versions",
                "--limit",
                "1",
                "--type",
                "organization",
            ],
        )

        self.assertEqual(result.exit_code, 0)
        select_ready_document_versions.assert_called_once_with(
            limit=1,
            entity_type=EntityType.ORGANIZATION,
        )
        self.assertIn(
            "ready_document_versions=3 selected=1 "
            "type=organization",
            result.stdout,
        )
        self.assertIn(
            "document document_version_id=21 document_id=20 version=2 "
            "candidates=3 safe_resolved=3 not_entity=0",
            result.stdout,
        )

    @patch("argus.interface.cli.get_corpus_entity_readiness")
    def test_corpus_entity_readiness_prints_complete_summary(
            self,
            get_corpus_entity_readiness,
    ) -> None:
        item = DocumentEntityReadinessReport(
            document_version_id=21,
            document_id=20,
            version_number=2,
            entity_type=EntityType.ORGANIZATION,
            status=DocumentEntityReadinessStatus.INCOMPLETE,
            ready_for_downstream_use=False,
            candidate_count=3,
            safe_resolved_count=2,
            unassigned_count=1,
            blocked_count=0,
            invalid_provenance_count=0,
        )
        get_corpus_entity_readiness.return_value = (
            CorpusEntityReadinessReport(
                entity_type=EntityType.ORGANIZATION,
                document_version_count=4,
                ready_document_version_count=1,
                unsafe_document_version_count=3,
                matched_document_version_count=2,
                candidate_count=9,
                safe_resolved_count=5,
                unassigned_count=2,
                blocked_count=1,
                invalid_provenance_count=1,
                counts_by_status=tuple(
                    CorpusEntityReadinessCount(
                        status=status,
                        count=1 if status.value != "no_candidates" else 0,
                    )
                    for status in DocumentEntityReadinessStatus
                ),
                items=(item,),
            )
        )

        result = runner.invoke(
            app,
            [
                "corpus-entity-readiness",
                "--limit",
                "1",
                "--status",
                "incomplete",
                "--type",
                "organization",
            ],
        )

        self.assertEqual(result.exit_code, 0)
        get_corpus_entity_readiness.assert_called_once_with(
            limit=1,
            status=DocumentEntityReadinessStatus.INCOMPLETE,
            entity_type=EntityType.ORGANIZATION,
        )
        self.assertIn(
            "document_versions=4 ready=1 unsafe=3 matched=2 shown=1 "
            "type=organization",
            result.stdout,
        )
        self.assertIn(
            "candidates=9 safe_resolved=5 not_entity=0 unassigned=2 blocked=1 "
            "invalid_provenance=1",
            result.stdout,
        )
        self.assertIn(
            "document document_version_id=21 document_id=20 version=2 "
            "status=incomplete ready=false candidates=3",
            result.stdout,
        )

    @patch("argus.interface.cli.get_document_entity_readiness")
    def test_document_entity_readiness_prints_enforceable_contract(
            self,
            get_document_entity_readiness,
    ) -> None:
        get_document_entity_readiness.return_value = (
            DocumentEntityReadinessReport(
                document_version_id=21,
                document_id=20,
                version_number=2,
                entity_type=EntityType.ORGANIZATION,
                status=DocumentEntityReadinessStatus.INCOMPLETE,
                ready_for_downstream_use=False,
                candidate_count=3,
                safe_resolved_count=2,
                unassigned_count=1,
                blocked_count=0,
                invalid_provenance_count=0,
            )
        )

        result = runner.invoke(
            app,
            [
                "document-entity-readiness",
                "--document-version-id",
                "21",
                "--type",
                "organization",
            ],
        )

        self.assertEqual(result.exit_code, 0)
        get_document_entity_readiness.assert_called_once_with(
            document_version_id=21,
            entity_type=EntityType.ORGANIZATION,
        )
        self.assertIn(
            "document_version_id=21 document_id=20 version=2 "
            "type=organization status=incomplete ready=false "
            "candidates=3 safe_resolved=2 not_entity=0 unassigned=1 blocked=0 "
            "invalid_provenance=0",
            result.stdout,
        )

    @patch("argus.interface.cli.run_telegram_news_bot")
    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    def test_telegram_bot_runs_public_polling_adapter(
            self,
            upgrade_database,
            configure_logging,
            run_telegram_news_bot,
    ) -> None:
        result = runner.invoke(
            app,
            [
                "telegram-bot",
                "--limit",
                "7",
                "--excerpt-chars",
                "400",
                "--timezone",
                "Europe/Amsterdam",
                "--poll-timeout",
                "20",
                "--once",
            ],
        )

        self.assertEqual(result.exit_code, 0)
        run_telegram_news_bot.assert_called_once_with(
            news_limit=7,
            excerpt_chars=400,
            output_timezone=ZoneInfo("Europe/Amsterdam"),
            poll_timeout_seconds=20,
            run_once=True,
            automatic_delivery=False,
            automatic_interval_seconds=3600,
            automatic_delivery_limit=20,
            automatic_parse_limit=20,
            latest_cooldown_seconds=10,
            delivery_state_path=Path(
                "data/telegram_delivery_state.json"
            ),
        )

    @patch("argus.interface.cli.run_telegram_news_bot")
    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    def test_telegram_bot_configures_automatic_delivery(
            self,
            upgrade_database,
            configure_logging,
            run_telegram_news_bot,
    ) -> None:
        result = runner.invoke(
            app,
            [
                "telegram-bot",
                "--auto-delivery",
                "--delivery-interval-minutes",
                "15",
                "--delivery-limit",
                "12",
                "--auto-parse-limit",
                "14",
                "--delivery-state-path",
                "data/test-delivery.json",
            ],
        )

        self.assertEqual(result.exit_code, 0)
        run_telegram_news_bot.assert_called_once_with(
            news_limit=10,
            excerpt_chars=500,
            output_timezone=ZoneInfo("UTC"),
            poll_timeout_seconds=30,
            run_once=False,
            automatic_delivery=True,
            automatic_interval_seconds=900,
            automatic_delivery_limit=12,
            automatic_parse_limit=14,
            latest_cooldown_seconds=10,
            delivery_state_path=Path("data/test-delivery.json"),
        )

    @patch("argus.interface.cli.run_telegram_news_bot")
    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    def test_telegram_bot_reports_missing_environment(
            self,
            upgrade_database,
            configure_logging,
            run_telegram_news_bot,
    ) -> None:
        run_telegram_news_bot.side_effect = ValueError(
            "ARGUS_TELEGRAM_BOT_TOKEN is required."
        )

        result = runner.invoke(app, ["telegram-bot", "--once"])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn(
            "ARGUS_TELEGRAM_BOT_TOKEN is required",
            result.output,
        )

    @patch("argus.interface.cli.get_latest_news")
    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    def test_latest_news_prints_reader_feed_in_requested_timezone(
            self,
            upgrade_database,
            configure_logging,
            get_latest_news,
    ) -> None:
        published_at = datetime(2026, 7, 28, 18, 30)
        fetched_at = datetime(2026, 7, 28, 18, 35)
        get_latest_news.return_value = LatestNewsReport(
            items=(
                LatestNewsItem(
                    article_id=7,
                    published_at=published_at,
                    fetched_at=fetched_at,
                    source="Example News",
                    title="First\nheadline",
                    url="https://example.test/story",
                    language="en",
                    parsing_status="done",
                    excerpt_source="content",
                    excerpt="First\nparagraph.",
                ),
                LatestNewsItem(
                    article_id=8,
                    published_at=None,
                    fetched_at=fetched_at,
                    source="unknown",
                    title="Second headline",
                    url="https://example.test/other",
                    language=None,
                    parsing_status="not_started",
                    excerpt_source=None,
                    excerpt=None,
                ),
            )
        )

        result = runner.invoke(
            app,
            [
                "latest-news",
                "--limit",
                "2",
                "--excerpt-chars",
                "120",
                "--timezone",
                "Europe/Amsterdam",
            ],
        )

        self.assertEqual(result.exit_code, 0)
        upgrade_database.assert_called_once_with()
        configure_logging.assert_called_once_with()
        get_latest_news.assert_called_once_with(
            limit=2,
            excerpt_chars=120,
        )
        self.assertIn(
            "shown=2 content=1 summary=0 headline_only=1 "
            "timezone=Europe/Amsterdam",
            result.stdout,
        )
        self.assertIn(
            "1. [2026-07-28 20:30 CEST] Example News",
            result.stdout,
        )
        self.assertIn("First\nheadline", result.stdout)
        self.assertIn("First\nparagraph.", result.stdout)
        self.assertNotIn("article_id=7", result.stdout)
        self.assertIn(
            "2. [time unknown] unknown",
            result.stdout,
        )
        self.assertNotIn("excerpt=unavailable", result.stdout)

    @patch("argus.interface.cli.get_latest_news")
    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    def test_latest_news_details_preserve_diagnostics(
            self,
            upgrade_database,
            configure_logging,
            get_latest_news,
    ) -> None:
        get_latest_news.return_value = LatestNewsReport(
            items=(
                LatestNewsItem(
                    article_id=7,
                    published_at=datetime(2026, 7, 28, 18, 30),
                    fetched_at=datetime(2026, 7, 28, 18, 35),
                    source="Example News",
                    title="Headline",
                    url="https://example.test/story",
                    language="en",
                    parsing_status="done",
                    excerpt_source="content",
                    excerpt="Paragraph.",
                ),
            )
        )

        result = runner.invoke(
            app,
            ["latest-news", "--details"],
        )

        self.assertEqual(result.exit_code, 0)
        self.assertIn("article_id=7", result.stdout)
        self.assertIn("language=en", result.stdout)
        self.assertIn("parsing=done", result.stdout)
        self.assertIn("excerpt_source=content", result.stdout)

    @patch("argus.interface.cli.get_latest_news")
    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    def test_latest_news_rejects_unknown_timezone(
            self,
            upgrade_database,
            configure_logging,
            get_latest_news,
    ) -> None:
        get_latest_news.return_value = LatestNewsReport(items=())

        result = runner.invoke(
            app,
            ["latest-news", "--timezone", "Mars/Olympus"],
        )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Unknown IANA timezone", result.output)

    @patch("argus.interface.cli.parse_articles")
    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    def test_parse_can_prioritize_newest_articles(
            self,
            upgrade_database,
            configure_logging,
            parse_articles,
    ) -> None:
        result = runner.invoke(
            app,
            ["parse", "--limit", "12", "--newest"],
        )

        self.assertEqual(result.exit_code, 0)
        parse_articles.assert_called_once_with(
            limit=12,
            retry_failed=False,
            newest_first=True,
        )

    @patch("argus.interface.cli.collect_articles")
    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    def test_database_is_upgraded_before_command(
            self,
            upgrade_database,
            configure_logging,
            collect_articles,
    ):
        calls: list[str] = []

        upgrade_database.side_effect = (
            lambda: calls.append("upgrade")
        )
        configure_logging.side_effect = (
            lambda: calls.append("logging")
        )
        collect_articles.side_effect = (
            lambda: calls.append("collect")
        )

        result = runner.invoke(app, ["collect"])

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(
            calls,
            ["upgrade", "logging", "collect"],
        )

    @patch("argus.interface.cli.acquire_articles")
    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    def test_acquire_runs_new_pipeline_and_prints_report(
            self,
            upgrade_database,
            configure_logging,
            acquire_articles,
    ):
        acquire_articles.return_value = AcquisitionBatchReport(items=())

        result = runner.invoke(
            app,
            [
                "acquire",
                "--limit",
                "7",
                "--retry-unsuccessful",
            ],
        )

        self.assertEqual(result.exit_code, 0)
        upgrade_database.assert_called_once_with()
        configure_logging.assert_called_once_with()
        acquire_articles.assert_called_once_with(
            limit=7,
            retry_unsuccessful=True,
        )
        self.assertIn(
            "total=0 processed=0 retrieval_only=0 failed=0",
            result.stdout,
        )

    @patch("argus.interface.cli.acquire_articles")
    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    def test_acquire_prints_per_candidate_failure_diagnostics(
            self,
            upgrade_database,
            configure_logging,
            acquire_articles,
    ) -> None:
        acquire_articles.return_value = AcquisitionBatchReport(
            items=(
                AcquisitionBatchItemResult(
                    candidate_id=17,
                    url="https://example.com/broken",
                    status=AcquisitionBatchItemStatus.FAILED,
                    failure_stage=AcquisitionStage.PROCESSING,
                    error_type="ValueError",
                    error_message="No main text\ncould be extracted.",
                ),
            )
        )

        result = runner.invoke(app, ["acquire"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn(
            "candidate_id=17 status=failed stage=processing "
            "url=https://example.com/broken error_type=ValueError "
            "error=No main text could be extracted.",
            result.stdout,
        )

    @patch("argus.interface.cli.get_acquisition_status")
    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    def test_acquisition_status_prints_queue_and_paused_sources(
            self,
            upgrade_database,
            configure_logging,
            get_acquisition_status,
    ) -> None:
        get_acquisition_status.return_value = AcquisitionStatusReport(
            total=15,
            unattempted=5,
            succeeded=4,
            retryable=3,
            access_restricted=2,
            exhausted=1,
            paused_sources=("The Telegraph",),
        )

        result = runner.invoke(app, ["acquisition-status"])

        self.assertEqual(result.exit_code, 0)
        upgrade_database.assert_called_once_with()
        configure_logging.assert_called_once_with()
        get_acquisition_status.assert_called_once_with()
        self.assertIn(
            "total=15 unattempted=5 succeeded=4 retryable=3 "
            "access_restricted=2 exhausted=1",
            result.stdout,
        )
        self.assertIn(
            "paused_source=The Telegraph",
            result.stdout,
        )

    @patch("argus.interface.cli.run_entity_mention_pipeline")
    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    def test_extract_mentions_runs_bounded_pipeline_and_prints_report(
            self,
            upgrade_database,
            configure_logging,
            run_entity_mention_pipeline,
    ) -> None:
        run_entity_mention_pipeline.return_value = (
            EntityMentionBatchReport(
                items=(
                    EntityMentionBatchItemResult(
                        text_artifact_id=7,
                        status=EntityMentionBatchItemStatus.PROCESSED,
                        entity_artifact_id=11,
                        mention_count=4,
                    ),
                    EntityMentionBatchItemResult(
                        text_artifact_id=8,
                        status=EntityMentionBatchItemStatus.FAILED,
                        error_type="ValueError",
                        error_message="Unsupported\ninput.",
                    ),
                )
            )
        )

        result = runner.invoke(
            app,
            ["extract-mentions", "--limit", "7"],
        )

        self.assertEqual(result.exit_code, 0)
        upgrade_database.assert_called_once_with()
        configure_logging.assert_called_once_with()
        run_entity_mention_pipeline.assert_called_once_with(limit=7)
        self.assertIn(
            "total=2 processed=1 failed=1 mentions=4",
            result.stdout,
        )
        self.assertIn(
            "text_artifact_id=8 status=failed "
            "error_type=ValueError error=Unsupported input.",
            result.stdout,
        )

    @patch("argus.interface.cli.run_entity_candidate_pipeline")
    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    def test_generate_candidates_runs_bounded_pipeline_and_prints_report(
            self,
            upgrade_database,
            configure_logging,
            run_entity_candidate_pipeline,
    ) -> None:
        run_entity_candidate_pipeline.return_value = (
            EntityCandidateBatchReport(
                items=(
                    EntityCandidateBatchItemResult(
                        mention_artifact_id=11,
                        status=EntityCandidateBatchItemStatus.PROCESSED,
                        candidate_artifact_id=21,
                        candidate_count=4,
                        excluded_count=3,
                    ),
                    EntityCandidateBatchItemResult(
                        mention_artifact_id=12,
                        status=EntityCandidateBatchItemStatus.FAILED,
                        error_type="ValueError",
                        error_message="Invalid\nmention offsets.",
                    ),
                )
            )
        )

        result = runner.invoke(
            app,
            ["generate-candidates", "--limit", "7"],
        )

        self.assertEqual(result.exit_code, 0)
        upgrade_database.assert_called_once_with()
        configure_logging.assert_called_once_with()
        run_entity_candidate_pipeline.assert_called_once_with(limit=7)
        self.assertIn(
            "total=2 processed=1 failed=1 candidates=4 excluded=3",
            result.stdout,
        )
        self.assertIn(
            "mention_artifact_id=12 status=failed "
            "error_type=ValueError error=Invalid mention offsets.",
            result.stdout,
        )

    @patch("argus.interface.cli.run_alias_proposal_pipeline")
    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    def test_propose_aliases_runs_bounded_pipeline_and_prints_report(
            self,
            upgrade_database,
            configure_logging,
            run_alias_proposal_pipeline,
    ) -> None:
        run_alias_proposal_pipeline.return_value = (
            AliasProposalBatchReport(
                items=(
                    AliasProposalBatchItemResult(
                        candidate_artifact_id=21,
                        status=AliasProposalBatchItemStatus.PROCESSED,
                        proposal_artifact_id=31,
                        proposal_count=4,
                    ),
                    AliasProposalBatchItemResult(
                        candidate_artifact_id=22,
                        status=AliasProposalBatchItemStatus.FAILED,
                        error_type="ValueError",
                        error_message="Invalid\ncandidate provenance.",
                    ),
                )
            )
        )

        result = runner.invoke(
            app,
            ["propose-aliases", "--limit", "7"],
        )

        self.assertEqual(result.exit_code, 0)
        upgrade_database.assert_called_once_with()
        configure_logging.assert_called_once_with()
        run_alias_proposal_pipeline.assert_called_once_with(limit=7)
        self.assertIn(
            "total=2 processed=1 failed=1 proposals=4",
            result.stdout,
        )
        self.assertIn(
            "candidate_artifact_id=22 status=failed "
            "error_type=ValueError error=Invalid candidate provenance.",
            result.stdout,
        )

    @patch("argus.interface.cli.get_entity_candidate_audit")
    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    def test_candidate_audit_prints_read_only_quality_report(
            self,
            upgrade_database,
            configure_logging,
            get_entity_candidate_audit,
    ) -> None:
        get_entity_candidate_audit.return_value = (
            EntityCandidateAuditReport(
                candidate_count=4,
                artifact_count=2,
                document_version_count=2,
                counts_by_language=(CandidateCount("en", 4),),
                counts_by_type=(CandidateCount("person", 4),),
                frequent_candidates=(
                    FrequentCandidate(
                        entity_type=EntityType.PERSON,
                        canonical_text="alice smith",
                        candidate_count=4,
                        document_count=2,
                        surface_variants=("Alice Smith", "ALICE SMITH"),
                    ),
                ),
                densest_runs=(
                    CandidateRunSummary(
                        artifact_id=21,
                        input_artifact_id=11,
                        document_version_id=3,
                        language="en",
                        candidate_count=3,
                        unique_form_count=2,
                        method_version="1",
                        title="First\nstory",
                    ),
                ),
                alias_signals=(
                    AliasSignal(
                        entity_type=EntityType.PERSON,
                        left_text="alice smith",
                        right_text="smith",
                        reason="person_short_name",
                        left_count=2,
                        right_count=2,
                        shared_document_count=1,
                        left_context="Alice\nSmith spoke.",
                        right_context="Smith\nanswered.",
                    ),
                ),
                examples=(
                    CandidateExample(
                        candidate_id=31,
                        artifact_id=21,
                        mention_id=11,
                        document_version_id=3,
                        language="en",
                        entity_type=EntityType.PERSON,
                        surface_text="Alice\nSmith",
                        canonical_text="alice smith",
                        context_text="Alice\nSmith spoke.",
                        context_start_char=0,
                        context_end_char=18,
                    ),
                ),
            )
        )

        result = runner.invoke(
            app,
            [
                "candidate-audit",
                "--top",
                "5",
                "--examples",
                "7",
                "--pairs",
                "9",
            ],
        )

        self.assertEqual(result.exit_code, 0)
        upgrade_database.assert_called_once_with()
        configure_logging.assert_called_once_with()
        get_entity_candidate_audit.assert_called_once_with(
            top=5,
            examples=7,
            pairs=9,
        )
        self.assertIn(
            "candidates=4 artifacts=2 document_versions=2",
            result.stdout,
        )
        self.assertIn("language=en candidates=4", result.stdout)
        self.assertIn(
            "frequent type=person canonical='alice smith' candidates=4 "
            "document_versions=2 surfaces='Alice Smith | ALICE SMITH'",
            result.stdout,
        )
        self.assertIn(
            "alias-signal type=person left='alice smith' right='smith' "
            "reason=person_short_name",
            result.stdout,
        )
        self.assertIn("title='First story'", result.stdout)
        self.assertIn(
            "surface='Alice Smith' canonical='alice smith' "
            "context='Alice Smith spoke.'",
            result.stdout,
        )

    @patch("argus.interface.cli.get_alias_proposal_audit")
    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    def test_alias_proposal_audit_prints_evidence_and_limitations(
            self,
            upgrade_database,
            configure_logging,
            get_alias_proposal_audit,
    ) -> None:
        get_alias_proposal_audit.return_value = AliasProposalAuditReport(
            proposal_count=1,
            artifact_count=1,
            document_version_count=1,
            counts_by_signal=(ProposalCount("acronym", 1),),
            counts_by_type=(ProposalCount("organization", 1),),
            counts_by_confidence_band=(ProposalCount("high", 1),),
            runs=(
                ProposalRunSummary(
                    artifact_id=31,
                    input_artifact_id=21,
                    document_version_id=3,
                    language="en",
                    proposal_count=1,
                    proposer_version="1",
                    title="First\nstory",
                ),
            ),
            examples=(
                ProposalExample(
                    proposal_id=41,
                    artifact_id=31,
                    document_version_id=3,
                    language="en",
                    entity_type=EntityType.ORGANIZATION,
                    left_text="un",
                    right_text="united nations",
                    signal_type=AliasSignalType.ACRONYM,
                    confidence_score=0.80,
                    confidence_band="high",
                    confidence_basis="deterministic-heuristic-v1",
                    rationale="Initialism in the same document.",
                    left_occurrence_count=1,
                    right_occurrence_count=1,
                    shared_document_count=1,
                    left_context="UN\nspoke.",
                    right_context="United Nations\nanswered.",
                    title="First\nstory",
                ),
            ),
            quality_limitations=(
                "A proposal is not an approved alias.",
            ),
        )

        result = runner.invoke(
            app,
            [
                "alias-proposal-audit",
                "--top",
                "5",
                "--examples",
                "29",
            ],
        )

        self.assertEqual(result.exit_code, 0)
        upgrade_database.assert_called_once_with()
        configure_logging.assert_called_once_with()
        get_alias_proposal_audit.assert_called_once_with(
            top=5,
            examples=29,
        )
        self.assertIn(
            "proposals=1 artifacts=1 document_versions=1",
            result.stdout,
        )
        self.assertIn("signal=acronym proposals=1", result.stdout)
        self.assertIn(
            "confidence_band=high proposals=1",
            result.stdout,
        )
        self.assertIn(
            "left='un' right='united nations' signal=acronym "
            "confidence=0.80 band=high",
            result.stdout,
        )
        self.assertIn("left_context='UN spoke.'", result.stdout)
        self.assertIn("title='First story'", result.stdout)
        self.assertIn(
            "limitation='A proposal is not an approved alias.'",
            result.stdout,
        )

    @patch("argus.interface.cli.get_alias_review_queue")
    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    def test_alias_review_queue_prints_open_evidence(
            self,
            upgrade_database,
            configure_logging,
            get_alias_review_queue,
    ) -> None:
        get_alias_review_queue.return_value = AliasReviewQueueReport(
            open_count=7,
            items=(
                AliasReviewQueueItem(
                    proposal_id=41,
                    document_version_id=3,
                    entity_type=EntityType.ORGANIZATION,
                    left_text="us",
                    right_text="united states",
                    signal_type=AliasSignalType.ACRONYM,
                    confidence_score=0.80,
                    confidence_basis="deterministic-heuristic-v1",
                    rationale="Initialism in the same document.",
                    left_occurrence_count=1,
                    right_occurrence_count=2,
                    shared_document_count=1,
                    left_context="US\nspoke.",
                    right_context="The United States\nanswered.",
                    latest_decision_id=51,
                    latest_revision=1,
                    latest_status=AliasDecisionStatus.NEEDS_REVIEW,
                    latest_reason="Case must be checked.",
                    latest_reviewer="analyst-one",
                ),
            ),
        )

        result = runner.invoke(
            app,
            ["alias-review-queue", "--limit", "5"],
        )

        self.assertEqual(result.exit_code, 0)
        upgrade_database.assert_called_once_with()
        configure_logging.assert_called_once_with()
        get_alias_review_queue.assert_called_once_with(limit=5)
        self.assertIn("open=7 shown=1", result.stdout)
        self.assertIn(
            "proposal_id=41 document_version_id=3 "
            "type=organization left='us' right='united states'",
            result.stdout,
        )
        self.assertIn(
            "latest_status=needs_review latest_revision=1",
            result.stdout,
        )
        self.assertIn("left_context='US spoke.'", result.stdout)

    @patch("argus.interface.cli.record_alias_decision")
    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    def test_decide_alias_records_one_explicit_manual_decision(
            self,
            upgrade_database,
            configure_logging,
            record_alias_decision,
    ) -> None:
        record_alias_decision.return_value = RecordedAliasDecision(
            decision_id=52,
            proposal_id=41,
            revision=2,
            supersedes_decision_id=51,
            status=AliasDecisionStatus.APPROVED,
            reason="Same organization in the cited context.",
            reviewer="analyst-two",
        )

        result = runner.invoke(
            app,
            [
                "decide-alias",
                "--proposal-id",
                "41",
                "--status",
                "approved",
                "--reason",
                "Same organization in the cited context.",
                "--reviewer",
                "analyst-two",
            ],
        )

        self.assertEqual(result.exit_code, 0)
        upgrade_database.assert_called_once_with()
        configure_logging.assert_called_once_with()
        record_alias_decision.assert_called_once_with(
            proposal_id=41,
            status=AliasDecisionStatus.APPROVED,
            reason="Same organization in the cited context.",
            reviewer="analyst-two",
        )
        self.assertIn(
            "decision_id=52 proposal_id=41 revision=2 "
            "supersedes=51 status=approved",
            result.stdout,
        )

    @patch("argus.interface.cli.record_alias_decision")
    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    def test_decide_alias_rejects_unknown_status_before_write(
            self,
            upgrade_database,
            configure_logging,
            record_alias_decision,
    ) -> None:
        result = runner.invoke(
            app,
            [
                "decide-alias",
                "--proposal-id",
                "41",
                "--status",
                "automatic",
                "--reason",
                "Invalid.",
                "--reviewer",
                "analyst",
            ],
        )

        self.assertNotEqual(result.exit_code, 0)
        record_alias_decision.assert_not_called()

    @patch("argus.interface.cli.resolve_alias_identity")
    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    def test_resolve_alias_registers_one_approved_identity(
            self,
            upgrade_database,
            configure_logging,
            resolve_alias_identity,
    ) -> None:
        resolve_alias_identity.return_value = EntityResolutionResult(
            entity_id=61,
            entity_type="organization",
            canonical_name="united nations",
            canonical_entity_candidate_id=12,
            alias_decision_id=52,
            assigned_candidate_ids=(11, 12),
            entity_created=True,
        )

        result = runner.invoke(
            app,
            [
                "resolve-alias",
                "--proposal-id",
                "41",
                "--canonical-candidate-id",
                "12",
            ],
        )

        self.assertEqual(result.exit_code, 0)
        upgrade_database.assert_called_once_with()
        configure_logging.assert_called_once_with()
        resolve_alias_identity.assert_called_once_with(
            proposal_id=41,
            entity_id=None,
            canonical_candidate_id=12,
        )
        self.assertIn(
            "entity_id=61 created=true type=organization "
            "canonical_name='united nations'",
            result.stdout,
        )
        self.assertIn(
            "alias_decision_id=52 assigned_candidate_ids=11,12",
            result.stdout,
        )

    @patch("argus.interface.cli.get_entity_registry_audit")
    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    def test_entity_registry_audit_prints_current_validity(
            self,
            upgrade_database,
            configure_logging,
            get_entity_registry_audit,
    ) -> None:
        get_entity_registry_audit.return_value = (
            EntityRegistryAuditReport(
                entity_count=1,
                safe_entity_count=0,
                blocked_entity_count=1,
                link_count=1,
                counts_by_validity=(
                    ResolutionValidityCount(
                        validity=(
                            EntityResolutionValidity.REVOKED
                        ),
                        count=1,
                    ),
                ),
                items=(
                    EntityRegistryAuditItem(
                        entity_id=61,
                        entity_type=EntityType.ORGANIZATION,
                        canonical_name="united nations",
                        safe_for_downstream_use=False,
                        proposal_id=41,
                        left_candidate_id=11,
                        right_candidate_id=12,
                        applied_decision_ids=(52,),
                        latest_decision_id=53,
                        latest_revision=3,
                        latest_status=(
                            AliasDecisionStatus.REJECTED
                        ),
                        validity=EntityResolutionValidity.REVOKED,
                    ),
                ),
            )
        )

        result = runner.invoke(
            app,
            ["entity-registry-audit", "--limit", "25"],
        )

        self.assertEqual(result.exit_code, 0)
        upgrade_database.assert_called_once_with()
        configure_logging.assert_called_once_with()
        get_entity_registry_audit.assert_called_once_with(limit=25)
        self.assertIn(
            "entities=1 safe=0 blocked=1 links=1 shown=1",
            result.stdout,
        )
        self.assertIn("validity=revoked links=1", result.stdout)
        self.assertIn(
            "entity_id=61 type=organization "
            "canonical_name='united nations' "
            "safe_for_downstream=false proposal_id=41",
            result.stdout,
        )
        self.assertIn(
            "applied_decision_ids=52 latest_decision_id=53 "
            "latest_revision=3 latest_status=rejected "
            "validity=revoked",
            result.stdout,
        )

    @patch("argus.interface.cli.get_safe_entity_projection")
    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    def test_safe_entities_prints_only_projected_identity(
            self,
            upgrade_database,
            configure_logging,
            get_safe_entity_projection,
    ) -> None:
        get_safe_entity_projection.return_value = SafeEntityProjection(
            safe_entity_count=1,
            items=(
                SafeEntity(
                    entity_id=61,
                    entity_type=EntityType.ORGANIZATION,
                    canonical_name="united nations",
                    canonical_entity_candidate_id=12,
                    created_from_alias_decision_id=52,
                    candidates=(
                        SafeEntityCandidate(
                            assignment_id=71,
                            entity_candidate_id=11,
                            entity_type=EntityType.ORGANIZATION,
                            canonical_text="un",
                            document_version_id=21,
                            derived_artifact_id=31,
                            entity_mention_id=41,
                            assigned_by_alias_decision_id=52,
                        ),
                        SafeEntityCandidate(
                            assignment_id=72,
                            entity_candidate_id=12,
                            entity_type=EntityType.ORGANIZATION,
                            canonical_text="united nations",
                            document_version_id=21,
                            derived_artifact_id=31,
                            entity_mention_id=42,
                            assigned_by_alias_decision_id=52,
                        ),
                    ),
                    active_resolutions=(
                        ActiveEntityResolution(
                            proposal_id=41,
                            left_candidate_id=11,
                            right_candidate_id=12,
                            latest_alias_decision_id=52,
                            latest_revision=1,
                        ),
                    ),
                ),
            ),
        )

        result = runner.invoke(
            app,
            [
                "safe-entities",
                "--limit",
                "25",
                "--type",
                "organization",
            ],
        )

        self.assertEqual(result.exit_code, 0)
        upgrade_database.assert_called_once_with()
        configure_logging.assert_called_once_with()
        get_safe_entity_projection.assert_called_once_with(
            limit=25,
            entity_type=EntityType.ORGANIZATION,
        )
        self.assertIn("safe_entities=1 shown=1", result.stdout)
        self.assertIn(
            "entity_id=61 type=organization "
            "canonical_name='united nations' "
            "canonical_candidate_id=12 "
            "candidate_ids=11,12 active_decision_ids=52",
            result.stdout,
        )

    @patch("argus.interface.cli.get_document_entity_projection")
    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    def test_document_entities_prints_safe_occurrence_provenance(
            self,
            upgrade_database,
            configure_logging,
            get_document_entity_projection,
    ) -> None:
        get_document_entity_projection.return_value = (
            DocumentEntityProjection(
                document_version_id=21,
                document_id=20,
                version_number=2,
                resolved_entity_count=1,
                resolved_occurrence_count=1,
                items=(
                    DocumentResolvedEntity(
                        entity_id=61,
                        entity_type=EntityType.ORGANIZATION,
                        canonical_name="united nations",
                        canonical_entity_candidate_id=12,
                        occurrences=(
                            ResolvedEntityOccurrence(
                                entity_candidate_id=11,
                                entity_mention_id=41,
                                derived_artifact_id=31,
                                canonical_text="un",
                                surface_text="UN",
                                normalized_text="un",
                                source_label="ORG",
                                start_char=5,
                                end_char=7,
                                assigned_by_alias_decision_id=52,
                            ),
                        ),
                        active_resolutions=(
                            ActiveEntityResolution(
                                proposal_id=41,
                                left_candidate_id=11,
                                right_candidate_id=12,
                                latest_alias_decision_id=52,
                                latest_revision=1,
                            ),
                        ),
                    ),
                ),
            )
        )

        result = runner.invoke(
            app,
            [
                "document-entities",
                "--document-version-id",
                "21",
                "--limit",
                "25",
                "--type",
                "organization",
            ],
        )

        self.assertEqual(result.exit_code, 0)
        upgrade_database.assert_called_once_with()
        configure_logging.assert_called_once_with()
        get_document_entity_projection.assert_called_once_with(
            document_version_id=21,
            limit=25,
            entity_type=EntityType.ORGANIZATION,
        )
        self.assertIn(
            "document_version_id=21 document_id=20 version=2 "
            "resolved_entities=1 resolved_occurrences=1 shown=1",
            result.stdout,
        )
        self.assertIn(
            "entity_id=61 type=organization "
            "canonical_name='united nations' "
            "canonical_candidate_id=12 occurrences=1 "
            "active_decision_ids=52",
            result.stdout,
        )
        self.assertIn(
            "entity_candidate_id=11 entity_mention_id=41 "
            "derived_artifact_id=31 span=5:7 surface='UN' "
            "canonical='un' assignment_decision_id=52",
            result.stdout,
        )

    @patch("argus.interface.cli.get_document_entity_coverage")
    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    def test_document_entity_coverage_prints_complete_counts(
            self,
            upgrade_database,
            configure_logging,
            get_document_entity_coverage,
    ) -> None:
        get_document_entity_coverage.return_value = (
            DocumentEntityCoverageReport(
                document_version_id=21,
                document_id=20,
                version_number=2,
                candidate_count=2,
                counts_by_status=(
                    DocumentEntityCoverageCount(
                        status=(
                            DocumentEntityCoverageStatus.SAFE_RESOLVED
                        ),
                        count=1,
                    ),
                    DocumentEntityCoverageCount(
                        status=DocumentEntityCoverageStatus.UNASSIGNED,
                        count=0,
                    ),
                    DocumentEntityCoverageCount(
                        status=DocumentEntityCoverageStatus.BLOCKED,
                        count=1,
                    ),
                    DocumentEntityCoverageCount(
                        status=(
                            DocumentEntityCoverageStatus.INVALID_PROVENANCE
                        ),
                        count=0,
                    ),
                ),
                items=(
                    DocumentEntityCoverageItem(
                        entity_candidate_id=11,
                        entity_mention_id=41,
                        derived_artifact_id=31,
                        entity_type=EntityType.ORGANIZATION,
                        canonical_text="un",
                        surface_text="UN",
                        normalized_text="un",
                        start_char=5,
                        end_char=7,
                        status=DocumentEntityCoverageStatus.BLOCKED,
                        entity_id=61,
                        entity_canonical_name="united nations",
                        assigned_by_alias_decision_id=52,
                        blocking_validities=(
                            EntityResolutionValidity.NEEDS_REVIEW,
                        ),
                        provenance_issue=None,
                    ),
                ),
            )
        )

        result = runner.invoke(
            app,
            [
                "document-entity-coverage",
                "--document-version-id",
                "21",
                "--limit",
                "1",
                "--type",
                "organization",
            ],
        )

        self.assertEqual(result.exit_code, 0)
        upgrade_database.assert_called_once_with()
        configure_logging.assert_called_once_with()
        get_document_entity_coverage.assert_called_once_with(
            document_version_id=21,
            limit=1,
            entity_type=EntityType.ORGANIZATION,
        )
        self.assertIn(
            "document_version_id=21 document_id=20 version=2 "
            "candidates=2 shown=1",
            result.stdout,
        )
        self.assertIn(
            "coverage=safe_resolved candidates=1",
            result.stdout,
        )
        self.assertIn(
            "coverage=blocked candidates=1",
            result.stdout,
        )
        self.assertIn(
            "entity_candidate_id=11 entity_mention_id=41 "
            "derived_artifact_id=31 type=organization "
            "coverage=blocked entity_id=61 "
            "assignment_decision_id=52 "
            "blocking_validities=needs_review span=5:7 "
            "surface='UN' canonical='un' provenance_issue=None",
            result.stdout,
        )

    @patch("argus.interface.cli.get_entity_mention_audit")
    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    def test_mention_audit_prints_read_only_quality_report(
            self,
            upgrade_database,
            configure_logging,
            get_entity_mention_audit,
    ) -> None:
        get_entity_mention_audit.return_value = EntityMentionAuditReport(
            mention_count=4,
            artifact_count=2,
            document_version_count=2,
            counts_by_language=(MentionCount("en", 4),),
            counts_by_type=(MentionCount("person", 4),),
            frequent_mentions=(
                FrequentMention(
                    entity_type=EntityType.PERSON,
                    normalized_text="alice",
                    mention_count=4,
                    document_count=2,
                    surface_variants=("Alice", "ALICE"),
                ),
            ),
            densest_runs=(
                MentionRunSummary(
                    artifact_id=8,
                    document_version_id=3,
                    language="en",
                    mention_count=3,
                    unique_form_count=2,
                    method_version="en_core_web_sm@3.8.0",
                    title="First\nstory",
                ),
            ),
            examples=(
                MentionExample(
                    mention_id=11,
                    artifact_id=8,
                    document_version_id=3,
                    language="en",
                    entity_type=EntityType.PERSON,
                    source_label="PERSON",
                    surface_text="Alice\nSmith",
                    normalized_text="alice smith",
                    start_char=0,
                    end_char=11,
                ),
            ),
        )

        result = runner.invoke(
            app,
            ["mention-audit", "--top", "5", "--examples", "7"],
        )

        self.assertEqual(result.exit_code, 0)
        upgrade_database.assert_called_once_with()
        configure_logging.assert_called_once_with()
        get_entity_mention_audit.assert_called_once_with(
            top=5,
            examples=7,
        )
        self.assertIn(
            "mentions=4 artifacts=2 document_versions=2",
            result.stdout,
        )
        self.assertIn("language=en mentions=4", result.stdout)
        self.assertIn(
            "frequent type=person normalized='alice' mentions=4 "
            "document_versions=2 surfaces='Alice | ALICE'",
            result.stdout,
        )
        self.assertIn("title='First story'", result.stdout)
        self.assertIn(
            "span=0:11 surface='Alice Smith' "
            "normalized='alice smith'",
            result.stdout,
        )

    @patch("argus.interface.cli.run_operational_pipeline")
    @patch("argus.interface.cli.configure_logging")
    @patch("argus.interface.cli.upgrade_database")
    def test_run_uses_provenance_pipeline_and_prints_report(
            self,
            upgrade_database,
            configure_logging,
            run_operational_pipeline,
    ) -> None:
        run_operational_pipeline.return_value = (
            OperationalPipelineReport(
                acquisition=AcquisitionBatchReport(items=()),
            )
        )

        result = runner.invoke(
            app,
            [
                "run",
                "--acquisition-limit",
                "7",
                "--analysis-limit",
                "11",
                "--retry-unsuccessful",
                "--retry-failed",
            ],
        )

        self.assertEqual(result.exit_code, 0)
        upgrade_database.assert_called_once_with()
        configure_logging.assert_called_once_with()
        run_operational_pipeline.assert_called_once_with(
            acquisition_limit=7,
            analysis_limit=11,
            retry_unsuccessful=True,
            retry_failed_analysis=True,
        )
        self.assertIn(
            "total=0 processed=0 retrieval_only=0 failed=0",
            result.stdout,
        )


if __name__ == "__main__":
    unittest.main()
