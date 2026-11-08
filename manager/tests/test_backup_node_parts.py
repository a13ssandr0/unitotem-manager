"""The node's backup parts `touch` (mapping, orientation, calibration) and `screens` (output configuration), and the
netplan part that is gone. Every file lands in tmp_path (the assets path of the command line is redirected)."""
import json
from types import SimpleNamespace

import pytest

import unitotem_node as node
import unitotem_node.models.screen_control as screen_module
import unitotem_node.models.touch_map as touch_module
from unitotem_node.models.screen_control import OutputConfig, ScreenControl
from unitotem_node.models.touch_map import TouchMap
from utils.models.command_line import cmdargs

MATRIX = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0]


@pytest.fixture
def stores(monkeypatch, tmp_path):
    monkeypatch.setitem(cmdargs.__dict__, 'assets_file', tmp_path / 'assets.json')
    touch = TouchMap()
    screens = ScreenControl()
    monkeypatch.setattr(touch_module, 'touch_map', touch)
    monkeypatch.setattr(screen_module, 'screen_control', screens)
    return SimpleNamespace(touch=touch, screens=screens, dir=tmp_path)


def _touch_file(stores):
    return stores.dir / 'touch_map.json'


def _screens_file(stores):
    return stores.dir / 'screen_control.json'


# ── the registry ─────────────────────────────────────────────────────────────

def test_netplan_is_no_part_and_the_touch_and_screens_are():
    keys = [part.key for part in node.BACKUP]
    assert 'netplan' not in keys
    assert {'touch', 'screens'} <= set(keys)


# ── touch ────────────────────────────────────────────────────────────────────

def test_touch_export_then_restore_keeps_mapping_orientation_and_calibration(stores):
    stores.touch.set('DEV1', 'HDMI-A-1')
    stores.touch.set_orientation('DEV2', 1, 2)
    stores.touch.set_calibration('DEV3', MATRIX)
    archive = json.loads(json.dumps(node._export_touch()))

    stores.touch.mapping, stores.touch.orientation, stores.touch.calibration = {}, {}, {}
    node._restore_touch(archive, '3.1.0')
    assert stores.touch.mapping == {'DEV1': 'HDMI-A-1'}
    assert stores.touch.orientation == {'DEV2': {'turn': 1, 'flip': 2}}
    assert stores.touch.calibration == {'DEV3': MATRIX}


def test_touch_restore_reads_an_archive_with_only_the_mapping(stores):
    node._restore_touch({'DEV1': 'HDMI-A-1'}, '3.0.0')
    assert stores.touch.mapping == {'DEV1': 'HDMI-A-1'}
    assert stores.touch.orientation == {} and stores.touch.calibration == {}


def test_a_calibration_in_the_archive_replaces_the_orientation_of_its_panel(stores):
    node._restore_touch({'mapping': {}, 'orientation': {'DEV1': {'turn': 1, 'flip': 0}},
                         'calibration': {'DEV1': MATRIX}}, '3.1.0')
    assert stores.touch.calibration == {'DEV1': MATRIX}
    assert 'DEV1' not in stores.touch.orientation


@pytest.mark.parametrize('archive', [
    ['not', 'a', 'mapping'],
    {'mapping': {'DEV1': '../../etc'}},
    {'mapping': {'DEV1': 5}},
    {'orientation': {'DEV1': {'turn': 9, 'flip': 0}}},
    {'orientation': {'DEV1': 'turn 1'}},
    {'calibration': {'DEV1': [1, 2]}},
    {'calibration': {'DEV1': 'matrix'}},
])
def test_a_bad_touch_entry_is_refused_and_nothing_is_stored(stores, archive):
    stores.touch.set('OLD', 'HDMI-A-1')
    with pytest.raises(ValueError):
        node._restore_touch(archive, '3.1.0')
    assert stores.touch.mapping == {'OLD': 'HDMI-A-1'}
    assert not _touch_file(stores).exists()


