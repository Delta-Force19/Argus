import re
from importlib.metadata import version

import trafilatura

from argus.extraction import ExtractedText, TextExtractionError


class TrafilaturaTextExtractor:
    """Extract main text from previously retrieved HTML bytes."""

    method = "trafilatura"
    method_version = version("trafilatura")

    _HTML_MEDIA_TYPES = frozenset({
        "text/html",
        "application/xhtml+xml",
    })

    def extract(self, content: bytes, *, media_type: str | None) -> ExtractedText:
        normalized_media_type = self._normalize_media_type(media_type)
        if (
                normalized_media_type is not None
                and normalized_media_type not in self._HTML_MEDIA_TYPES
        ):
            raise TextExtractionError(
                f"Trafilatura does not support media type {media_type!r}."
            )

        text = trafilatura.extract(content)
        if text is None:
            raise TextExtractionError(
                "Trafilatura returned no text for the document bytes."
            )

        normalized_text = self._normalize_text(text)
        if not normalized_text:
            raise TextExtractionError(
                "Trafilatura returned only blank text."
            )

        return ExtractedText(
            text=normalized_text,
            quality_limitations=(
                "Main-text and boilerplate detection are heuristic.",
            ),
        )

    @staticmethod
    def _normalize_media_type(media_type: str | None) -> str | None:
        if media_type is None:
            return None
        normalized = media_type.partition(";")[0].strip().lower()
        return normalized or None

    @staticmethod
    def _normalize_text(text: str) -> str:
        lines = [
            re.sub(r"[\t ]+", " ", line).strip()
            for line in text.splitlines()
        ]
        normalized_lines: list[str] = []
        previous_blank = True
        for line in lines:
            if line:
                normalized_lines.append(line)
                previous_blank = False
            elif not previous_blank:
                normalized_lines.append("")
                previous_blank = True
        return "\n".join(normalized_lines).strip()
