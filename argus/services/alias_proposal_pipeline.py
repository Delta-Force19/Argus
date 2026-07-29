from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from argus.database import SessionLocal
from argus.documents import DerivedArtifactType
from argus.knowledge import EntityAliasProposer
from argus.logging.logger import get_logger
from argus.models import DerivedArtifact
from argus.proposers import DeterministicEntityAliasProposer
from argus.services.alias_proposal_batch_runner import (
    AliasProposalBatchReport,
    AliasProposalBatchRunner,
)
from argus.services.alias_proposal_generation_service import (
    AliasProposalGenerationService,
)


logger = get_logger(__name__)


def run_alias_proposal_pipeline(
        *,
        limit: int = 20,
        session_factory: Callable[[], Session] = SessionLocal,
        proposer: EntityAliasProposer | None = None,
) -> AliasProposalBatchReport:
    """Generate proposals from a bounded batch of candidate artifacts."""

    if limit < 1:
        raise ValueError("limit must be greater than zero.")

    resolved_proposer = proposer or DeterministicEntityAliasProposer()
    with session_factory() as session:
        artifact_ids = _pending_candidate_artifact_ids(
            session=session,
            proposer=resolved_proposer,
            limit=limit,
        )

    report = AliasProposalBatchRunner(
        session_factory,
        proposer=resolved_proposer,
    ).run(artifact_ids)
    logger.info(
        "Alias proposal generation finished; total: %s; "
        "processed: %s; failed: %s; proposals: %s",
        report.total_count,
        report.processed_count,
        report.failed_count,
        report.proposal_count,
    )
    return report


def _pending_candidate_artifact_ids(
        *,
        session: Session,
        proposer: EntityAliasProposer,
        limit: int,
) -> tuple[int, ...]:
    """Select candidate inputs without a matching reproducible output."""

    if limit < 1:
        raise ValueError("limit must be greater than zero.")

    completed = _completed_input_signatures(
        session=session,
        proposer=proposer,
    )
    statement = (
        select(DerivedArtifact)
        .where(
            DerivedArtifact.artifact_type
            == DerivedArtifactType.ENTITY_CANDIDATES
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
        proposer: EntityAliasProposer,
) -> set[tuple[int, str]]:
    statement = select(DerivedArtifact).where(
        DerivedArtifact.artifact_type
        == DerivedArtifactType.ALIAS_PROPOSALS,
        DerivedArtifact.method == proposer.method,
        DerivedArtifact.method_version == proposer.method_version,
        DerivedArtifact.schema_version
        == AliasProposalGenerationService.SCHEMA_VERSION,
    )
    signatures: set[tuple[int, str]] = set()

    for artifact in session.scalars(statement):
        input_id = artifact.payload.get("input_artifact_id")
        input_hash = artifact.payload.get("input_content_hash")
        if isinstance(input_id, int) and isinstance(input_hash, str):
            signatures.add((input_id, input_hash))

    return signatures
