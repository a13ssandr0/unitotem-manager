"""Per-asset zoom and pinch-zoom switch: defaults, range validation, compatibility with files that predate them."""
import asyncio
import json

import pytest
from pydantic import ValidationError

from utils.models.assets import Asset, AssetsManager
from webview.controller import show_payload


@pytest.fixture(autouse=True)
def loop():
    asyncio.set_event_loop(asyncio.new_event_loop())


def test_defaults():
    a = Asset(url='https://example.com')
    assert a.zoom == 1.0
    assert a.allow_pinch_zoom is False


@pytest.mark.parametrize('value', [0.25, 1, 2.5, 5])
def test_valid_zoom(value):
    assert Asset(url='https://example.com', zoom=value).zoom == value


@pytest.mark.parametrize('value', [0, -1, 0.24, 5.01, 1e9, float('nan'), float('inf')])
def test_invalid_zoom_is_refused(value):
    with pytest.raises(ValidationError):
        Asset(url='https://example.com', zoom=value)
    a = Asset(url='https://example.com')
    with pytest.raises(ValidationError):
        a.zoom = value
    assert a.zoom == 1.0


def test_old_file_without_the_fields_loads(tmp_path):
    old = {'playlist_id': 'p', 'name': 'Old', 'default_duration': 10,
           'assets': [{'url': 'https://example.com', 'duration': 5, 'enabled': True, 'uuid': 'a' * 32}]}
    f = tmp_path / 'p.json'
    f.write_text(json.dumps(old))
    am = AssetsManager()
    am.load(f)
    assert am.assets[0].zoom == 1.0 and am.assets[0].allow_pinch_zoom is False


def test_round_trip_and_payload():
    a = Asset(url='https://example.com', zoom=1.5, allow_pinch_zoom=True)
    dumped = a.model_dump(mode='json')
    assert dumped['zoom'] == 1.5 and dumped['allow_pinch_zoom'] is True
    b = Asset.model_validate({**dumped, 'uuid': 'b' * 32})
    assert b.zoom == 1.5 and b.allow_pinch_zoom is True
    p = show_payload(a, 0)
    assert p['zoom'] == 1.5 and p['allow_pinch_zoom'] is True
