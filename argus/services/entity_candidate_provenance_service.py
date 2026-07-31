from dataclasses import dataclass

from sqlalchemy.orm import Session

from argus.documents import DerivedArtifactType
from argus.models import DerivedArtifact, EntityCandidate, EntityMention


TEXT_ARTIFACT_TYPES = frozenset(
    {
        DerivedArtifactType.EXTRACTED_TEXT,
        DerivedArtifactType.OCR_TEXT,
        DerivedArtifactType.TRANSCRIPT,
        DerivedArtifactType.TRANSLATION,
    }
)


@dataclass(frozen=True, slots=True)
class EntityCandidateProvenance:
    """Validated immutable artifact chain behind one entity candidate."""

    candidate_artifact: DerivedArtifact
    mention_artifact: DerivedArtifact
    text_artifact: DerivedArtifact
    text: str


def resolve_entity_candidate_provenance(
        session: Session,
        *,
        candidate: EntityCandidate,
        mention: EntityMention | None,
        document_version_id: int,
) -> tuple[EntityCandidateProvenance | None, str | None]:
    """Resolve candidate -> mentions -> text without hiding broken links."""

    if mention is None:
        return None, "Entity candidate references a missing mention."
    if mention.document_version_id != document_version_id:
        return None, "Entity candidate and mention use different documents."
    if mention.entity_type is not candidate.entity_type:
        return None, "Entity candidate and mention use different types."

    candidate_artifact = session.get(
        DerivedArtifact,
        candidate.derived_artifact_id,
    )
    if candidate_artifact is None:
        return None, "Entity candidate artifact does not exist."
    if (
        candidate_artifact.artifact_type
        is not DerivedArtifactType.ENTITY_CANDIDATES
    ):
        return None, "Entity candidate references the wrong artifact type."
    if candidate_artifact.document_version_id != document_version_id:
        return None, "Entity candidate artifact belongs to another document."

    mention_artifact = session.get(
        DerivedArtifact,
        mention.derived_artifact_id,
    )
    if mention_artifact is None:
        return None, "Entity mention artifact does not exist."
    if (
        mention_artifact.artifact_type
        is not DerivedArtifactType.ENTITY_MENTIONS
    ):
        return None, "Entity mention references the wrong artifact type."
    if mention_artifact.document_version_id != document_version_id:
        return None, "Entity mention artifact belongs to another document."

    issue = _validate_input_reference(
        output_artifact=candidate_artifact,
        input_artifact=mention_artifact,
        label="candidate",
    )
    if issue is not None:
        return None, issue

    text_artifact_id = mention_artifact.payload.get("input_artifact_id")
    if not isinstance(text_artifact_id, int):
        return None, "Entity mention artifact has no text input."
    text_artifact = session.get(DerivedArtifact, text_artifact_id)
    if text_artifact is None:
        return None, "Entity mention text input does not exist."
    if text_artifact.artifact_type not in TEXT_ARTIFACT_TYPES:
        return None, "Entity mention input is not a text artifact."
    if text_artifact.document_version_id != document_version_id:
        return None, "Entity mention text input belongs to another document."

    issue = _validate_input_reference(
        output_artifact=mention_artifact,
        input_artifact=text_artifact,
        label="mention",
    )
    if issue is not None:
        return None, issue

    text = text_artifact.payload.get("text")
    if not isinstance(text, str) or not text.strip():
        return None, "Entity mention text input has no usable text."
    if mention.end_char > len(text):
        return None, "Entity mention exceeds its text input."
    if text[mention.start_char:mention.end_char] != mention.surface_text:
        return None, "Entity mention span does not match its text input."

    return (
        EntityCandidateProvenance(
            candidate_artifact=candidate_artifact,
            mention_artifact=mention_artifact,
            text_artifact=text_artifact,
            text=text,
        ),
        None,
    )


def _validate_input_reference(
        *,
        output_artifact: DerivedArtifact,
        input_artifact: DerivedArtifact,
        label: str,
) -> str | None:
    input_id = output_artifact.payload.get("input_artifact_id")
    input_hash = output_artifact.payload.get("input_content_hash")
    if input_id != input_artifact.id:
        return f"Entity {label} artifact references another input."
    if input_hash != input_artifact.content_hash:
        return f"Entity {label} artifact input hash conflicts."
    return None
