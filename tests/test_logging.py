import logging
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from argus.logging.logger import (
    ARGUS_LOGGER_NAME,
    CONSOLE_HANDLER_NAME,
    FILE_HANDLER_NAME,
    configure_logging,
)


class LoggingConfigurationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.application_logger = logging.getLogger(
            ARGUS_LOGGER_NAME
        )
        self.original_handlers = list(
            self.application_logger.handlers
        )
        self.original_level = self.application_logger.level
        self.original_disabled = (
            self.application_logger.disabled
        )
        self.original_propagate = (
            self.application_logger.propagate
        )
        self.application_logger.handlers.clear()

        self.root_logger = logging.getLogger()
        self.root_handler = logging.NullHandler()
        self.root_logger.addHandler(self.root_handler)

        self.temporary_directory = TemporaryDirectory()
        self.log_directory = Path(
            self.temporary_directory.name
        )
        self.log_file = self.log_directory / "argus.log"

        self.directory_patch = patch(
            "argus.logging.logger.LOG_DIRECTORY",
            self.log_directory,
        )
        self.file_patch = patch(
            "argus.logging.logger.LOG_FILE",
            self.log_file,
        )
        self.directory_patch.start()
        self.file_patch.start()

    def tearDown(self) -> None:
        self.directory_patch.stop()
        self.file_patch.stop()
        self.root_logger.removeHandler(
            self.root_handler
        )

        for handler in self.application_logger.handlers:
            handler.close()

        self.application_logger.handlers = (
            self.original_handlers
        )
        self.application_logger.setLevel(
            self.original_level
        )
        self.application_logger.disabled = (
            self.original_disabled
        )
        self.application_logger.propagate = (
            self.original_propagate
        )
        self.temporary_directory.cleanup()

    def test_configures_argus_when_root_has_handler(
        self,
    ) -> None:
        configure_logging()

        handler_names = {
            handler.get_name()
            for handler in self.application_logger.handlers
        }

        self.assertEqual(
            handler_names,
            {
                CONSOLE_HANDLER_NAME,
                FILE_HANDLER_NAME,
            },
        )
        self.assertFalse(
            self.application_logger.propagate
        )

        self.application_logger.info(
            "collection completed"
        )

        self.assertIn(
            "collection completed",
            self.log_file.read_text(encoding="utf-8"),
        )

    def test_configuration_is_idempotent(self) -> None:
        configure_logging()
        configure_logging()

        self.assertEqual(
            len(self.application_logger.handlers),
            2,
        )

    def test_reenables_loggers_disabled_by_alembic(
        self,
    ) -> None:
        child_logger = logging.getLogger(
            "argus.services.collection_service"
        )
        original_child_disabled = child_logger.disabled

        try:
            self.application_logger.disabled = True
            child_logger.disabled = True

            configure_logging()

            self.assertFalse(
                self.application_logger.disabled
            )
            self.assertFalse(child_logger.disabled)

            child_logger.info("feed collection visible")

            self.assertIn(
                "feed collection visible",
                self.log_file.read_text(
                    encoding="utf-8"
                ),
            )
        finally:
            child_logger.disabled = (
                original_child_disabled
            )


if __name__ == "__main__":
    unittest.main()
