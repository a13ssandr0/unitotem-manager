import pytest

from unitotem_node.system.screens.mechanism import NoMechanismAvailable, resolve_mechanism


def test_auto_power_on_prefers_the_compositor_then_cec_then_ddc():
    # user decision 2026-10-25: DPMS first, it is the one mechanism with full control
    assert resolve_mechanism({'cec', 'ddc', 'output'}, 'on') == 'output'
    assert resolve_mechanism({'cec', 'ddc'}, 'on') == 'cec'
    assert resolve_mechanism({'ddc'}, 'on') == 'ddc'


def test_auto_power_off_prefers_the_compositor_then_ddc_then_cec():
    # a DDC/CI standby keeps the link: the compositor cannot see it, wake it or tell the screen is off (measured on an LG Ultrafine)
    assert resolve_mechanism({'cec', 'ddc', 'output'}, 'off') == 'output'
    assert resolve_mechanism({'cec', 'ddc'}, 'off') == 'ddc'
    assert resolve_mechanism({'cec'}, 'off') == 'cec'


def test_x_only_output_resolves_to_x_for_both_actions():
    assert resolve_mechanism({'output'}, 'on') == 'output'
    assert resolve_mechanism({'output'}, 'off') == 'output'


def test_nothing_available_raises():
    with pytest.raises(NoMechanismAvailable):
        resolve_mechanism(set(), 'on')
    with pytest.raises(NoMechanismAvailable):
        resolve_mechanism(set(), 'off')


def test_explicit_preference_honoured_when_available():
    assert resolve_mechanism({'cec', 'ddc', 'output'}, 'off', preferred='cec') == 'cec'


def test_explicit_preference_not_silently_rerouted():
    # An explicit request for a mechanism this output does not have must be
    # refused, not quietly satisfied through a different one - the caller
    # asked for a specific thing and getting a different thing without being
    # told is a worse failure mode than an error.
    with pytest.raises(NoMechanismAvailable):
        resolve_mechanism({'cec', 'output'}, 'off', preferred='ddc')
