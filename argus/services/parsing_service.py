from argus.database import session_manager
from argus.logging.logger import get_logger
from argus.parsers.article_parser import extract_article_text
from argus.processing import (
    PARSING_METHOD_VERSION,
    ProcessingStage,
)
from argus.storage.article_repository import ArticleRepository
from argus.storage.processing_repository import (
    ProcessingStateRepository,
)


logger = get_logger(__name__)


def parse_articles(
        limit: int = 20,
        retry_failed: bool = False,
        newest_first: bool = False,
        after_article_id: int | None = None,
) -> None:


    parsed_count = 0
    failed_count = 0

    with session_manager.session() as session:
        article_repository = ArticleRepository(session)
        state_repository = ProcessingStateRepository(session)

        articles = article_repository.get_pending_parsing(
            limit=limit,
            retry_failed=retry_failed,
            newest_first=newest_first,
            after_article_id=after_article_id,
        )

        if not articles:
            logger.info(
                "No articles require parsing."
            )
            return

        for article in articles:
            state = state_repository.get_or_create(
                article_id=article.id,
                stage=ProcessingStage.PARSING,
                method_version=PARSING_METHOD_VERSION,
            )

            logger.info(
                "Parsing article %s: %s",
                article.id,
                article.title,
            )

            state_repository.mark_running(state)

            try:
                content = extract_article_text(
                    article.url
                )

                if not content:
                    raise RuntimeError(
                        "Article parser returned no content."
                    )

                article_repository.update_content(
                    article,
                    content,
                )

                state_repository.mark_done(state)

                parsed_count += 1

                logger.info(
                    "Article parsed: id=%s; characters=%s",
                    article.id,
                    len(content),
                )

            except Exception as error:
                session.rollback()

                state = state_repository.get_or_create(
                    article_id=article.id,
                    stage=ProcessingStage.PARSING,
                    method_version=PARSING_METHOD_VERSION,
                )

                state_repository.mark_failed(
                    state,
                    error=str(error),
                )

                failed_count += 1

                logger.exception(
                    "Article parsing failed: id=%s; url=%s",
                    article.id,
                    article.url,
                )

    logger.info(
        "Parsing finished; parsed=%s; failed=%s",
        parsed_count,
        failed_count,
    )
