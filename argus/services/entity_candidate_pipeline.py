from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from argus.canonicalizers import (
    DeterministicEntityCandidateCanonicalizer,
)
from argus.database import SessionLocal
from argus.documents import DerivedArtifactType
from argus.knowledge import EntityCandidateCanonicalizer
from argus.logging.logger import get_logger
from argus.models import DerivedArtifact
from argus.services.entity_candidate_batch_runner import (
    EntityCandidateBatchReport,
    EntityCandidateBatchRunner,
)
from argus.services.entity_candidate_generation_service import (
    EntityCandidateGenerationService,
)


logger = get_logger(__name__)


def run_entity_candidate_pipeline(
        *,
        limit: int = 20,
        session_factory: Callable[[], Session] = SessionLocal,
        canonicalizer: EntityCandidateCanonicalizer | None = None,
) -> EntityCandidateBatchReport:
    """Generate candidates from a bounded batch of pending NER artifacts."""

    if limit < 1:
        raise ValueError("limit must be greater than zero.")

    resolved_canonicalizer = (
        canonicalizer or DeterministicEntityCandidateCanonicalizer()
    )
    with session_factory() as session:
        artifact_ids = _pending_mention_artifact_ids(
            session=session,
            canonicalizer=resolved_canonicalizer,
            limit=limit,
        )

    report = EntityCandidateBatchRunner(
        session_factory,
        canonicalizer=resolved_canonicalizer,
    ).run(artifact_ids)
    logger.info(
        "Entity candidate generation finished; total: %s; "
        "processed: %s; failed: %s; candidates: %s; excluded: %s",
        report.total_count,
        report.processed_count,
        report.failed_count,
        report.candidate_count,
        report.excluded_count,
    )
    return report


def _pending_mention_artifact_ids(
        *,
        session: Session,
        canonicalizer: EntityCandidateCanonicalizer,
        limit: int,
) -> tuple[int, ...]:
    """Select NER inputs without a matching reproducible candidate output."""

    if limit < 1:
        raise ValueError("limit must be greater than zero.")

    completed = _completed_input_signatures(
        session=session,
        canonicalizer=canonicalizer,
    )
    statement = (
        select(DerivedArtifact)
        .where(
            DerivedArtifact.artifact_type
            == DerivedArtifactType.ENTITY_MENTIONS
        )
        .order_by(DerivedArtifact.id.asc())
    )
    selected: list[int] = []

    for artifact in session.scalars(statement):
        signature = (artifact.id, artifact.content_hash)
        if signature in completed:
            continue
        selected.append(artifact.id)
        if len(selected) == limit:
            break

    return tuple(selected)


def _completed_input_signatures(
        *,
        session: Session,
        canonicalizer: EntityCandidateCanonicalizer,
) -> set[tuple[int, str]]:
    statement = select(DerivedArtifact).where(
        DerivedArtifact.artifact_type
        == DerivedArtifactType.ENTITY_CANDIDATES,
        DerivedArtifact.method == canonicalizer.method,
        DerivedArtifact.method_version == canonicalizer.method_version,
        DerivedArtifact.schema_version
        == EntityCandidateGenerationService.SCHEMA_VERSION,
    )
    signatures: set[tuple[int, str]] = set()

    for artifact in session.scalars(statement):
        input_id = artifact.payload.get("input_artifact_id")
        input_hash = artifact.payload.get("input_content_hash")
        if isinstance(input_id, int) and isinstance(input_hash, str):
            signatures.add((input_id, input_hash))

    return signatures