def test_touch_restore_writes_the_file_atomically_and_applies_it(stores, monkeypatch):
    applied = []
    monkeypatch.setattr(node, '_touch_changed', lambda: applied.append(True))
    node._restore_touch({'DEV1': 'HDMI-A-1'}, '3.0.0')
    assert json.loads(_touch_file(stores).read_text())['mapping'] == {'DEV1': 'HDMI-A-1'}
    assert not (stores.dir / 'touch_map.tmp').exists()
    assert applied == [True]


def test_touch_reset_clears_every_section(stores):
    stores.touch.set('DEV1', 'HDMI-A-1')
    stores.touch.set_orientation('DEV2', 1, 0)
    stores.touch.set_calibration('DEV3', MATRIX)
    node._reset_touch()
    assert (stores.touch.mapping, stores.touch.orientation, stores.touch.calibration) == ({}, {}, {})
    assert json.loads(_touch_file(stores).read_text()) == {'mapping': {}, 'orientation': {}, 'calibration': {}}


# ── screens ──────────────────────────────────────────────────────────────────

def test_screens_export_carries_the_configuration_and_not_the_probe_results(stores):
    stores.screens.outputs['HDMI-A-1'] = OutputConfig(on_mechanism='ddc', cec_device='/dev/cec0', ddc_bus=7,
                                                      identity='local-panel', ddc_features=['D6'])
    exported = node._export_screens()
    assert exported['HDMI-A-1']['on_mechanism'] == 'ddc'
    assert exported['HDMI-A-1']['cec_device'] == '/dev/cec0'
    assert not set(exported['HDMI-A-1']) & {'ddc_bus', 'identity', 'ddc_features', 'ddc_capabilities'}


def test_screens_restore_keeps_the_probe_results_of_this_machine(stores):
    stores.screens.outputs['HDMI-A-1'] = OutputConfig(ddc_bus=7, identity='local-panel')
    node._restore_screens({'HDMI-A-1': {'on_mechanism': 'cec', 'ddc_bus': 99, 'identity': 'other-panel'}}, '3.1.0')
    cfg = stores.screens.outputs['HDMI-A-1']
    assert (cfg.on_mechanism, cfg.ddc_bus, cfg.identity) == ('cec', 7, 'local-panel')


def test_screens_restore_replaces_the_outputs(stores):
    stores.screens.outputs['OLD'] = OutputConfig(on_mechanism='cec')
    node._restore_screens({'HDMI-A-1': {'power_on_at_start': False}}, '3.1.0')
    assert list(stores.screens.outputs) == ['HDMI-A-1']
    assert stores.screens.outputs['HDMI-A-1'].power_on_at_start is False


@pytest.mark.parametrize('archive', [
    ['not', 'outputs'],
    {'../x': {}},
    {'HDMI-A-1': 'configuration'},
    {'HDMI-A-1': {'cec_device': '/etc/passwd'}},
    {'HDMI-A-1': {'on_mechanism': 'laser'}},
    {'HDMI-A-1': {'gpio': {'line': 5000}}},
    {'HDMI-A-1': {'wol': {'mac': 'not-a-mac'}}},
])
def test_a_bad_screen_entry_is_refused_and_nothing_is_stored(stores, archive):
    stores.screens.outputs['OLD'] = OutputConfig(on_mechanism='cec')
    with pytest.raises(ValueError):
        node._restore_screens(archive, '3.1.0')
    assert list(stores.screens.outputs) == ['OLD']
    assert not _screens_file(stores).exists()


def test_screens_restore_writes_the_file_atomically(stores):
    node._restore_screens({'HDMI-A-1': {'on_mechanism': 'cec'}}, '3.1.0')
    saved = json.loads(_screens_file(stores).read_text())
    assert saved['outputs']['HDMI-A-1']['on_mechanism'] == 'cec'
    assert not (stores.dir / 'screen_control.tmp').exists()


def test_screens_reset_keeps_the_probe_results_and_restores_the_defaults(stores):
    stores.screens.outputs['HDMI-A-1'] = OutputConfig(on_mechanism='cec', power_on_at_start=False, ddc_bus=7)
    node._reset_screens()
    cfg = stores.screens.outputs['HDMI-A-1']
    assert (cfg.on_mechanism, cfg.power_on_at_start, cfg.ddc_bus) == ('auto', True, 7)
