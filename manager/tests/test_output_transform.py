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
    assert ot.plan(settings, {'HDMI-A-1', 'DP-2'}) == [('DP-2', 'flipped'), ('HDMI-A-1', '90')]


def test_plan_ignores_values_it_cannot_read():
    settings = {'orientations': {'local-webview|A': 'x', 'local-webview|B': True, 'local-webview|C': 5},
                'flips': {'local-webview|C': 9, 'local-webview|D': 2}}
    assert ot.plan(settings, {'A', 'B', 'C', 'D'}) == [('D', 'flipped-180')]
    assert ot.plan({}, {'A'}) == [] and ot.plan({'orientations': None}, {'A'}) == []


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
