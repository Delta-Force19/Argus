from argus.interface.cli import app
from argus.logging.logger import configure_logging


def main() -> None:
    configure_logging()
    app()


if __name__ == "__main__":
    main()