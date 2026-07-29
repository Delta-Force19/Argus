from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from argus.models import Article, ProcessingState
from argus.processing import (
    PARSING_METHOD_VERSION,
    ProcessingStage,
    ProcessingStatus,
)
from argus.storage.base_repository import BaseRepository


class ArticleRepository(BaseRepository[Article]):
    def __init__(self, session: Session) -> None:
        super().__init__(
            session=session,
            model_type=Article,
        )

    def save(
            self,
            article: Article,
    ) -> Article:
        self.add(article)

        try:
            self.session.commit()
            self.refresh(article)
            return article

        except IntegrityError:
            self.session.rollback()

            existing_article = self.get_by_url(
                article.url
            )

            if existing_article is None:
                raise

            return existing_article

    def get_by_url(
            self,
            url: str,
    ) -> Article | None:
        statement = (
            select(Article)
            .where(Article.url == url)
        )

        return self.session.scalar(statement)

    def get_latest(
            self,
            limit: int = 20,
    ) -> list[Article]:
        statement = (
            select(Article)
            .order_by(Article.fetched_at.desc())
            .limit(limit)
        )

        return list(
            self.session.scalars(statement).all()
        )

    def update_content(
            self,
            article: Article,
            content: str,
    ) -> Article:
        article.content = content

        self.session.commit()
        self.refresh(article)

        return article

    def get_pending_parsing(
            self,
            limit: int = 20,
            retry_failed: bool = False,
            newest_first: bool = False,
            after_article_id: int | None = None,
    ) -> list[Article]:
        blocked_statuses: list[ProcessingStatus] = [
            ProcessingStatus.RUNNING,
            ProcessingStatus.DONE,
        ]

        if not retry_failed:
            blocked_statuses.append(
                ProcessingStatus.FAILED
            )

        blocking_state_exists = exists().where(
            ProcessingState.article_id == Article.id,
            ProcessingState.stage == (
                ProcessingStage.PARSING
            ),
            ProcessingState.method_version == (
                PARSING_METHOD_VERSION
            ),
            ProcessingState.status.in_(
                blocked_statuses
            ),
            )

        statement = (
            select(Article)
            .where(Article.content.is_(None))
            .where(~blocking_state_exists)
        )
        if after_article_id is not None:
            if after_article_id < 0:
                raise ValueError(
                    "after_article_id must not be negative."
                )
            statement = statement.where(
                Article.id > after_article_id
            ).order_by(Article.id.asc())
        elif newest_first:
            statement = statement.order_by(
                Article.published_at.is_(None).asc(),
                Article.published_at.desc(),
                Article.fetched_at.desc(),
                Article.id.desc(),
            )
        else:
            statement = statement.order_by(
                Article.fetched_at.asc(),
                Article.id.asc(),
            )
        statement = statement.limit(limit)

        return list(
            self.session.scalars(statement).all()
        )

    def get_latest_with_content(
            self,
            limit: int = 1,
    ) -> list[Article]:
        statement = (
            select(Article)
            .where(Article.content.is_not(None))
            .where(Article.content != "")
            .order_by(Article.fetched_at.desc())
            .limit(limit)
        )

        return list(
            self.session.scalars(statement).all()
        )
