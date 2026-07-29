from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import feedparser
import httpx

from argus.acquisition import (
    CandidateRecord,
    DiscoveryRequest,
    RetrievalOutcome,
    RetrievalResult,
)
from argus.config import RSSFeedConfig


RSS_CONNECTOR_ID = "rss"
RSS_CONNECTOR_VERSION = "1.0.0"
HTTP_TIMEOUT = httpx.Timeout(
    connect=5.0,
    read=10.0,
    write=10.0,
    pool=5.0,
)
HTTP_HEADERS = {
    "User-Agent": "Argus/0.1.1",
}


class RSSDiscoveryAccessRestricted(RuntimeError):
    """Raised when an RSS endpoint rejects automated discovery."""

    def __init__(
        self,
        *,
        url: str,
        status_code: int,
    ) -> None:
        self.url = url
        self.status_code = status_code
        super().__init__(
            f"RSS discovery access restricted with HTTP "
            f"{status_code}: {url}"
        )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None

    normalized = value.strip()
    return normalized or None


def _parse_entry_datetime(
    entry: Mapping[str, Any],
) -> datetime | None:
    parsed_time = (
        entry.get("published_parsed")
        or entry.get("updated_parsed")
    )

    if parsed_time is not None:
        return datetime(
            *parsed_time[:6],
            tzinfo=timezone.utc,
        )

    raw_time = (
        entry.get("published")
        or entry.get("updated")
    )

    if not raw_time:
        return None

    try:
        parsed_datetime = parsedate_to_datetime(
            raw_time
        )
    except (TypeError, ValueError):
        return None

    if (
        parsed_datetime.tzinfo is None
        or parsed_datetime.utcoffset() is None
    ):
        return None

    return parsed_datetime.astimezone(timezone.utc)


class RSSConnector:
    """Discover and retrieve documents exposed through one RSS feed."""

    def __init__(
        self,
        feed: RSSFeedConfig,
        *,
        clock: Callable[[], datetime] = _utc_now,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._feed = feed
        self._clock = clock
        self._http_client = http_client

    @property
    def connector_id(self) -> str:
        return RSS_CONNECTOR_ID

    @property
    def connector_version(self) -> str:
        return RSS_CONNECTOR_VERSION

    def discover(
        self,
        request: DiscoveryRequest,
    ) -> list[CandidateRecord]:
        if request.cursor is not None:
            raise ValueError(
                "RSS connector does not support cursors."
            )

        if (
            request.languages
            and self._feed.language not in request.languages
        ):
            return []

        http_get = (
            self._http_client.get
            if self._http_client is not None
            else httpx.get
        )
        response = http_get(
            self._feed.url,
            follow_redirects=True,
            timeout=HTTP_TIMEOUT,
            headers=HTTP_HEADERS,
        )

        if response.status_code in {401, 403}:
            raise RSSDiscoveryAccessRestricted(
                url=str(response.url),
                status_code=response.status_code,
            )

        response.raise_for_status()

        parsed_feed = feedparser.parse(response.content)
        discovered_at = self._clock()
        candidates: list[CandidateRecord] = []

        for entry in parsed_feed.entries:
            candidate = self._candidate_from_entry(
                entry=entry,
                discovered_at=discovered_at,
            )

            if candidate is None:
                continue

            if not self._matches_request(
                candidate,
                request,
            ):
                continue

            candidates.append(candidate)

            if (
                request.limit is not None
                and len(candidates) >= request.limit
            ):
                break

        return candidates

    def retrieve(
        self,
        candidate: CandidateRecord,
    ) -> RetrievalResult:
        self._validate_candidate(candidate)
        retrieved_at = self._clock()
        http_get = (
            self._http_client.get
            if self._http_client is not None
            else httpx.get
        )

        try:
            response = http_get(
                candidate.location,
                follow_redirects=True,
                timeout=HTTP_TIMEOUT,
                headers=HTTP_HEADERS,
            )
        except httpx.HTTPError as error:
            return RetrievalResult(
                candidate=candidate,
                outcome=RetrievalOutcome.FAILED,
                retrieved_at=retrieved_at,
                error=(
                    str(error)
                    or error.__class__.__name__
                ),
            )

        outcome = self._outcome_for_status(
            response.status_code
        )
        error = None

        if outcome is RetrievalOutcome.FAILED:
            error = (
                f"HTTP request returned status "
                f"{response.status_code}."
            )

        return RetrievalResult(
            candidate=candidate,
            outcome=outcome,
            retrieved_at=retrieved_at,
            resolved_location=str(response.url),
            response_status=str(response.status_code),
            content_type=response.headers.get(
                "content-type"
            ),
            content=(
                response.content
                if outcome is RetrievalOutcome.SUCCEEDED
                else None
            ),
            error=error,
        )

    def _candidate_from_entry(
        self,
        *,
        entry: Mapping[str, Any],
        discovered_at: datetime,
    ) -> CandidateRecord | None:
        location = entry.get("link")

        if (
            not isinstance(location, str)
            or not location.strip()
        ):
            return None

        return CandidateRecord(
            connector_id=self.connector_id,
            connector_version=self.connector_version,
            location=location,
            discovered_at=discovered_at,
            external_identifier=(
                _optional_text(entry.get("id"))
                or location
            ),
            title=_optional_text(entry.get("title")),
            source_identifier=(
                self._feed.effective_source_identifier
            ),
            language=self._feed.language,
            published_at=_parse_entry_datetime(entry),
        )

    @staticmethod
    def _matches_request(
        candidate: CandidateRecord,
        request: DiscoveryRequest,
    ) -> bool:
        if request.query is not None:
            title = candidate.title or ""

            if request.query.casefold() not in title.casefold():
                return False

        if request.published_from is not None:
            if (
                candidate.published_at is None
                or candidate.published_at < request.published_from
            ):
                return False

        if request.published_until is not None:
            if (
                candidate.published_at is None
                or candidate.published_at > request.published_until
            ):
                return False

        return True

    def _validate_candidate(
        self,
        candidate: CandidateRecord,
    ) -> None:
        if candidate.connector_id != self.connector_id:
            raise ValueError(
                "Candidate belongs to another connector."
            )

        if (
            candidate.connector_version
            != self.connector_version
        ):
            raise ValueError(
                "Candidate uses another connector version."
            )

    @staticmethod
    def _outcome_for_status(
        status_code: int,
    ) -> RetrievalOutcome:
        if 200 <= status_code < 300:
            return RetrievalOutcome.SUCCEEDED

        if status_code in {401, 403}:
            return RetrievalOutcome.ACCESS_RESTRICTED

        if status_code in {404, 410}:
            return RetrievalOutcome.UNAVAILABLE

        return RetrievalOutcome.FAILED
