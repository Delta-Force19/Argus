from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlparse

from argus.documents import DerivedArtifactType


class EventTextReadinessStatus(str, Enum):
    """Whether a text artifact can represent the source's event content."""

    READY = "ready"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class EventTextReadiness:
    status: EventTextReadinessStatus
    ready_for_event_analysis: bool
    reasons: tuple[str, ...]
    limitations: tuple[str, ...]


def assess_event_text_readiness(
        *,
        identifier_scheme: str,
        identifier_value: str,
        artifact_type: DerivedArtifactType,
        text: str,
) -> EventTextReadiness:
    """Conservatively check whether text covers the source content itself."""

    reasons: list[str] = []
    limitations: list[str] = []
    if (
            _is_video_page(identifier_scheme, identifier_value)
            and artifact_type is not DerivedArtifactType.TRANSCRIPT
    ):
        reasons.append(
            "The document URI identifies a video page, but the selected "
            "artifact is not a transcript. Extracted HTML may contain only "
            "the page description or surrounding boilerplate."
        )
    if len(text) < 800:
        limitations.append(
            "The selected text contains fewer than 800 characters; it may "
            "be a summary, teaser, or unusually short report."
        )
    if "\n\n" not in text:
        limitations.append(
            "The selected text has no paragraph separators; structural "
            "coverage cannot be established from layout."
        )
    blocked = bool(reasons)
    return EventTextReadiness(
        status=(
            EventTextReadinessStatus.BLOCKED
            if blocked
            else EventTextReadinessStatus.READY
        ),
        ready_for_event_analysis=not blocked,
        reasons=tuple(reasons),
        limitations=tuple(limitations),
    )


def _is_video_page(identifier_scheme: str, identifier_value: str) -> bool:
    if identifier_scheme.strip().lower() not in {"uri", "url"}:
        return False
    path_segments = {
        segment.lower()
        for segment in urlparse(identifier_value).path.split("/")
        if segment
    }
    return "video" in path_segments
