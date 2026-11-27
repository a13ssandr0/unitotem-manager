"""The compositor hides its own pointer natively: rc.xml keybind + window rule (HideCursor, WarpCursor) and the autostart that presses the key."""
import os
import xml.etree.ElementTree as ET

DEBIAN = os.path.join(os.path.dirname(__file__), "..", "..", "debian")
if not os.path.isdir(DEBIAN):          # autopkgtest copies manager/tests only
    import pytest
    pytest.skip("debian/ not present", allow_module_level=True)


def actions(node):
    return [(a.get("name"), a.get("to"), a.get("x"), a.get("y")) for a in node.findall("action")]


def test_rc_keybind_and_window_rule():
    root = ET.parse(os.path.join(DEBIAN, "labwc-rc.xml")).getroot()
    keybinds = root.findall("./keyboard/keybind")
    assert [k.get("key") for k in keybinds] == ["W-F23"]
    expected = [("HideCursor", None, None, None), ("WarpCursor", "output", "-1", "-1")]
    assert actions(keybinds[0]) == expected
    rules = root.findall("./windowRules/windowRule")
    assert len(rules) == 1 and rules[0].get("identifier") == "*"
    assert actions(rules[0]) == expected
    assert root.find("./keyboard/default") is None


def test_autostart_presses_the_key_after_the_output_exists():
    path = os.path.join(DEBIAN, "labwc-autostart")
    text = open(path).read()
    assert text.startswith("#!/bin/sh\n") and os.access(path, os.X_OK)
    assert text.index("wlr-randr") < text.index("wtype -M logo -k F23 -m logo")
    assert "sleep" in text and "-ge 100" in text            # bounded wait
