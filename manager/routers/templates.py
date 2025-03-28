from starlette.templating import Jinja2Templates

from api.constants import templates_folder
from utils.objs import flatten

TEMPLATES = Jinja2Templates(templates_folder, extensions=['jinja2.ext.do', 'jinja2.ext.debug'])
TEMPLATES.env.filters['flatten'] = flatten
