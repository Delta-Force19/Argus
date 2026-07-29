from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class TextExtractionError(RuntimeError):
    """Raised when stored document bytes cannot produce usable text."""


@dataclass(frozen=True, slots=True)
class ExtractedText:
    """Normalized text and explicit limitations returned by an extractor."""

    text: str
    quality_limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("Extracted text must not be blank.")
        if any(not limitation.strip() for limitation in self.quality_limitations):
            raise ValueError("Quality limitations must not contain blanks.")


@runtime_checkable
class TextExtractor(Protocol):
    """Deterministically extract normalized text from stored bytes."""

    @property
    def method(self) -> str:
        ...

    @property
    def method_version(self) -> str:
        ...

    def extract(self, content: bytes, *, media_type: str | None) -> ExtractedText:
        ...
