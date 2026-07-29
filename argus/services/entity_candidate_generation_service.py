from dataclasses import dataclass

from sqlalchemy.orm import Session

from argus.documents import DerivedArtifactType
from argus.knowledge import (
    CanonicalizedEntityCandidate,
    EntityCandidateCanonicalizer,
)
from argus.models import (
    DerivedArtifact,
    DocumentVersion,
    EntityCandidate,
    EntityMention,
)
from argus.storage.derived_artifact_repository import DerivedArtifactRepository
from argus.storage.entity_candidate_repository import (
    EntityCandidateRepository,
)
from argus.storage.entity_mention_repository import EntityMentionRepository


@dataclass(frozen=True, slots=True)
class EntityCandidateGeneration:
    """Persisted output of one reproducible candidate-generation run."""

    artifact: DerivedArtifact
    candidates: tuple[EntityCandidate, ...]


class EntityCandidateGenerationService:
    """Create identity-resolution candidates from one immutable NER result.

    The service never commits. The caller owns the surrounding transaction.
    """

    SCHEMA_VERSION = "1"
    CONTEXT_RADIUS = 120
    QUALITY_LIMITATIONS = (
        "Canonical text does not establish that two mentions share identity.",
        "Source NER misclassifications remain visible and are not corrected.",
    )

    def __init__(
            self,
            session: Session,
            *,
            canonicalizer: EntityCandidateCanonicalizer,
    ) -> None:
        self._session = session
        self._canonicalizer = canonicalizer
        self._artifacts = DerivedArtifactRepository(session)
        self._mentions = EntityMentionRepository(session)
        self._candidates = EntityCandidateRepository(session)

    def generate(
            self,
            mention_artifact: DerivedArtifact,
    ) -> EntityCandidateGeneration:
        self._validate_mention_artifact(mention_artifact)
        mentions = self._mentions.get_for_artifact(mention_artifact.id)
        text_artifact = self._load_text_artifact(mention_artifact)
        text = self._read_text(text_artifact)

        decisions: list[dict[str, object]] = []
        projections: list[CanonicalizedEntityCandidate] = []

        for mention in mentions:
            self._validate_mention(mention, mention_artifact, text)
            decision = self._canonicalizer.canonicalize(
                entity_type=mention.entity_type,
                normalized_text=mention.normalized_text,
            )
            context_start, context_end, context_text = self._context(
                text,
                mention,
            )
            decision_payload: dict[str, object] = {
                "entity_mention_id": mention.id,
                "entity_type": mention.entity_type.value,
                "surface_text": mention.surface_text,
                "is_candidate": decision.is_candidate,
            }

            if decision.is_candidate:
                decision_payload["canonical_text"] = (
                    decision.canonical_text
                )
                decision_payload["context_start_char"] = context_start
                decision_payload["context_end_char"] = context_end
                decision_payload["context_text"] = context_text
                projections.append(
                    CanonicalizedEntityCandidate(
                        entity_mention_id=mention.id,
                        document_version_id=(
                            mention.document_version_id
                        ),
                        entity_type=mention.entity_type,
                        canonical_text=decision.canonical_text,
                        context_text=context_text,
                        context_start_char=context_start,
                        context_end_char=context_end,
                    )
                )
            else:
                decision_payload["exclusion_reason"] = (
                    decision.exclusion_reason.value
                )
            decisions.append(decision_payload)

        artifact = self._artifacts.register(
            document_version=self._load_version(mention_artifact),
            artifact_type=DerivedArtifactType.ENTITY_CANDIDATES,
            method=self._canonicalizer.method,
            method_version=self._canonicalizer.method_version,
            schema_version=self.SCHEMA_VERSION,
            payload={
                "input_artifact_id": mention_artifact.id,
                "input_content_hash": mention_artifact.content_hash,
                "input_method": mention_artifact.method,
                "input_method_version": mention_artifact.method_version,
                "context_radius": self.CONTEXT_RADIUS,
                "decisions": decisions,
            },
            quality_limitations=self.QUALITY_LIMITATIONS,
        )
        candidates = self._candidates.register(
            artifact=artifact,
            candidates=projections,
        )
        return EntityCandidateGeneration(
            artifact=artifact,
            candidates=tuple(candidates),
        )

    @staticmethod
    def _validate_mention_artifact(artifact: DerivedArtifact) -> None:
        if artifact.id is None:
            raise ValueError(
                "mention_artifact must be persisted before generation."
            )
        if artifact.artifact_type is not DerivedArtifactType.ENTITY_MENTIONS:
            raise ValueError(
                "mention_artifact must contain entity mentions."
            )

    def _load_text_artifact(
            self,
            mention_artifact: DerivedArtifact,
    ) -> DerivedArtifact:
        input_id = mention_artifact.payload.get("input_artifact_id")
        input_hash = mention_artifact.payload.get("input_content_hash")
        if not isinstance(input_id, int) or not isinstance(input_hash, str):
            raise ValueError(
                "Mention artifact must identify its exact text input."
            )
        artifact = self._session.get(DerivedArtifact, input_id)
        if artifact is None:
            raise ValueError("Mention artifact text input does not exist.")
        if artifact.content_hash != input_hash:
            raise ValueError("Mention artifact text input hash conflicts.")
        if artifact.document_version_id != mention_artifact.document_version_id:
            raise ValueError(
                "Mention and text artifacts must share a document version."
            )
        return artifact

    @staticmethod
    def _read_text(artifact: DerivedArtifact) -> str:
        text = artifact.payload.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Text artifact payload must contain usable text.")
        return text

    def _load_version(
            self,
            artifact: DerivedArtifact,
    ) -> DocumentVersion:
        version = self._session.get(
            DocumentVersion,
            artifact.document_version_id,
        )
        if version is None:
            raise ValueError("Mention artifact document version does not exist.")
        return version

    @staticmethod
    def _validate_mention(
            mention: EntityMention,
            artifact: DerivedArtifact,
            text: str,
    ) -> None:
        if mention.document_version_id != artifact.document_version_id:
            raise ValueError(
                "Mention and artifact document versions conflict."
            )
        if mention.end_char > len(text):
            raise ValueError("Entity mention exceeds source text.")
        if text[mention.start_char:mention.end_char] != mention.surface_text:
            raise ValueError(
                "Entity mention surface text does not match source text."
            )

    @classmethod
    def _context(
            cls,
            text: str,
            mention: EntityMention,
    ) -> tuple[int, int, str]:
        start = max(0, mention.start_char - cls.CONTEXT_RADIUS)
        end = min(len(text), mention.end_char + cls.CONTEXT_RADIUS)
        return start, end, text[start:end]
