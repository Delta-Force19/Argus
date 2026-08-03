from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from argus.transcripts import TranscriptFormat, TranscriptKind


YOUTUBE_TRANSCRIPT_PROVIDER = "yt-dlp/youtube"
YOUTUBE_TRANSCRIPT_MEDIA_TYPE = "text/vtt; charset=utf-8"
YOUTUBE_HTTP_TIMEOUT = httpx.Timeout(
    connect=10.0,
    read=30.0,
    write=10.0,
    pool=10.0,
)
MAX_TRANSCRIPT_BYTES = 10 * 1024 * 1024
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "youtu.be",
}


class YouTubeTranscriptError(ValueError):
    """Raised when a YouTube transcript cannot be selected or retrieved."""


@dataclass(frozen=True, slots=True)
class YouTubeTranscriptTrack:
    track_id: str
    name: str
    transcript_kind: TranscriptKind
    transcript_format: TranscriptFormat
    media_type: str
    location: str
    request_headers: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class YouTubeTranscriptCatalog:
    requested_location: str
    canonical_location: str
    video_id: str
    title: str | None
    provider: str
    provider_version: str
    tracks: tuple[YouTubeTranscriptTrack, ...]


@dataclass(frozen=True, slots=True)
class RetrievedYouTubeTranscript:
    catalog: YouTubeTranscriptCatalog
    track: YouTubeTranscriptTrack
    content: bytes
    resolved_location: str
    retrieved_at: datetime


class YouTubeTranscriptSource:
    """Discover and retrieve exact WebVTT caption-track bytes via yt-dlp."""

    def __init__(
            self,
            *,
            info_loader: Callable[[str], Mapping[str, Any]] | None = None,
            provider_version: str | None = None,
            http_client: httpx.Client | None = None,
            clock: Callable[[], datetime] | None = None,
            maximum_bytes: int = MAX_TRANSCRIPT_BYTES,
    ) -> None:
        if maximum_bytes < 1:
            raise ValueError("maximum_bytes must be positive.")
        if info_loader is None:
            info_loader, detected_version = _yt_dlp_loader()
            provider_version = provider_version or detected_version
        if provider_version is None or not provider_version.strip():
            raise ValueError(
                "provider_version is required with a custom info_loader."
            )
        self._info_loader = info_loader
        self._provider_version = provider_version.strip()
        self._http_client = http_client
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._maximum_bytes = maximum_bytes

    def catalog(self, requested_location: str) -> YouTubeTranscriptCatalog:
        video_id = youtube_video_id(requested_location)
        canonical_location = (
            f"https://www.youtube.com/watch?v={video_id}"
        )
        try:
            info = self._info_loader(canonical_location)
        except YouTubeTranscriptError:
            raise
        except Exception as error:
            raise YouTubeTranscriptError(
                "yt-dlp could not inspect the YouTube video: "
                f"{error or error.__class__.__name__}"
            ) from error
        if not isinstance(info, Mapping):
            raise YouTubeTranscriptError(
                "yt-dlp returned no structured video metadata."
            )
        resolved_id = info.get("id")
        if resolved_id != video_id:
            raise YouTubeTranscriptError(
                "yt-dlp metadata identifies a different YouTube video."
            )
        title = info.get("title")
        tracks = tuple(_tracks(info))
        return YouTubeTranscriptCatalog(
            requested_location=requested_location.strip(),
            canonical_location=canonical_location,
            video_id=video_id,
            title=title.strip() if isinstance(title, str) and title.strip() else None,
            provider=YOUTUBE_TRANSCRIPT_PROVIDER,
            provider_version=self._provider_version,
            tracks=tracks,
        )

    def retrieve(
            self,
            requested_location: str,
            *,
            track_id: str,
            allow_auto_generated: bool = False,
    ) -> RetrievedYouTubeTranscript:
        catalog = self.catalog(requested_location)
        track = select_youtube_transcript_track(
            catalog,
            track_id=track_id,
            allow_auto_generated=allow_auto_generated,
        )
        client = self._http_client or httpx.Client()
        owns_client = self._http_client is None
        try:
            with client.stream(
                    "GET",
                    track.location,
                    follow_redirects=True,
                    timeout=YOUTUBE_HTTP_TIMEOUT,
                headers=(
                    dict(track.request_headers)
                    or {"User-Agent": "Argus/0.1.1"}
                ),
            ) as response:
                response.raise_for_status()
                chunks: list[bytes] = []
                byte_count = 0
                for chunk in response.iter_bytes():
                    byte_count += len(chunk)
                    if byte_count > self._maximum_bytes:
                        raise YouTubeTranscriptError(
                            "YouTube caption track exceeds the configured "
                            "size limit."
                        )
                    chunks.append(chunk)
                content = b"".join(chunks)
                resolved_location = str(response.url)
        except httpx.HTTPError as error:
            raise YouTubeTranscriptError(
                "YouTube caption-track retrieval failed: "
                f"{error or error.__class__.__name__}"
            ) from error
        finally:
            if owns_client:
                client.close()
        if not content:
            raise YouTubeTranscriptError(
                "YouTube returned an empty caption track."
            )
        retrieved_at = self._clock()
        if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
            raise YouTubeTranscriptError(
                "YouTube transcript retrieval clock must be timezone-aware."
            )
        return RetrievedYouTubeTranscript(
            catalog=catalog,
            track=track,
            content=content,
            resolved_location=resolved_location,
            retrieved_at=retrieved_at.astimezone(timezone.utc),
        )


