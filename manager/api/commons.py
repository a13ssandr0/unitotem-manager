__all__ = [
    "UPLOADS",
    "cmdargs",
    "SHUTDOWN_EVENT"
]

import asyncio

from .constants import Arguments, uploads_folder
from .models import UploadManager

UPLOADS = UploadManager(uploads_folder)

# noinspection PyTypeChecker
cmdargs: Arguments = None

SHUTDOWN_EVENT = asyncio.Event()
