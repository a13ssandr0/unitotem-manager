import pytest

from utils.system.screens.mechanism import NoMechanismAvailable, resolve_mechanism


def test_auto_power_on_prefers_cec_then_ddc_then_x():
    assert resolve_mechanism({'cec', 'ddc', 'x'}, 'on') == 'cec'
    assert resolve_mechanism({'ddc', 'x'}, 'on') == 'ddc'
    assert resolve_mechanism({'x'}, 'on') == 'x'


def test_auto_power_off_prefers_ddc_then_cec_then_x():
    # DDC/CI reliably powers a monitor down (confirmed real on the dev host:
    # 0xD6 answered on every monitor tried), which is why the order flips
    # relative to power-on.
    assert resolve_mechanism({'cec', 'ddc', 'x'}, 'off') == 'ddc'
    assert resolve_mechanism({'cec', 'x'}, 'off') == 'cec'
    assert resolve_mechanism({'x'}, 'off') == 'x'


def test_x_only_output_resolves_to_x_for_both_actions():
    assert resolve_mechanism({'x'}, 'on') == 'x'
    assert resolve_mechanism({'x'}, 'off') == 'x'


def test_nothing_available_raises():
    with pytest.raises(NoMechanismAvailable):
        resolve_mechanism(set(), 'on')
    with pytest.raises(NoMechanismAvailable):
        resolve_mechanism(set(), 'off')


def test_explicit_preference_honoured_when_available():
    assert resolve_mechanism({'cec', 'ddc', 'x'}, 'off', preferred='cec') == 'cec'


def test_explicit_preference_not_silently_rerouted():
    # An explicit request for a mechanism this output does not have must be
    # refused, not quietly satisfied through a different one - the caller
    # asked for a specific thing and getting a different thing without being
    # told is a worse failure mode than an error.
    with pytest.raises(NoMechanismAvailable):
        resolve_mechanism({'cec', 'x'}, 'off', preferred='ddc')
