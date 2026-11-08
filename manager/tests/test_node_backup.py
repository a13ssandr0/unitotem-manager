"""The node's own backup parts (hostname, audio, timers, touch mapping, screens), the core's composition of the parts, the
archive of a legacy backup and the uploaded files of an archive.

Every system tool and every path is redirected: the timers, the touch mapping and the screens are saved in tmp, the
hostname and the sound server are replaced by recorders. Importing the upload manager would create the node's
/var/lib/unitotem, so the uploads folder is pointed to tmp before the first import.
"""
import asyncio
import io
import json
import socket
import zipfile
from types import SimpleNamespace

import pytest

from utils.models.command_line import cmdargs


@pytest.fixture
def node(monkeypatch, tmp_path):
    """Imports the node plugin and the core backup with every file under tmp."""
    monkeypatch.setitem(cmdargs.__dict__, 'assets_file', tmp_path / 'assets.json')
    monkeypatch.setitem(cmdargs.__dict__, 'users_file', tmp_path / 'users.json')
    import utils.constants as constants
    monkeypatch.setattr(constants, 'uploads_folder', tmp_path / 'uploaded')
    asyncio.set_event_loop(asyncio.new_event_loop())     # the upload manager reads the current loop when it is built
    import unitotem_node as plugin
    import unitotem_node.models.timers as timers_model
    import unitotem_node.models.touch_map as touch_model
    import unitotem_node.system.audio as audio
    import unitotem_node.system.dbus_system as dbus_system
    import unitotem_node.models.screen_control as screen_model
    import utils.backup as backup
    monkeypatch.setattr(timers_model, 'timer_manager', timers_model.TimerManager())
    monkeypatch.setattr(touch_model, 'touch_map', touch_model.TouchMap())
    monkeypatch.setattr(screen_model, 'screen_control', screen_model.ScreenControl())
    return SimpleNamespace(plugin=plugin, timers=timers_model, touch=touch_model, audio=audio, dbus=dbus_system,
                           screens=screen_model, backup=backup, tmp=tmp_path)


def run(awaitable):
    """The hostname restore is a coroutine; the others are plain functions."""
    return asyncio.run(awaitable) if asyncio.iscoroutine(awaitable) else awaitable


# ── the parts and their composition ──────────────────────────────────────────

def test_the_node_parts_are_the_plugin_backup(node):
    keys = [p.key for p in node.plugin.BACKUP]
    assert keys == ['hostname', 'audio', 'timers', 'touch', 'screens']
    assert node.plugin.PLUGIN.backup is node.plugin.BACKUP


def test_the_node_parts_join_the_core_parts_and_describe_them(node, monkeypatch):
    monkeypatch.setattr(node.backup.plugins, 'each', lambda attribute: [(node.plugin.PLUGIN, node.plugin.BACKUP)]
                        if attribute == 'backup' else [])
    found = node.backup.parts()
    assert {'playlists', 'layouts', 'users', 'security', 'remote', 'ticker', 'uploads'} <= set(found)
    assert {'hostname', 'audio', 'timers', 'touch', 'screens'} <= set(found)
    described = {d['key']: d for d in node.backup.describe()}
    assert described['timers']['files'] is False and described['timers']['default_in_backup'] is True
    assert described['uploads']['default_in_backup'] is False          # large: asked for, not in every backup


def test_a_plugin_part_with_a_core_key_replaces_it(node, monkeypatch):
    from utils.plugins import BackupPart
    mine = BackupPart('playlists', 'Plugin playlists', lambda: {}, lambda data, version: None)
    monkeypatch.setattr(node.backup.plugins, 'each', lambda attribute: [(None, [mine])])
    assert node.backup.parts()['playlists'] is mine


# ── hostname ─────────────────────────────────────────────────────────────────

def test_the_hostname_is_exported_as_the_machine_name(node):
    assert node.plugin.BACKUP[0].export() == socket.gethostname()


def test_a_hostname_is_restored_through_systemd(node, monkeypatch):
    calls = []

    async def set_name(name):
        calls.append(name)

    monkeypatch.setattr(node.dbus, 'set_static_hostname', set_name)
    run(node.plugin._restore_hostname('totem-lobby', '3.0.0'))
    assert calls == ['totem-lobby']


