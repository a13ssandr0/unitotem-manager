__all__ = [
    "TEMPLATES",
    "UPLOADS"
]


from starlette.templating import Jinja2Templates

from .constants import templates_folder
from .models import UploadManager
from .objs import flatten

TEMPLATES = Jinja2Templates(templates_folder, extensions=['jinja2.ext.do'])
TEMPLATES.env.filters['flatten'] = flatten

# noinspection PyTypeChecker
UPLOADS: UploadManager = None
