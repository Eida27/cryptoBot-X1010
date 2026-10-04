import logging
from datetime import UTC, datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path


class DailyBoundedHandler(logging.Handler):
    def __init__(self, directory: Path) -> None:
        super().__init__()
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.date = ""
        self.writer: RotatingFileHandler | None = None

    def emit(self, record: logging.LogRecord) -> None:
        date = datetime.now(UTC).strftime("%Y-%m-%d")
        if date != self.date:
            if self.writer:
                self.writer.close()
            self.date = date
            self.writer = RotatingFileHandler(
                self.directory / f"bot-{date}.log",
                maxBytes=5 * 1048576,
                backupCount=3,
                encoding="utf-8",
            )
            self.writer.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            cutoff = datetime.now(UTC).date() - timedelta(days=14)
            for path in self.directory.glob("bot-????-??-??.log*"):
                try:
                    old = datetime.strptime(path.name[4:14], "%Y-%m-%d").date()
                except ValueError:
                    continue
                if old <= cutoff and path.is_file() and not path.is_symlink():
                    path.unlink()
        assert self.writer is not None
        self.writer.emit(record)

    def close(self) -> None:
        if self.writer:
            self.writer.close()
        super().close()


def configure_logging(directory: Path) -> None:
    logger = logging.getLogger("crypto_bot")
    logger.setLevel(logging.INFO)
    logger.addHandler(DailyBoundedHandler(directory))
    # SDK wire/debug logs can contain signed URLs; only sanitized application reasons are logged.
    for name in ("binance_common", "binance_sdk_derivatives_trading_usds_futures"):
        logging.getLogger(name).disabled = True
