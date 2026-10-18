"""Pages the server renders itself must be reachable from the web UI build, or Vite leaves them out and the page is blank."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_server_rendered_pages_are_routes():
    routes = (ROOT / 'webui/src/routes.js').read_text()
    for page in ('login', 'setup'):
        assert f"@/pages/{page}.vue" in routes, f'{page}.vue is not imported by routes.js: the build would not contain it'


def test_no_cached_hostname():
    """platform.node() is cached for the life of the process: a hostname change would never show on a page."""
    import re
    for path in (ROOT / 'manager').rglob('*.py'):
        if 'tests' in path.parts:
            continue
        text = path.read_text()
        assert not re.search(r'from platform import[^\n]*\bnode\b', text) and 'platform.node(' not in text, path
