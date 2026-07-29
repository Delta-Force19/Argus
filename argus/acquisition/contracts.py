from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
from json import dumps


class AcquisitionMode(str, Enum):
    """Reason for starting an acquisition operation."""

    CONTINUOUS = "continuous"
    INVESTIGATION = "investigation"


class RetrievalOutcome(str, Enum):
    """Protocol-independent outcome of a retrieval attempt."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"
    ACCESS_RESTRICTED = "access_restricted"


def _validate_aware_datetime(
    value: datetime | None,
    field_name: str,
) -> None:
    if value is None:
        return

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(
            f"{field_name} must be timezone-aware."
        )


@dataclass(frozen=True, slots=True)
class DiscoveryRequest:
    """Normalized constraints supplied to a discovery connector."""

    mode: AcquisitionMode
    query: str | None = None
    languages: tuple[str, ...] = ()
    published_from: datetime | None = None
    published_until: datetime | None = None
    limit: int | None = None
    cursor: str | None = None

    def __post_init__(self) -> None:
        if self.query is not None and not self.query.strip():
            raise ValueError(
                "query must not be blank."
            )

        if any(
            not language.strip()
            for language in self.languages
        ):
            raise ValueError(
                "languages must not contain blank values."
            )

        if len(set(self.languages)) != len(self.languages):
            raise ValueError(
                "languages must not contain duplicates."
            )

        if self.limit is not None and self.limit < 1:
            raise ValueError(
                "limit must be greater than zero."
            )

        if self.cursor is not None and not self.cursor.strip():
            raise ValueError(
                "cursor must not be blank."
            )

        _validate_aware_datetime(
            self.published_from,
            "published_from",
        )
        _validate_aware_datetime(
            self.published_until,
            "published_until",
        )

        if (
            self.published_from is not None
            and self.published_until is not None
            and self.published_from > self.published_until
        ):
            raise ValueError(
                "published_from must not be later than "
                "published_until."
            )


@dataclass(frozen=True, slots=True)
class CandidateRecord:
    """Lightweight attributable record discovered by a connector."""

    connector_id: str
    connector_version: str
    location: str
    discovered_at: datetime
    external_identifier: str | None = None
    title: str | None = None
    source_identifier: str | None = None
    media_type: str | None = None
    language: str | None = None
    published_at: datetime | None = None

    def __post_init__(self) -> None:
        required_values = {
            "connector_id": self.connector_id,
            "connector_version": self.connector_version,
            "location": self.location,
        }

        for field_name, value in required_values.items():
            if not value.strip():
                raise ValueError(
                    f"{field_name} must not be blank."
                )

        optional_values = {
            "external_identifier": self.external_identifier,
            "title": self.title,
            "source_identifier": self.source_identifier,
            "media_type": self.media_type,
            "language": self.language,
        }

        for field_name, value in optional_values.items():
            if value is not None and not value.strip():
                raise ValueError(
                    f"{field_name} must not be blank."
                )

        _validate_aware_datetime(
            self.discovered_at,
            "discovered_at",
        )
        _validate_aware_datetime(
            self.published_at,
            "published_at",
        )

    @property
    def fingerprint(self) -> str:
        """Return a deterministic fingerprint of discovery metadata."""

        published_at = (
            self.published_at.astimezone(
                timezone.utc
            ).isoformat()
            if self.published_at is not None
            else None
        )
        payload = dumps(
            {
                "connector_id": self.connector_id,
                "connector_version": self.connector_version,
                "external_identifier": self.external_identifier,
                "language": self.language,
                "location": self.location,
                "media_type": self.media_type,
                "published_at": published_at,
                "source_identifier": self.source_identifier,
                "title": self.title,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

        return sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    """Normalized result of one connector retrieval attempt."""

    candidate: CandidateRecord
    outcome: RetrievalOutcome
    retrieved_at: datetime
    resolved_location: str | None = None
    response_status: str | None = None
    content_type: str | None = None
    content: bytes | None = None
    error: str | None = None
    warnings: tuple[str, ...] = field(
        default_factory=tuple
    )

    def __post_init__(self) -> None:
        _validate_aware_datetime(
            self.retrieved_at,
            "retrieved_at",
        )

        if (
            self.outcome is RetrievalOutcome.SUCCEEDED
            and self.content is None
        ):
            raise ValueError(
                "successful retrieval must contain bytes."
            )

        if (
            self.outcome is not RetrievalOutcome.SUCCEEDED
            and self.content is not None
        ):
            raise ValueError(
                "unsuccessful retrieval must not contain bytes."
            )

        if self.error is not None and not self.error.strip():
            raise ValueError(
                "error must not be blank."
            )

        if (
            self.outcome is RetrievalOutcome.FAILED
            and self.error is None
        ):
            raise ValueError(
                "failed retrieval must include an error."
            )

        if any(
            not warning.strip()
            for warning in self.warnings
        ):
            raise ValueError(
                "warnings must not contain blank values."
            )

    @property
    def content_hash(self) -> str | None:
        """Return the SHA-256 hash of retrieved bytes when present."""

        if self.content is None:
            return None

        return sha256(self.content).hexdigest()
