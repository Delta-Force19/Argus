import json

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from argus.analysis.schemas import DiscourseMetrics
from argus.models import (
    AnalysisEvidence,
    Article,
    DiscourseAnalysisResult,
    ProcessingState,
)
from argus.processing import (
    ProcessingStage,
    ProcessingStatus,
)
from argus.storage.base_repository import BaseRepository


class DiscourseAnalysisRepository(
    BaseRepository[DiscourseAnalysisResult]
):
    def __init__(self, session: Session) -> None:
        super().__init__(
            session=session,
            model_type=DiscourseAnalysisResult,
        )

    def get_pending_articles(
            self,
            method_version: str,
            limit: int = 20,
            retry_failed: bool = False,
    ) -> list[Article]:
        blocked_statuses: list[ProcessingStatus] = [
            ProcessingStatus.RUNNING,
            ProcessingStatus.DONE,
        ]

        if not retry_failed:
            blocked_statuses.append(
                ProcessingStatus.FAILED
            )

        analysis_exists = exists().where(
            DiscourseAnalysisResult.article_id == (
                Article.id
            ),
            DiscourseAnalysisResult.method_version == (
                method_version
            ),
            )

        processing_state_exists = exists().where(
            ProcessingState.article_id == Article.id,
            ProcessingState.stage == (
                ProcessingStage.DISCOURSE
            ),
            ProcessingState.method_version == (
                method_version
            ),
            ProcessingState.status.in_(
                blocked_statuses
            ),
            )

        statement = (
            select(Article)
            .where(Article.content.is_not(None))
            .where(Article.content != "")
            .where(~analysis_exists)
            .where(~processing_state_exists)
            .order_by(Article.fetched_at.asc())
            .limit(limit)
        )

        return list(
            self.session.scalars(statement).all()
        )

    def save_result(
            self,
            article_id: int,
            method_version: str,
            metrics: DiscourseMetrics,
    ) -> DiscourseAnalysisResult:
        result = DiscourseAnalysisResult(
            article_id=article_id,
            method_version=method_version,
            word_count=metrics.word_count,
            sentence_count=metrics.sentence_count,
            average_sentence_length=(
                metrics.average_sentence_length
            ),
            question_count=metrics.question_count,
            exclamation_count=metrics.exclamation_count,
            first_person_plural_count=(
                metrics.first_person_plural_count
            ),
            third_person_plural_count=(
                metrics.third_person_plural_count
            ),
            certainty_marker_count=(
                metrics.certainty_marker_count
            ),
            uncertainty_marker_count=(
                metrics.uncertainty_marker_count
            ),
            fear_marker_count=(
                metrics.fear_marker_count
            ),
            threat_marker_count=(
                metrics.threat_marker_count
            ),
        )

        self.add(result)
        self.flush()

        evidence_rows = [
            AnalysisEvidence(
                analysis_result_id=result.id,
                category=evidence.category.value,
                sentence=evidence.sentence,
                matched_terms=json.dumps(
                    evidence.matched_terms,
                    ensure_ascii=False,
                ),
            )
            for evidence in metrics.evidence
        ]

        self.session.add_all(evidence_rows)
        self.session.commit()
        self.refresh(result)

        return result