def youtube_video_id(location: str) -> str:
    """Return an exact video id for supported, non-ambiguous YouTube URLs."""

    normalized = location.strip()
    if not normalized:
        raise YouTubeTranscriptError("YouTube URL must not be blank.")
    parsed = urlparse(normalized)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host not in _YOUTUBE_HOSTS:
        raise YouTubeTranscriptError(
            "Only explicit HTTPS youtube.com or youtu.be video URLs are "
            "supported."
        )
    candidate: str | None = None
    if host == "youtu.be":
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) == 1:
            candidate = parts[0]
    elif parsed.path == "/watch":
        values = parse_qs(parsed.query).get("v", [])
        if len(values) == 1:
            candidate = values[0]
    else:
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) == 2 and parts[0] in {"shorts", "live"}:
            candidate = parts[1]
    if candidate is None or not _VIDEO_ID.fullmatch(candidate):
        raise YouTubeTranscriptError(
            "YouTube URL must identify exactly one 11-character video id."
        )
    return candidate


def select_youtube_transcript_track(
        catalog: YouTubeTranscriptCatalog,
        *,
        track_id: str,
        allow_auto_generated: bool = False,
) -> YouTubeTranscriptTrack:
    """Select one exact track, preferring publisher tracks over automation."""

    normalized_id = track_id.strip()
    if not normalized_id:
        raise YouTubeTranscriptError("track_id must not be blank.")
    matches = [
        track for track in catalog.tracks
        if track.track_id == normalized_id
    ]
    publisher = [
        track for track in matches
        if track.transcript_kind is TranscriptKind.PUBLISHER_PROVIDED
    ]
    automatic = [
        track for track in matches
        if track.transcript_kind is TranscriptKind.AUTO_GENERATED
    ]
    if len(publisher) == 1:
        return publisher[0]
    if len(publisher) > 1:
        raise YouTubeTranscriptError(
            f"YouTube exposes multiple publisher WebVTT tracks for {normalized_id!r}."
        )
    if automatic and not allow_auto_generated:
        raise YouTubeTranscriptError(
            f"Track {normalized_id!r} is automatically generated; "
            "explicitly allow automatic captions to ingest it."
        )
    if len(automatic) == 1:
        return automatic[0]
    if len(automatic) > 1:
        raise YouTubeTranscriptError(
            f"YouTube exposes multiple automatic WebVTT tracks for {normalized_id!r}."
        )
    available = ", ".join(sorted({item.track_id for item in catalog.tracks}))
    suffix = f" Available track ids: {available}." if available else ""
    raise YouTubeTranscriptError(
        f"No supported WebVTT track has id {normalized_id!r}.{suffix}"
    )


def _tracks(info: Mapping[str, Any]) -> Sequence[YouTubeTranscriptTrack]:
    tracks: list[YouTubeTranscriptTrack] = []
    groups = (
        ("subtitles", TranscriptKind.PUBLISHER_PROVIDED),
        ("automatic_captions", TranscriptKind.AUTO_GENERATED),
    )
    for group_name, kind in groups:
        group = info.get(group_name)
        if not isinstance(group, Mapping):
            continue
        for track_id, formats in group.items():
            if not isinstance(track_id, str) or not isinstance(formats, list):
                continue
            for item in formats:
                if not isinstance(item, Mapping) or item.get("ext") != "vtt":
                    continue
                location = item.get("url")
                if not isinstance(location, str) or not location.strip():
                    continue
                name = item.get("name")
                tracks.append(YouTubeTranscriptTrack(
                    track_id=track_id,
                    name=(
                        name.strip()
                        if isinstance(name, str) and name.strip()
                        else track_id
                    ),
                    transcript_kind=kind,
                    transcript_format=TranscriptFormat.WEBVTT,
                    media_type=YOUTUBE_TRANSCRIPT_MEDIA_TYPE,
                    location=location,
                    request_headers=_request_headers(info, item),
                ))
    return sorted(
        tracks,
        key=lambda item: (
            item.track_id,
            item.transcript_kind is TranscriptKind.AUTO_GENERATED,
            item.name,
        ),
    )


def _request_headers(
        info: Mapping[str, Any],
        track: Mapping[str, Any],
) -> tuple[tuple[str, str], ...]:
    allowed = {
        "accept",
        "accept-language",
        "origin",
        "referer",
        "user-agent",
    }
    selected: dict[str, str] = {}
    for source in (info.get("http_headers"), track.get("http_headers")):
        if not isinstance(source, Mapping):
            continue
        for name, value in source.items():
            if (
                    isinstance(name, str)
                    and name.lower() in allowed
                    and isinstance(value, str)
                    and value.strip()
            ):
                selected[name] = value
    selected.setdefault("User-Agent", "Argus/0.1.1")
    return tuple(sorted(selected.items(), key=lambda item: item[0].lower()))


def _yt_dlp_loader() -> tuple[
        Callable[[str], Mapping[str, Any]],
        str,
]:
    try:
        from yt_dlp import YoutubeDL
        from yt_dlp.utils import DownloadError
        from yt_dlp.version import __version__
    except ImportError as error:
        raise YouTubeTranscriptError(
            "yt-dlp is required for YouTube transcript acquisition."
        ) from error

    def load(location: str) -> Mapping[str, Any]:
        options = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
        }
        try:
            with YoutubeDL(options) as downloader:
                info = downloader.extract_info(location, download=False)
        except DownloadError as error:
            raise YouTubeTranscriptError(
                "yt-dlp failed to resolve YouTube metadata. Verify network "
                "access, yt-dlp freshness, and its JavaScript runtime: "
                f"{error or error.__class__.__name__}"
            ) from error
        if not isinstance(info, Mapping):
            raise YouTubeTranscriptError(
                "yt-dlp returned no structured video metadata."
            )
        return info

    return load, __version__
