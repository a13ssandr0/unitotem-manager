"""The boot-time script that turns the outputs as saved (plugins/unitotem_viewer/output_transform.py)."""
import importlib.util
import json
from pathlib import Path

from unitotem_node.system.screens import wayland

SPEC = importlib.util.spec_from_file_location(
    'output_transform', Path(__file__).parents[1] / 'plugins' / 'unitotem_viewer' / 'output_transform.py')
ot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ot)


def test_the_script_table_is_the_nodes_table():
    assert ot.CONTENT_TO_TRANSFORM == wayland.CONTENT_TO_TRANSFORM


def test_plan_takes_only_this_nodes_present_outputs_with_something_to_do():
    settings = {'orientations': {'local-webview|HDMI-A-1': 3, 'local-webview|DP-2': 0, 'local-webview|DP-9': 1, 'other|HDMI-A-1': 2},
                'flips': {'local-webview|DP-2': 1, 'local-webview|HDMI-A-1': 0}}
    outputs = {'HDMI-A-1': set(), 'DP-2': set()}
    assert ot.plan(settings, outputs) == [('DP-2', ['--output', 'DP-2', '--transform', 'flipped']),
                                          ('HDMI-A-1', ['--output', 'HDMI-A-1', '--transform', '90'])]


def test_plan_ignores_values_it_cannot_read():
    settings = {'orientations': {'local-webview|A': 'x', 'local-webview|B': True, 'local-webview|C': 5},
                'flips': {'local-webview|C': 9, 'local-webview|D': 2}}
    assert ot.plan(settings, {'A': set(), 'B': set(), 'C': set(), 'D': set()}) == [
        ('D', ['--output', 'D', '--transform', 'flipped-180'])]
    assert ot.plan({}, {'A': set()}) == [] and ot.plan({'orientations': None}, {'A': set()}) == []


GOOD_MODE = (1920, 1080, 59940)


def test_plan_replays_a_saved_mode_only_when_it_is_listed():
    settings = {'modes': {'local-webview|HDMI-A-1': {'width': 1920, 'height': 1080, 'refresh_mhz': 59940},
                          'local-webview|DP-2': {'width': 800, 'height': 600, 'refresh_mhz': 60000}},
                'orientations': {'local-webview|DP-2': 1}}
    outputs = {'HDMI-A-1': {GOOD_MODE}, 'DP-2': {(1920, 1080, 60000)}}
    # HDMI-A-1: the mode is listed, DP-2: 800x600 is not, so its turn alone is applied
    assert ot.plan(settings, outputs) == [
        ('DP-2', ['--output', 'DP-2', '--transform', '270']),
        ('HDMI-A-1', ['--output', 'HDMI-A-1', '--mode', '1920x1080@59.940Hz'])]


def test_plan_ignores_malformed_saved_modes():
    settings = {'modes': {'local-webview|A': 'x', 'local-webview|B': {'width': 1920}, 'local-webview|C': {'width': 1, 'height': 1,
                                                                                                       'refresh_mhz': 1}}}
    assert ot.plan(settings, {'A': {GOOD_MODE}, 'B': {GOOD_MODE}, 'C': {(1, 1, 1)}}) == [
        ('C', ['--output', 'C', '--mode', '1x1@0.001Hz'])]


def test_listed_modes_reads_the_refresh_in_exact_mhz_and_skips_what_it_cannot_read():
    entries = [{'name': 'DP-1', 'modes': [{'width': 1920, 'height': 1080, 'refresh': 59.94},
                                          {'width': 1920, 'height': 1080, 'refresh': 60.0},
                                          {'width': 1920, 'refresh': 60.0},
                                          {'width': 0, 'height': 1080, 'refresh': 60.0}]},
               {'modes': []}, 'junk']
    assert ot.listed_modes(entries) == {'DP-1': {(1920, 1080, 59940), (1920, 1080, 60000)}}
    assert ot.listed_modes('not a list') == {}


def test_mode_argument_is_the_format_of_the_node_code():
    from unitotem_node.system.screens import wayland
    for width, height, refresh in [(1920, 1080, 59940), (1280, 720, 60000), (800, 600, 1), (3840, 2160, 143999)]:
        assert ot.mode_argument(width, height, refresh) == wayland.mode_argument(width, height, refresh)


def test_main_never_fails_and_always_clears_the_pending_mark(tmp_path, monkeypatch):
    pending = tmp_path / 'output-pending'
    pending.write_text('')
    monkeypatch.setattr(ot, 'PENDING', str(pending))
    assert ot.main(str(tmp_path)) == 0        # no viewer_settings.json at all
    assert not pending.exists()


def test_main_runs_one_wlr_randr_for_all_the_turned_outputs(tmp_path, monkeypatch):
    (tmp_path / 'viewer_settings.json').write_text(json.dumps({'orientations': {'local-webview|HDMI-A-1': 1}}))
    monkeypatch.setattr(ot, 'PENDING', str(tmp_path / 'none'))
    calls = []

    class Done:
        stdout = json.dumps([{'name': 'HDMI-A-1'}])

    monkeypatch.setattr(ot.subprocess, 'run', lambda cmd, **kw: calls.append(cmd) or Done())
    assert ot.main(str(tmp_path)) == 0
    assert calls == [['wlr-randr', '--json'], ['wlr-randr', '--output', 'HDMI-A-1', '--transform', '270']]


def test_main_sets_mode_and_transform_in_one_invocation(tmp_path, monkeypatch):
    (tmp_path / 'viewer_settings.json').write_text(json.dumps({
        'modes': {'local-webview|HDMI-A-1': {'width': 1920, 'height': 1080, 'refresh_mhz': 59940}},
        'orientations': {'local-webview|HDMI-A-1': 1}}))
    monkeypatch.setattr(ot, 'PENDING', str(tmp_path / 'none'))
    calls = []

    class Done:
        stdout = json.dumps([{'name': 'HDMI-A-1', 'modes': [{'width': 1920, 'height': 1080, 'refresh': 59.94}]}])

    monkeypatch.setattr(ot.subprocess, 'run', lambda cmd, **kw: calls.append(cmd) or Done())
    assert ot.main(str(tmp_path)) == 0
    assert calls == [['wlr-randr', '--json'],
                     ['wlr-randr', '--output', 'HDMI-A-1', '--mode', '1920x1080@59.940Hz', '--transform', '270']]


def test_main_exits_zero_when_the_compositor_lists_nothing_usable(tmp_path, monkeypatch):
    (tmp_path / 'viewer_settings.json').write_text(json.dumps({'modes': {'local-webview|A': {'width': 1, 'height': 1,
                                                                                            'refresh_mhz': 1}}}))
    monkeypatch.setattr(ot, 'PENDING', str(tmp_path / 'none'))

    class Done:
        stdout = 'not json'

    calls = []
    monkeypatch.setattr(ot.subprocess, 'run', lambda cmd, **kw: calls.append(cmd) or Done())
    assert ot.main(str(tmp_path)) == 0
    assert calls == [['wlr-randr', '--json']]
