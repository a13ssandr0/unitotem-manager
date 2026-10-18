"""Pages the server renders itself must be reachable from the web UI build, or Vite leaves them out and the page is blank."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_server_rendered_pages_are_routes():
    routes = (ROOT / 'webui/src/routes.js').read_text()
    for page in ('login', 'setup'):
        assert f"@/pages/{page}.vue" in routes, f'{page}.vue is not imported by routes.js: the build would not contain it'
