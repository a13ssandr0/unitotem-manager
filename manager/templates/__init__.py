from pathlib import Path

from starlette.templating import Jinja2Templates

from utils.objs import flatten

# noinspection PyTypeChecker
templates = Jinja2Templates(
    directory=Path(__file__).joinpath('../www').resolve(),
    extensions=['jinja2.ext.do', 'jinja2.ext.debug']
)
templates.env.filters['flatten'] = flatten

