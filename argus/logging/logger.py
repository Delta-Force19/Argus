import logging

from argus.config import LOG_DIRECTORY, LOG_FILE


LOG_FORMAT = (
    "%(asctime)s | %(levelname)-8s | "
    "%(name)s | %(message)s"
)
ARGUS_LOGGER_NAME = "argus"
CONSOLE_HANDLER_NAME = "argus.console"
FILE_HANDLER_NAME = "argus.file"


def configure_logging(
    level: int = logging.INFO,
) -> None:
    """Configure Argus console and file logging."""

    LOG_DIRECTORY.mkdir(
        parents=True,
        exist_ok=True,
    )

    application_logger = logging.getLogger(
        ARGUS_LOGGER_NAME
    )
    application_logger.disabled = False
    application_logger.setLevel(level)
    application_logger.propagate = False

    for logger_name, logger in (
        logging.root.manager.loggerDict.items()
    ):
        if (
            logger_name.startswith(
                f"{ARGUS_LOGGER_NAME}."
            )
            and isinstance(logger, logging.Logger)
        ):
            logger.disabled = False

    formatter = logging.Formatter(
        LOG_FORMAT,
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    handlers_by_name = {
        handler.get_name(): handler
        for handler in application_logger.handlers
    }

    console_handler = handlers_by_name.get(
        CONSOLE_HANDLER_NAME
    )

    if console_handler is None:
        console_handler = logging.StreamHandler()
        console_handler.set_name(
            CONSOLE_HANDLER_NAME
        )
        console_handler.setFormatter(formatter)
        application_logger.addHandler(
            console_handler
        )

    console_handler.setLevel(level)

    file_handler = handlers_by_name.get(
        FILE_HANDLER_NAME
    )

    if file_handler is None:
        file_handler = logging.FileHandler(
            LOG_FILE,
            encoding="utf-8",
        )
        file_handler.set_name(FILE_HANDLER_NAME)
        file_handler.setFormatter(formatter)
        application_logger.addHandler(file_handler)

    file_handler.setLevel(level)


def get_logger(name: str) -> logging.Logger:
    """Return a named Argus logger."""

    return logging.getLogger(name)
