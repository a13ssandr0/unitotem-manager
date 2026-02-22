import json
from functools import cache
from pathlib import Path
from platform import freedesktop_os_release as os_release, node as gethostname

import jinja2
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


files = {}

templates = Jinja2Templates(
        context_processors=[ctx_proc],
        env=jinja2.Environment(loader=jinja2.DictLoader(files), extensions=['jinja2.ext.do', 'jinja2.ext.debug']),
)
templates.env.filters['flatten'] = flatten

# files are loaded after creating the templates object to always remember that `files` can be changed at runtime
for file in ['base.html', 'first-boot.html.j2', 'no-assets.html.j2']:
    with open(Path(__file__).parent.joinpath(file).resolve()) as f:
        files[file] = f.read()


@cache
def load_vite_manifest():
    manifest_path = const.static_folder.joinpath('manifest.json').resolve()
    if manifest_path.exists():
        with open(manifest_path) as f:
            return json.load(f)
    return {}


@cache
def get_vite_assets(entry: str):
    vite_manifest = load_vite_manifest()
    assets = {"js": "", "css": []}

    if entry in vite_manifest:
        entry = vite_manifest[entry]
        for _import in entry.get('imports', []):
            assets['css'].extend(get_vite_assets(_import)['css'])
        assets["js"] = f"/{entry['file']}"
        assets["css"].extend([f"/{css}" for css in entry.get('css', [])])

    return assets
