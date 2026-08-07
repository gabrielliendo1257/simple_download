from simple_downloader.engines.telegram.client import (
    STATUS_AUTHENTICATED,
    STATUS_AUTH_REQUIRED,
    TelegramClientProvider,
    TelegramLoginNeedsPasswordError,
    TelegramNotAuthorizedError,
)
from simple_downloader.engines.telegram.engine import TelegramEngine
from simple_downloader.engines.telegram.links import TelegramLink, parse_link
from simple_downloader.engines.telegram.task import TelegramDownloadTask

__all__ = [
    "TelegramClientProvider",
    "TelegramDownloadTask",
    "TelegramEngine",
    "TelegramLink",
    "TelegramLoginNeedsPasswordError",
    "TelegramNotAuthorizedError",
    "parse_link",
]