@pytest.mark.parametrize('name', ['bad name', 'a;rm -rf /', '-leading', 'x' * 65, '', 'dots..here'])
def test_an_invalid_hostname_is_refused_before_systemd_is_asked(node, monkeypatch, name):
    def no_bus():
        raise AssertionError('systemd must not be reached with an invalid name')

    monkeypatch.setattr(node.dbus, '_system_bus', no_bus)
    with pytest.raises(ValueError):
        run(node.plugin._restore_hostname(name, '3.0.0'))


# ── default audio device ─────────────────────────────────────────────────────

def test_the_default_audio_device_is_exported_and_none_without_a_sound_server(node, monkeypatch):
    monkeypatch.setattr(node.audio, 'get_default_audio_device', lambda: 'alsa_output.usb')
    assert node.plugin._export_audio() == 'alsa_output.usb'

    def no_server():
        raise ConnectionError('no pulseaudio')

    monkeypatch.setattr(node.audio, 'get_default_audio_device', no_server)
    assert node.plugin._export_audio() is None


@pytest.mark.parametrize('version', ['2.9.0', '1.0.0'])
def test_an_audio_name_from_before_3_0_0_means_nothing_and_is_not_applied(node, monkeypatch, version):
    applied = []
    monkeypatch.setattr(node.audio, 'set_default_audio_device', applied.append)
    node.plugin._restore_audio('alsa_output.old', version)
    assert applied == []


def test_an_audio_name_from_3_0_0_is_applied(node, monkeypatch):
    applied = []
    monkeypatch.setattr(node.audio, 'set_default_audio_device', applied.append)
    node.plugin._restore_audio('alsa_output.usb', '3.0.0')
    assert applied == ['alsa_output.usb']


# ── timers ───────────────────────────────────────────────────────────────────

def timer(**fields):
    from unitotem_node.models.timers import Timer
    base = dict(action='reb', minute='0', hour='3', day_of_month='*', month='*', day_of_week='*')
    return Timer(**{**base, **fields})


def test_timers_round_trip_through_the_backup(node):
    manager = node.timers.timer_manager
    manager.add(timer())
    manager.add(timer(action='scr_off', output='DP-1', hour='22'))
    exported = json.loads(json.dumps(node.plugin._export_timers()))
    assert sorted(item['action'] for item in exported) == ['reb', 'scr_off']
    before = {uuid: t.model_dump() for uuid, t in manager.timers.items()}
    node.plugin._reset_timers()
    assert manager.timers == {}
    node.plugin._restore_timers(exported, '3.0.0')
    assert {uuid: t.model_dump() for uuid, t in manager.timers.items()} == before
    saved = json.loads((node.tmp / 'timers.json').read_text())
    assert len(saved['timers']) == 2


def test_a_backup_with_one_invalid_timer_restores_none_of_them(node):
    manager = node.timers.timer_manager
    kept = manager.add(timer())
    bad = {**timer().model_dump(), 'action': 'scr_on'}           # a screen timer needs an output
    with pytest.raises(ValueError):
        node.plugin._restore_timers([timer(hour='5').model_dump(), bad], '3.0.0')
    assert list(manager.timers) == [kept.uuid]


def test_a_suspend_timer_without_its_wake_up_is_refused(node):
    with pytest.raises(ValueError):
        node.plugin._restore_timers([{**timer().model_dump(), 'action': 'sus'}], '3.0.0')


# ── touch screen mapping ─────────────────────────────────────────────────────

def test_the_touch_mapping_is_exported_and_restored(node):
    node.touch.touch_map.set('ILITEK Touch|222a:0001|1234', 'DP-1')
    exported = json.loads(json.dumps(node.plugin._export_touch()))
    assert exported['mapping'] == {'ILITEK Touch|222a:0001|1234': 'DP-1'}
    node.plugin._reset_touch()
    assert node.touch.touch_map.mapping == {}
    node.plugin._restore_touch(exported, '3.0.0')
    assert node.touch.touch_map.mapping == exported['mapping']
    assert json.loads((node.tmp / 'touch_map.json').read_text())['mapping'] == exported['mapping']


def test_an_archive_from_before_the_touch_details_is_the_flat_mapping(node):
    node.plugin._restore_touch({'ILITEK Touch|222a:0001|1234': 'DP-1'}, '3.0.0')
    assert node.touch.touch_map.mapping == {'ILITEK Touch|222a:0001|1234': 'DP-1'}


