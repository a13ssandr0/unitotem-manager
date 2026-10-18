import json
from functools import cache
from pathlib import Path
from platform import freedesktop_os_release as os_release, node as gethostname

import jinja2
from starlette.templating import Jinja2Templates

import resources
import utils.constants as const
from utils.objs import flatten
from utils.system.ip import address_text


def ctx_proc(request):
    return dict(
            R=resources,
            ut_vers=const.__version__,
            os_vers=os_release()['PRETTY_NAME'],
            ip_addr=address_text(),
            hostname=gethostname(),
    )


files = {}

templates = Jinja2Templates(
        context_processors=[ctx_proc],
        env=jinja2.Environment(loader=jinja2.DictLoader(files), extensions=['jinja2.ext.do', 'jinja2.ext.debug']),
)
templates.env.filters['flatten'] = flatten

# files are loaded after creating the templates object to always remember that `files` can be changed at runtime
for file in ['base.html', 'first-boot.html.j2', 'no-assets.html.j2', 'errors/error.html.j2']:
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
def get_vite_assets(entry: str, dev: bool = False):
    assets = {"js": "", "css": [], "dev": False}

    # Development: the request came through the Vite dev server (see webui/vite.config.mjs), which marks
    # it with X-Unitotem-Vite-Dev and proxies everything else to this backend, so the entry is referenced by source path and HMR works.
    if dev:
        assets.update(js=f"/{entry}", dev=True)
        return assets

    vite_manifest = load_vite_manifest()

    if entry in vite_manifest:
        entry = vite_manifest[entry]
        for _import in entry.get('imports', []):
            assets['css'].extend(get_vite_assets(_import)['css'])
        assets["js"] = f"/{entry['file']}"
        assets["css"].extend([f"/{css}" for css in entry.get('css', [])])

    return assets


def is_vite_dev(request) -> bool:
    return request.headers.get('x-unitotem-vite-dev') == '1'


@cache
def public_asset_files() -> frozenset[str]:
    """
    Built JS files the login page needs, i.e. the import closure of its entry (relative to the
    static folder, e.g. 'assets/login-xxx.js'). Every other JS chunk is a page of the authenticated
    app and is only served to a logged-in session.
    """
    manifest = load_vite_manifest()
    seen: set[str] = set()

    def walk(key: str):
        if key in seen or key not in manifest:
            return
        seen.add(key)
        for _import in manifest[key].get('imports', []):
            walk(_import)

    walk('src/pages/login.vue')
    walk('src/pages/setup.vue')
    return frozenset(manifest[key]['file'] for key in seen)
