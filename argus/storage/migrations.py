from alembic import command
from alembic.config import Config

from argus.config import ALEMBIC_CONFIG_PATH


def upgrade_database() -> None:
    """Apply all pending database migrations."""

    config = Config(str(ALEMBIC_CONFIG_PATH))
    command.upgrade(config, "head")