def test_a_restored_touch_mapping_is_validated_like_a_normal_write(node):
    """set() refuses an output name that is not a RandR name; a backup must not be a way around it."""
    with pytest.raises(ValueError):
        node.plugin._restore_touch({'mapping': {'ILITEK Touch|222a:0001|1234': 'DP-1; reboot'}}, '3.0.0')


def test_orientation_and_calibration_survive_a_backup(node):
    touch = node.touch.touch_map
    touch.set('panel', 'DP-1')
    touch.set_orientation('panel', 1, 0)
    touch.set('glass', 'DP-2')
    touch.set_calibration('glass', [1, 0, 0, 0, 1, 0])
    exported = json.loads(json.dumps(node.plugin._export_touch()))
    node.plugin._reset_touch()
    node.plugin._restore_touch(exported, '3.0.0')
    assert touch.orientation == {'panel': {'turn': 1, 'flip': 0}}
    assert touch.calibration == {'glass': [1.0, 0.0, 0.0, 0.0, 1.0, 0.0]}


# ── screens ──────────────────────────────────────────────────────────────────

def test_screen_export_leaves_out_what_the_machine_probed(node):
    node.screens.screen_control.output('DP-1').ddc_bus = 9
    node.screens.screen_control.output('DP-1').identity = 'LG Ultrafine'
    exported = node.plugin._export_screens()
    assert 'DP-1' in exported
    assert 'ddc_bus' not in exported['DP-1'] and 'identity' not in exported['DP-1']


def test_a_screen_name_that_is_not_an_output_name_is_refused(node):
    with pytest.raises(ValueError):
        node.plugin._restore_screens({'DP-1; reboot': {}}, '3.0.0')


def test_screen_restore_keeps_the_probe_results_of_this_machine(node):
    node.screens.screen_control.output('DP-1').ddc_bus = 10
    node.plugin._restore_screens({'DP-1': {}}, '3.0.0')
    assert node.screens.screen_control.outputs['DP-1'].ddc_bus == 10


# ── the core's helpers ───────────────────────────────────────────────────────

def test_a_legacy_archive_becomes_the_equivalent_parts(node):
    legacy = {
        'CONFIG': json.dumps({'playlists': {'p1': {'name': 'Main'}}, 'playlists_default': 'p1',
                              'users': {'alice': {'permissions': ['audio']}}}),
        'hostname': 'lobby',
        'def_audio_dev': 'alsa_output.usb',
    }
    parts = node.backup.from_legacy(legacy)
    assert parts['playlists'] == {'playlists': {'p1': {'name': 'Main'}}, 'default': 'p1'}
    assert parts['users'] == {'alice': {'permissions': ['audio']}}
    assert parts['hostname'] == 'lobby' and parts['audio'] == 'alsa_output.usb'


def test_a_part_already_in_the_archive_is_not_replaced_by_a_legacy_key(node):
    archive = {'parts': {'hostname': 'new-name'}, 'hostname': 'old-name'}
    assert node.backup.from_legacy(archive)['hostname'] == 'new-name'


def test_an_archive_without_config_gives_its_parts_back(node):
    assert node.backup.from_legacy({'parts': {'touch': {'a': 'DP-1'}}}) == {'touch': {'a': 'DP-1'}}
    assert node.backup.from_legacy({}) == {}


# ── uploaded files of an archive ─────────────────────────────────────────────

def test_uploaded_files_are_written_under_uploaded_and_restored_from_there(node, monkeypatch, tmp_path):
    source = tmp_path / 'clip.mp4'
    source.write_bytes(b'video')
    saved = []

    async def save(infile, name):
        saved.append((name, infile.read()))

    monkeypatch.setattr(node.backup, 'upload_manager', SimpleNamespace(files=[source], save=save))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        node.backup._export_files(archive)
        archive.writestr('config.json', '{}')
        archive.writestr('uploaded/', '')
    with zipfile.ZipFile(io.BytesIO(buffer.getvalue())) as archive:
        assert archive.namelist() == ['uploaded/clip.mp4', 'config.json', 'uploaded/']
        asyncio.run(node.backup._restore_files(archive))
    assert saved == [('uploaded/clip.mp4', b'video')]
