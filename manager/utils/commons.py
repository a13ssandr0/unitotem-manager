__all__ = [
    "TEMPLATES",
    "UPLOADS",
    "cmdargs",
    "SHUTDOWN_EVENT"
]

import asyncio
from typing import Mapping, List, Tuple, Any

from jinja2.filters import K, V, ignore_case
from starlette.templating import Jinja2Templates

from .constants import templates_folder, Arguments, uploads_folder
from .models import UploadManager
from .objs import flatten


def do_dictsort(
    value: Mapping[K, V],
    case_sensitive: bool = False,
    reverse: bool = False,
) -> List[Tuple[K, V]]:

    def sort_func(item: Tuple[Any, Any]) -> Any:
        value = item[0]

        if not case_sensitive and isinstance(value, str):
            value = value.lower()

        return value

    return sorted(value.items(), key=sort_func, reverse=reverse)




TEMPLATES = Jinja2Templates(templates_folder, extensions=['jinja2.ext.do', 'jinja2.ext.debug'])
TEMPLATES.env.filters['flatten'] = flatten
TEMPLATES.env.filters['dictsort2'] = do_dictsort

UPLOADS = UploadManager(uploads_folder)

# noinspection PyTypeChecker
cmdargs: Arguments = None


SHUTDOWN_EVENT = asyncio.Event()
