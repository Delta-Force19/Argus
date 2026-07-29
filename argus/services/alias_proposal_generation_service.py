from dataclasses import dataclass

from sqlalchemy.orm import Session

from argus.documents import DerivedArtifactType
from argus.knowledge import (
    AliasCandidate,
    EntityAliasProposer,
    ProposedEntityAlias,
)
from argus.models import (
    AliasProposal,
    DerivedArtifact,
    DocumentVersion,
    EntityCandidate,
)
from argus.storage.alias_proposal_repository import AliasProposalRepository
from argus.storage.derived_artifact_repository import DerivedArtifactRepository
from argus.storage.entity_candidate_repository import (
    EntityCandidateRepository,
)


@dataclass(frozen=True, slots=True)
class AliasProposalGeneration:
    """Persisted output of one reproducible alias-proposal run."""

    artifact: DerivedArtifact
    proposals: tuple[AliasProposal, ...]


class AliasProposalGenerationService:
    """Create review-only alias proposals from one candidate artifact.

    The service never commits. The caller owns the surrounding transaction.
    """

    SCHEMA_VERSION = "1"
    QUALITY_LIMITATIONS = (
        "An alias proposal is not a resolved identity or approved alias.",
        "Confidence scores are deterministic heuristic ranks, not calibrated "
        "probabilities.",
        "Only forms co-occurring in one candidate artifact are compared.",
        "Source NER types and canonicalization errors are not repaired.",
    )

    def __init__(
            self,
            session: Session,
            *,
            proposer: EntityAliasProposer,
    ) -> None:
        self._session = session
        self._proposer = proposer
        self._artifacts = DerivedArtifactRepository(session)
        self._candidates = EntityCandidateRepository(session)
        self._proposals = AliasProposalRepository(session)

    def generate(
            self,
            candidate_artifact: DerivedArtifact,
    ) -> AliasProposalGeneration:
        self._validate_candidate_artifact(candidate_artifact)
        candidates = self._candidates.get_for_artifact(
            candidate_artifact.id
        )
        detached = tuple(
            AliasCandidate(
                id=candidate.id,
                document_version_id=candidate.document_version_id,
                entity_type=candidate.entity_type,
                canonical_text=candidate.canonical_text,
                context_text=candidate.context_text,
            )
            for candidate in candidates
        )
        proposed = self._proposer.propose(detached)
        candidates_by_id = {
            candidate.id: candidate for candidate in candidates
        }
        self._validate_proposals(
            proposed,
            candidates_by_id=candidates_by_id,
            candidate_artifact=candidate_artifact,
        )

        artifact = self._artifacts.register(
            document_version=self._load_version(candidate_artifact),
            artifact_type=DerivedArtifactType.ALIAS_PROPOSALS,
            method=self._proposer.method,
            method_version=self._proposer.method_version,
            schema_version=self.SCHEMA_VERSION,
            payload={
                "input_artifact_id": candidate_artifact.id,
                "input_content_hash": candidate_artifact.content_hash,
                "input_method": candidate_artifact.method,
                "input_method_version": candidate_artifact.method_version,
                "proposals": [
                    self._proposal_payload(
                        proposal,
                        candidates_by_id=candidates_by_id,
                    )
                    for proposal in proposed
                ],
            },
            quality_limitations=self.QUALITY_LIMITATIONS,
        )
        rows = self._proposals.register(
            artifact=artifact,
            proposals=proposed,
        )
        return AliasProposalGeneration(
            artifact=artifact,
            proposals=tuple(rows),
        )

    @staticmethod
    def _validate_candidate_artifact(artifact: DerivedArtifact) -> None:
        if artifact.id is None:
            raise ValueError(
                "candidate_artifact must be persisted before generation."
            )
        if artifact.artifact_type is not DerivedArtifactType.ENTITY_CANDIDATES:
            raise ValueError(
                "candidate_artifact must contain entity candidates."
            )

    def _load_version(
            self,
            artifact: DerivedArtifact,
    ) -> DocumentVersion:
        version = self._session.get(
            DocumentVersion,
            artifact.document_version_id,
        )
        if version is None:
            raise ValueError(
                "Candidate artifact document version does not exist."
            )
        return version

    @staticmethod
    def _validate_proposals(
            proposals: tuple[ProposedEntityAlias, ...],
            *,
            candidates_by_id: dict[int, EntityCandidate],
            candidate_artifact: DerivedArtifact,
    ) -> None:
        signatures: set[tuple[int, int, object]] = set()
        for proposal in proposals:
            left = candidates_by_id.get(
                proposal.left_entity_candidate_id
            )
            right = candidates_by_id.get(
                proposal.right_entity_candidate_id
            )
            if left is None or right is None:
                raise ValueError(
                    "Alias proposal must reference candidates from its input."
                )
            if (
                    left.document_version_id
                    != candidate_artifact.document_version_id
                    or right.document_version_id
                    != candidate_artifact.document_version_id
                    or proposal.document_version_id
                    != candidate_artifact.document_version_id
            ):
                raise ValueError(
                    "Alias proposal and input must share a document version."
                )
            if (
                    proposal.entity_type != left.entity_type
                    or proposal.entity_type != right.entity_type
            ):
                raise ValueError(
                    "Alias proposal candidate types must agree."
                )
            if (
                    proposal.left_canonical_text
                    != left.canonical_text
                    or proposal.right_canonical_text
                    != right.canonical_text
            ):
                raise ValueError(
                    "Alias proposal forms must match persisted candidates."
                )
            signature = (
                proposal.left_entity_candidate_id,
                proposal.right_entity_candidate_id,
                proposal.signal_type,
            )
            if signature in signatures:
                raise ValueError(
                    "Alias proposer returned a duplicate pair and signal."
                )
            signatures.add(signature)

    @staticmethod
    def _proposal_payload(
            proposal: ProposedEntityAlias,
            *,
            candidates_by_id: dict[int, EntityCandidate],
    ) -> dict[str, object]:
        left = candidates_by_id[proposal.left_entity_candidate_id]
        right = candidates_by_id[proposal.right_entity_candidate_id]
        return {
            "left_entity_candidate_id": (
                proposal.left_entity_candidate_id
            ),
            "right_entity_candidate_id": (
                proposal.right_entity_candidate_id
            ),
            "entity_type": proposal.entity_type.value,
            "left_canonical_text": proposal.left_canonical_text,
            "right_canonical_text": proposal.right_canonical_text,
            "signal_type": proposal.signal_type.value,
            "confidence_score": proposal.confidence_score,
            "confidence_basis": proposal.confidence_basis,
            "rationale": proposal.rationale,
            "evidence": {
                "left_occurrence_count": (
                    proposal.left_occurrence_count
                ),
                "right_occurrence_count": (
                    proposal.right_occurrence_count
                ),
                "shared_document_count": (
                    proposal.shared_document_count
                ),
                "left_context": left.context_text,
                "right_context": right.context_text,
            },
        }
