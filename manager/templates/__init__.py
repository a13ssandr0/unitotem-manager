from pathlib import Path
from platform import freedesktop_os_release as os_release, node as gethostname

from starlette.templating import Jinja2Templates

import resources
import utils.constants as const
from utils.objs import flatten
from utils.system.network.ip import do_ip_addr


def ctx_proc(request):
    ip = do_ip_addr(True)
    return dict(
            R=resources,
            ut_vers=const.__version__,
            os_vers=os_release()['PRETTY_NAME'],
            ip_addr=ip['addr'][0]['addr'] if ip else None,
            hostname=gethostname(),
    )


# noinspection PyTypeChecker
templates = Jinja2Templates(
        directory=Path(__file__).joinpath('../www').resolve(),
        extensions=['jinja2.ext.do', 'jinja2.ext.debug'],
        context_processors=[ctx_proc],
)
templates.env.filters['flatten'] = flatten


# noinspection PyTypeChecker
templates_new = Jinja2Templates(
        directory=Path(__file__).joinpath('../www2').resolve(),
        extensions=['jinja2.ext.do', 'jinja2.ext.debug'],
        context_processors=[ctx_proc],
)
templates_new.env.filters['flatten'] = flatten
