__all__ = [
    "TEMPLATES",
    "UPLOADS"
]


from starlette.templating import Jinja2Templates

from .constants import templates_folder, uploads_folder
from .models import UploadManager

TEMPLATES = Jinja2Templates(templates_folder, extensions=['jinja2.ext.do'])

# noinspection PyTypeChecker
UPLOADS: UploadManager = None
