from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from argus.documents import DerivedArtifactType
from argus.knowledge import ProposedEntityAlias
from argus.models import AliasProposal, DerivedArtifact
from argus.storage.base_repository import BaseRepository


class AliasProposalRepository(BaseRepository[AliasProposal]):
    """Project immutable alias-proposal artifacts into queryable rows."""

    def __init__(self, session: Session) -> None:
        super().__init__(session=session, model_type=AliasProposal)

    def register(
            self,
            *,
            artifact: DerivedArtifact,
            proposals: Sequence[ProposedEntityAlias],
    ) -> list[AliasProposal]:
        if artifact.id is None:
            raise ValueError("artifact must be persisted before projection.")
        if artifact.artifact_type is not DerivedArtifactType.ALIAS_PROPOSALS:
            raise ValueError("artifact must contain alias proposals.")

        existing = self.get_for_artifact(artifact.id)
        if existing:
            if self._signatures(existing) != self._proposal_signatures(
                    proposals
            ):
                raise ValueError(
                    "Stored alias proposals conflict with immutable artifact."
                )
            return existing

        rows = [
            AliasProposal(
                derived_artifact_id=artifact.id,
                document_version_id=proposal.document_version_id,
                left_entity_candidate_id=(
                    proposal.left_entity_candidate_id
                ),
                right_entity_candidate_id=(
                    proposal.right_entity_candidate_id
                ),
                entity_type=proposal.entity_type,
                left_canonical_text=proposal.left_canonical_text,
                right_canonical_text=proposal.right_canonical_text,
                signal_type=proposal.signal_type,
                confidence_score=proposal.confidence_score,
                confidence_basis=proposal.confidence_basis,
                rationale=proposal.rationale,
                left_occurrence_count=proposal.left_occurrence_count,
                right_occurrence_count=proposal.right_occurrence_count,
                shared_document_count=proposal.shared_document_count,
            )
            for proposal in proposals
        ]
        self.session.add_all(rows)
        self.flush()
        return rows

    def get_for_artifact(self, artifact_id: int) -> list[AliasProposal]:
        statement = (
            select(AliasProposal)
            .where(AliasProposal.derived_artifact_id == artifact_id)
            .order_by(
                AliasProposal.left_entity_candidate_id.asc(),
                AliasProposal.right_entity_candidate_id.asc(),
                AliasProposal.signal_type.asc(),
                AliasProposal.id.asc(),
            )
        )
        return list(self.session.scalars(statement).all())

    @staticmethod
    def _signatures(
            proposals: Sequence[AliasProposal],
    ) -> list[tuple[object, ...]]:
        return [
            (
                item.document_version_id,
                item.left_entity_candidate_id,
                item.right_entity_candidate_id,
                item.entity_type,
                item.left_canonical_text,
                item.right_canonical_text,
                item.signal_type,
                item.confidence_score,
                item.confidence_basis,
                item.rationale,
                item.left_occurrence_count,
                item.right_occurrence_count,
                item.shared_document_count,
            )
            for item in proposals
        ]

    @staticmethod
    def _proposal_signatures(
            proposals: Sequence[ProposedEntityAlias],
    ) -> list[tuple[object, ...]]:
        return [
            (
                item.document_version_id,
                item.left_entity_candidate_id,
                item.right_entity_candidate_id,
                item.entity_type,
                item.left_canonical_text,
                item.right_canonical_text,
                item.signal_type,
                item.confidence_score,
                item.confidence_basis,
                item.rationale,
                item.left_occurrence_count,
                item.right_occurrence_count,
                item.shared_document_count,
            )
            for item in proposals
        ]
