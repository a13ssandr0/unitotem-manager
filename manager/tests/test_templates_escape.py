"""Automatic escaping of the HTML pages must change nothing for ordinary values and neutralise markup in data."""
import jinja2

from templates import files, templates

PAGES = ['base.html', 'first-boot.html.j2', 'no-assets.html.j2', 'errors/error.html.j2']
BENIGN = {
    'ut_vers': '3.0.0~dev18', 'os_vers': 'Debian GNU/Linux 13 (trixie)', 'ip_addr': '192.168.0.254', 'hostname': 'unitotem', 'boot_id': 'abc123',
    'wifi': {'ssid': 'UniTotem-1F2E', 'password': 'p4ssw0rd', 'qrcode': '<svg viewBox="0 0 10 10"><path d="M0 0h1v1H0z"/></svg>'},
    'assets': {'css': ['/assets/main-AbC123.css'], 'js': '/assets/main-AbC123.js', 'dev': False},
    'state': {'user': 'admin', 'hostname': 'unitotem', 'plugins': ['unitotem_node']},
    'code': 404, 'short_desc': 'Not Found', 'long_desc': 'The page was not found.', 'image': 'data:image/png;base64,iVBORw0KGgo+/=',
}
HOSTILE = '<script>alert(1)</script>"\'&'


def render(name, context, autoescape):
    env = jinja2.Environment(loader=jinja2.DictLoader(files), extensions=['jinja2.ext.do', 'jinja2.ext.debug'], autoescape=autoescape)
    env.filters.update(templates.env.filters)
    return env.get_template(name).render(**context)


def test_the_product_environment_escapes():
    assert templates.env.autoescape


def test_ordinary_values_render_exactly_as_before():
    for name in PAGES:
        assert render(name, BENIGN, True) == render(name, BENIGN, False), name


def test_markup_in_data_is_text_and_the_qr_code_is_still_markup():
    hostile = dict(BENIGN, hostname=HOSTILE, os_vers=HOSTILE, ip_addr=HOSTILE, short_desc=HOSTILE, long_desc=HOSTILE,
                   wifi={'ssid': HOSTILE, 'password': HOSTILE, 'qrcode': BENIGN['wifi']['qrcode']})
    for name in PAGES:
        page = render(name, hostile, True)
        assert '<script>alert(1)</script>' not in page, name
    first_boot = render('first-boot.html.j2', hostile, True)
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in first_boot and BENIGN['wifi']['qrcode'] in first_boot
    assert 'alert(1)' not in render('errors/error.html.j2', hostile, True).replace('&lt;script&gt;alert(1)&lt;/script&gt;', '')


def test_state_embedded_in_the_script_cannot_close_it():
    page = render('base.html', dict(BENIGN, state={'hostname': '</script><script>alert(1)</script>', 'src': "'\"&"}), True)
    assert '</script><script>alert(1)' not in page and page.count('<script') == 2
