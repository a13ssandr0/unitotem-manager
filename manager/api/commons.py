__all__ = [
    "UPLOADS",
    "cmdargs",
    "SHUTDOWN_EVENT"
]

import asyncio

from utils.constants import Arguments, uploads_folder
from utils.storage.uploadmanager import UploadManager

UPLOADS = UploadManager(uploads_folder)

# noinspection PyTypeChecker
cmdargs: Arguments = None

SHUTDOWN_EVENT = asyncio.Event()
