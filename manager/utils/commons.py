__all__ = [
    "TEMPLATES",
    "UPLOADS",
    "cmdargs",
    "SHUTDOWN_EVENT"
]

import asyncio

from starlette.templating import Jinja2Templates

from .constants import templates_folder, Arguments, uploads_folder
from .models import UploadManager
from .objs import flatten

TEMPLATES = Jinja2Templates(templates_folder, extensions=['jinja2.ext.do'])
TEMPLATES.env.filters['flatten'] = flatten

UPLOADS = UploadManager(uploads_folder)

# noinspection PyTypeChecker
cmdargs: Arguments = None


SHUTDOWN_EVENT = asyncio.Event()
