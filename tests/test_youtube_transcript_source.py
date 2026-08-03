from datetime import datetime, timezone
import unittest

import httpx

from argus.transcript_sources.youtube import (
    YouTubeTranscriptError,
    YouTubeTranscriptSource,
    select_youtube_transcript_track,
    youtube_video_id,
)
from argus.transcripts import TranscriptKind


VIDEO_ID = "dcmdgYtPeTg"
VIDEO_URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"


def _info():
    return {
        "id": VIDEO_ID,
        "title": "Latest news bulletin",
        "subtitles": {
            "en": [
                {"ext": "json3", "url": "https://captions.test/en.json3"},
                {
                    "ext": "vtt",
                    "url": "https://captions.test/manual-en.vtt",
                    "name": "English",
                },
            ],
        },
        "automatic_captions": {
            "en": [{
                "ext": "vtt",
                "url": "https://captions.test/auto-en.vtt",
                "name": "English (auto-generated)",
            }],
            "en-orig": [{
                "ext": "vtt",
                "url": "https://captions.test/en-orig.vtt",
            }],
        },
    }


class YouTubeTranscriptSourceTests(unittest.TestCase):
    def test_accepts_supported_video_url_forms(self) -> None:
        self.assertEqual(youtube_video_id(VIDEO_URL), VIDEO_ID)
        self.assertEqual(
            youtube_video_id(f"https://youtu.be/{VIDEO_ID}?feature=share"),
            VIDEO_ID,
        )
        self.assertEqual(
            youtube_video_id(f"https://www.youtube.com/shorts/{VIDEO_ID}"),
            VIDEO_ID,
        )

    def test_rejects_non_youtube_and_ambiguous_urls(self) -> None:
        for location in (
            f"https://youtube.example/watch?v={VIDEO_ID}",
            f"http://www.youtube.com/watch?v={VIDEO_ID}",
            "https://www.youtube.com/watch?v=short",
            "https://www.youtube.com/channel/example",
        ):
            with self.subTest(location=location):
                with self.assertRaises(YouTubeTranscriptError):
                    youtube_video_id(location)

    def test_catalog_preserves_exact_track_ids_and_supported_vtt_only(self) -> None:
        source = YouTubeTranscriptSource(
            info_loader=lambda _: _info(),
            provider_version="2026.7.4",
        )

        catalog = source.catalog(VIDEO_URL)

        self.assertEqual(catalog.video_id, VIDEO_ID)
        self.assertEqual([track.track_id for track in catalog.tracks], [
            "en", "en", "en-orig",
        ])
        self.assertEqual(
            catalog.tracks[0].transcript_kind,
            TranscriptKind.PUBLISHER_PROVIDED,
        )

    def test_selection_prefers_publisher_track(self) -> None:
        source = YouTubeTranscriptSource(
            info_loader=lambda _: _info(),
            provider_version="2026.7.4",
        )
        catalog = source.catalog(VIDEO_URL)

        selected = select_youtube_transcript_track(
            catalog,
            track_id="en",
        )

        self.assertEqual(
            selected.location, "https://captions.test/manual-en.vtt"
        )

    def test_automatic_track_requires_explicit_permission(self) -> None:
        source = YouTubeTranscriptSource(
            info_loader=lambda _: _info(),
            provider_version="2026.7.4",
        )
        catalog = source.catalog(VIDEO_URL)

        with self.assertRaisesRegex(
                YouTubeTranscriptError, "automatically generated"):
            select_youtube_transcript_track(
                catalog,
                track_id="en-orig",
            )
        selected = select_youtube_transcript_track(
            catalog,
            track_id="en-orig",
            allow_auto_generated=True,
        )
        self.assertEqual(selected.track_id, "en-orig")

    def test_retrieves_exact_bytes_and_records_final_location(self) -> None:
        content = b"WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nBulletin.\n"
        now = datetime(2026, 8, 3, 20, 0, tzinfo=timezone.utc)
        client = httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                content=content,
                request=request,
                headers={"content-type": "text/vtt"},
            )
        ))
        source = YouTubeTranscriptSource(
            info_loader=lambda _: _info(),
            provider_version="2026.7.4",
            http_client=client,
            clock=lambda: now,
        )

        retrieved = source.retrieve(VIDEO_URL, track_id="en")

        self.assertEqual(retrieved.content, content)
        self.assertEqual(
            retrieved.resolved_location,
            "https://captions.test/manual-en.vtt",
        )
        self.assertEqual(retrieved.retrieved_at, now)
        client.close()

    def test_rejects_oversized_caption_track(self) -> None:
        client = httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, content=b"12345", request=request
            )
        ))
        source = YouTubeTranscriptSource(
            info_loader=lambda _: _info(),
            provider_version="2026.7.4",
            http_client=client,
            maximum_bytes=4,
        )

        with self.assertRaisesRegex(YouTubeTranscriptError, "size limit"):
            source.retrieve(VIDEO_URL, track_id="en")
        client.close()

    def test_rejects_metadata_for_another_video(self) -> None:
        info = {**_info(), "id": "abcdefghijk"}
        source = YouTubeTranscriptSource(
            info_loader=lambda _: info,
            provider_version="2026.7.4",
        )

        with self.assertRaisesRegex(YouTubeTranscriptError, "different"):
            source.catalog(VIDEO_URL)


if __name__ == "__main__":
    unittest.main()
