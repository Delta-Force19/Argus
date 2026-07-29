from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import case, select
from sqlalchemy.orm import Session

from argus.database import SessionLocal
from argus.models import Article, ProcessingState, Source
from argus.processing import (
    PARSING_METHOD_VERSION,
    ProcessingStage,
)
from argus.text_quality import is_probably_readable_text


@dataclass(frozen=True, slots=True)
class LatestNewsItem:
    """Detached presentation data for one collected article."""

    article_id: int
    published_at: datetime | None
    fetched_at: datetime
    source: str
    title: str
    url: str
    language: str | None
    parsing_status: str
    excerpt_source: str | None
    excerpt: str | None


@dataclass(frozen=True, slots=True)
class LatestNewsReport:
    """Bounded, read-only chronological view of collected articles."""

    items: tuple[LatestNewsItem, ...]

    @property
    def content_count(self) -> int:
        return sum(
            item.excerpt_source == "content"
            for item in self.items
        )

    @property
    def summary_count(self) -> int:
        return sum(
            item.excerpt_source == "summary"
            for item in self.items
        )

    @property
    def headline_only_count(self) -> int:
        return sum(
            item.excerpt_source is None
            for item in self.items
        )


def get_latest_news(
        *,
        limit: int = 20,
        excerpt_chars: int = 240,
        session_factory: Callable[[], Session] = SessionLocal,
) -> LatestNewsReport:
    """Return recent articles without ranking or modifying stored state."""

    if limit < 1:
        raise ValueError("limit must be greater than zero.")
    if excerpt_chars < 40:
        raise ValueError("excerpt_chars must be at least 40.")

    statement = (
        select(Article, Source.name, ProcessingState.status)
        .outerjoin(Source, Source.id == Article.source_id)
        .outerjoin(
            ProcessingState,
            (
                (ProcessingState.article_id == Article.id)
                & (
                    ProcessingState.stage
                    == ProcessingStage.PARSING
                )
                & (
                    ProcessingState.method_version
                    == PARSING_METHOD_VERSION
                )
            ),
        )
        .order_by(
            case(
                (Article.published_at.is_(None), 1),
                else_=0,
            ).asc(),
            Article.published_at.desc(),
            Article.fetched_at.desc(),
            Article.id.desc(),
        )
        .limit(limit)
    )

    with session_factory() as session:
        rows = session.execute(statement).all()

    return LatestNewsReport(
        items=tuple(
            _to_item(
                article=article,
                normalized_source=normalized_source,
                processing_status=processing_status,
                excerpt_chars=excerpt_chars,
            )
            for article, normalized_source, processing_status in rows
        )
    )


def get_news_after_article_id(
        *,
        after_article_id: int,
        limit: int = 20,
        excerpt_chars: int = 240,
        session_factory: Callable[[], Session] = SessionLocal,
) -> LatestNewsReport:
    """Return the oldest bounded ingestion slice after a delivery cursor."""

    if after_article_id < 0:
        raise ValueError("after_article_id must not be negative.")
    if limit < 1:
        raise ValueError("limit must be greater than zero.")
    if excerpt_chars < 40:
        raise ValueError("excerpt_chars must be at least 40.")

    statement = (
        select(Article, Source.name, ProcessingState.status)
        .outerjoin(Source, Source.id == Article.source_id)
        .outerjoin(
            ProcessingState,
            (
                (ProcessingState.article_id == Article.id)
                & (
                    ProcessingState.stage
                    == ProcessingStage.PARSING
                )
                & (
                    ProcessingState.method_version
                    == PARSING_METHOD_VERSION
                )
            ),
        )
        .where(Article.id > after_article_id)
        .order_by(Article.id.asc())
        .limit(limit)
    )

    with session_factory() as session:
        rows = session.execute(statement).all()

    return LatestNewsReport(
        items=tuple(
            _to_item(
                article=article,
                normalized_source=normalized_source,
                processing_status=processing_status,
                excerpt_chars=excerpt_chars,
            )
            for article, normalized_source, processing_status in rows
        )
    )


def get_highest_article_id(
        *,
        session_factory: Callable[[], Session] = SessionLocal,
) -> int:
    """Return the current ingestion boundary without modifying state."""

    statement = select(Article.id).order_by(Article.id.desc()).limit(1)
    with session_factory() as session:
        article_id = session.scalar(statement)
    return article_id or 0


def _to_item(
        *,
        article: Article,
        normalized_source: str | None,
        processing_status: object | None,
        excerpt_chars: int,
) -> LatestNewsItem:
    excerpt_source, excerpt = _article_excerpt(
        article,
        excerpt_chars=excerpt_chars,
    )
    if processing_status is not None:
        parsing_status = str(processing_status.value)
    elif article.content and article.content.strip():
        parsing_status = "available"
    else:
        parsing_status = "not_started"

    return LatestNewsItem(
        article_id=article.id,
        published_at=article.published_at,
        fetched_at=article.fetched_at,
        source=(
            _clean_text(normalized_source)
            or _clean_text(article.source)
            or "unknown"
        ),
        title=_clean_text(article.title) or "untitled",
        url=article.url,
        language=article.language,
        parsing_status=parsing_status,
        excerpt_source=excerpt_source,
        excerpt=excerpt,
    )


def _article_excerpt(
        article: Article,
        *,
        excerpt_chars: int,
) -> tuple[str | None, str | None]:
    for source_name, value in (
        ("content", article.content),
        ("summary", article.summary),
    ):
        cleaned = _clean_text(value)
        if cleaned:
            return source_name, _truncate(cleaned, excerpt_chars)
    return None, None


def _clean_text(value: str | None) -> str | None:
    if not is_probably_readable_text(value):
        return None
    cleaned = " ".join(value.split())
    return cleaned or None


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit - 1].rstrip()}…"
