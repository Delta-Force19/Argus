from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from argus.database import SessionLocal
from argus.documents import DerivedArtifactType
from argus.knowledge import EntityRecognizer
from argus.logging.logger import get_logger
from argus.models import DerivedArtifact, Document, DocumentVersion
from argus.recognizers.spacy_entity_recognizer import SpacyEntityRecognizer
from argus.services.entity_mention_batch_runner import (
    EntityMentionBatchReport,
    EntityMentionBatchRunner,
)
from argus.services.entity_mention_extraction_service import (
    EntityMentionExtractionService,
)


logger = get_logger(__name__)


def run_entity_mention_pipeline(
        *,
        limit: int = 20,
        session_factory: Callable[[], Session] = SessionLocal,
        recognizer: EntityRecognizer | None = None,
) -> EntityMentionBatchReport:
    """Extract mentions from a bounded batch of pending text artifacts."""

    if limit < 1:
        raise ValueError("limit must be greater than zero.")

    resolved_recognizer = recognizer or SpacyEntityRecognizer()
    with session_factory() as session:
        artifact_ids = _pending_text_artifact_ids(
            session=session,
            recognizer=resolved_recognizer,
            limit=limit,
        )

    report = EntityMentionBatchRunner(
        session_factory,
        recognizer=resolved_recognizer,
    ).run(artifact_ids)
    logger.info(
        "Entity mention extraction finished; total: %s; "
        "processed: %s; failed: %s; mentions: %s",
        report.total_count,
        report.processed_count,
        report.failed_count,
        report.mention_count,
    )
    return report


def _pending_text_artifact_ids(
        *,
        session: Session,
        recognizer: EntityRecognizer,
        limit: int,
) -> tuple[int, ...]:
    """Select inputs without a matching reproducible recognition output."""

    if limit < 1:
        raise ValueError("limit must be greater than zero.")

    completed = _completed_input_signatures(
        session=session,
        recognizer=recognizer,
    )
    statement = (
        select(DerivedArtifact, Document.language)
        .join(
            DocumentVersion,
            DocumentVersion.id == DerivedArtifact.document_version_id,
        )
        .join(
            Document,
            Document.id == DocumentVersion.document_id,
        )
        .where(
            DerivedArtifact.artifact_type.in_(
                EntityMentionExtractionService.TEXT_ARTIFACT_TYPES
            )
        )
        .order_by(DerivedArtifact.id.asc())
    )
    method_versions: dict[str, str | None] = {}
    selected: list[int] = []

    for artifact, language in session.execute(statement):
        normalized_language = _normalize_language(language)
        if normalized_language is None:
            continue
        if normalized_language not in method_versions:
            try:
                method_versions[normalized_language] = (
                    recognizer.method_version(normalized_language)
                )
            except ValueError:
                method_versions[normalized_language] = None
        method_version = method_versions[normalized_language]
        if method_version is None:
            continue

        signature = (
            artifact.id,
            artifact.content_hash,
            method_version,
        )
        if signature in completed:
            continue
        selected.append(artifact.id)
        if len(selected) == limit:
            break

    return tuple(selected)


def _completed_input_signatures(
        *,
        session: Session,
        recognizer: EntityRecognizer,
) -> set[tuple[int, str, str]]:
    statement = select(DerivedArtifact).where(
        DerivedArtifact.artifact_type
        == DerivedArtifactType.ENTITY_MENTIONS,
        DerivedArtifact.method == recognizer.method,
        DerivedArtifact.schema_version
        == EntityMentionExtractionService.SCHEMA_VERSION,
    )
    signatures: set[tuple[int, str, str]] = set()

    for artifact in session.scalars(statement):
        input_id = artifact.payload.get("input_artifact_id")
        input_hash = artifact.payload.get("input_content_hash")
        if (
                isinstance(input_id, int)
                and isinstance(input_hash, str)
        ):
            signatures.add(
                (
                    input_id,
                    input_hash,
                    artifact.method_version,
                )
            )

    return signatures


def _normalize_language(language: str | None) -> str | None:
    if language is None or not language.strip():
        return None
    return language.strip().lower().split("-", maxsplit=1)[0]
