"""The idle pages (no assets, welcome) name their screen only when more than one is connected: with one screen they render
exactly as they did before the label existed."""
from pathlib import Path

import pytest

from templates import files, screen_context
from test_templates_escape import BENIGN, render

GOLDEN = Path(__file__).parent / 'fixtures' / 'idle-pages'
PAGES = {'no-assets.html.j2': 'no-assets.html', 'first-boot.html.j2': 'first-boot.html'}


@pytest.mark.parametrize('template', PAGES)
def test_one_screen_renders_byte_identical_to_the_page_before_the_label(template):
    # The goldens were rendered from the templates before the screen label existed (HEAD of the change that added it)
    assert render(template, BENIGN, True) == (GOLDEN / PAGES[template]).read_text()


@pytest.mark.parametrize('template', PAGES)
def test_one_screen_with_an_explicit_empty_label_is_the_same_page(template):
    assert render(template, dict(BENIGN, **screen_context('', '')), True) == render(template, BENIGN, True)


def test_the_label_goes_under_the_address_on_both_pages():
    context = dict(BENIGN, **screen_context('HDMI-A-1', '2'))
    no_assets = render('no-assets.html.j2', context, True)
    assert 'IP address: 192.168.0.254<br>Screen 2: HDMI-A-1<br>Hostname: unitotem' in no_assets
    first_boot = render('first-boot.html.j2', context, True)
    assert 'IP address: <span id="ip">192.168.0.254</span><br>Screen 2: HDMI-A-1<br>Hostname: unitotem' in first_boot


def test_the_label_is_validated_and_numbered_from_one():
    assert screen_context('HDMI-A-1', '2') == {'screen_label': 'Screen 2: HDMI-A-1'}
    assert screen_context('DP-2', '1') == {'screen_label': 'Screen 1: DP-2'}
    for name, index in [('', '1'), ('<script>', '1'), ('HDMI A', '1'), ('a' * 65, '1'), ('HDMI-A-1', ''),
                        ('HDMI-A-1', '0'), ('HDMI-A-1', '1000'), ('HDMI-A-1', '²'), ('HDMI-A-1', '-1')]:
        assert screen_context(name, index) == {}, (name, index)


def test_a_hostile_name_cannot_reach_the_page_as_markup():
    assert screen_context('<script>alert(1)</script>', '2') == {}
    assert 'screen_label' not in files['no-assets.html.j2'].split('{% if')[0]
