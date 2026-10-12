"""The plugin loader, the backup parts and the network helpers that the core depends on instead of a plugin."""
import sys

import pytest

import utils.plugins as plugins
from utils.backup import from_legacy
from utils.system.ip import default_interface


def make_plugin(directory, name, body):
    package = directory / name
    package.mkdir()
    (package / '__init__.py').write_text(body)


@pytest.fixture
def fresh(monkeypatch):
    monkeypatch.setattr(plugins, 'loaded', [])
    yield
    for name in [m for m in sys.modules if m.startswith('plug_')]:
        del sys.modules[name]


def test_a_plugin_package_is_loaded(tmp_path, fresh):
    make_plugin(tmp_path, 'plug_good', "from utils.plugins import Plugin\nPLUGIN = Plugin(name='good')\n")
    assert [p.name for p in plugins.load(tmp_path)] == ['good']
    assert plugins.provides('good') and not plugins.provides('other')


def test_a_broken_plugin_is_left_out_and_the_others_still_load(tmp_path, fresh):
    make_plugin(tmp_path, 'plug_a_broken', "import a_module_that_does_not_exist\n")
    make_plugin(tmp_path, 'plug_b_wrong', "PLUGIN = 'not a plugin'\n")
    make_plugin(tmp_path, 'plug_c_good', "from utils.plugins import Plugin\nPLUGIN = Plugin(name='good')\n")
    (tmp_path / 'not_a_package.py').write_text("raise SystemExit\n")
    assert [p.name for p in plugins.load(tmp_path)] == ['good']


def test_no_plugins_directory_is_fine(tmp_path, fresh):
    assert plugins.load(tmp_path / 'missing') == []


def test_each_yields_only_what_a_plugin_provides(tmp_path, fresh):
    make_plugin(tmp_path, 'plug_x', "from utils.plugins import Plugin\nPLUGIN = Plugin(name='x', api=(('m', 'C', None),))\n")
    make_plugin(tmp_path, 'plug_y', "from utils.plugins import Plugin\nPLUGIN = Plugin(name='y')\n")
    plugins.load(tmp_path)
    assert [(p.name, v) for p, v in plugins.each('api')] == [('x', (('m', 'C', None),))]


def test_old_backups_become_parts():
    old = {'version': '3.0.0', 'CONFIG': {'playlists': {'a': {}}, 'playlists_default': 'a', 'users': {'root': {}}},
           'hostname': 'totem', 'def_audio_dev': 'sink', 'netplan': {'x.yaml': 'y'}}
    parts = from_legacy(old)
    assert parts['playlists'] == {'playlists': {'a': {}}, 'default': 'a'}
    assert parts['users'] == {'root': {}}
    assert (parts['hostname'], parts['audio'], parts['netplan']) == ('totem', 'sink', {'x.yaml': 'y'})


def test_new_backups_are_left_alone():
    assert from_legacy({'parts': {'users': {'a': 1}}}) == {'users': {'a': 1}}


HEADER = 'Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n'


def test_default_interface_is_the_one_with_the_default_gateway(tmp_path):
    route = tmp_path / 'route'
    route.write_text(HEADER + 'wlan0\t0001A8C0\t00000000\t0001\t0\t0\t600\t00FFFFFF\t0\t0\t0\n'
                              'eth0\t00000000\t0101A8C0\t0003\t0\t0\t100\t00000000\t0\t0\t0\n')
    assert default_interface(str(route)) == 'eth0'


def test_no_default_route_means_not_connected(tmp_path):
    route = tmp_path / 'route'
    route.write_text(HEADER + 'wlan0\t0001A8C0\t00000000\t0001\t0\t0\t600\t00FFFFFF\t0\t0\t0\n')
    assert default_interface(str(route)) is None
    assert default_interface(str(tmp_path / 'missing')) is None
