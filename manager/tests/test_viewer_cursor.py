"""The pointer is hidden from the start of a document and shown only after the first mouse movement, for CURSOR_HIDE_MS (10 s) after the last one."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from plugins.unitotem_viewer import cdp

pytestmark = pytest.mark.skipif(shutil.which('node') is None, reason='node is not installed')

HARNESS = r"""
const log = [];
let rootReady = false, observerCb = null, moveHandler = null, timers = [];
const style = {dataset: {}, disabled: false, textContent: ''};
const root = {append(el) { log.push('mounted'); el.mounted = true; }};
const document = {
  createElement: () => style,
  get head() { return rootReady ? root : null; },
  get documentElement() { return rootReady ? root : null; },
};
const window = {};
class MutationObserver { constructor(cb) { observerCb = cb; } observe() { log.push('observing'); } disconnect() { log.push('disconnected'); } }
const addEventListener = (type, fn) => { if (type === 'pointermove') moveHandler = fn; };
const setTimeout = (fn, ms) => { timers.push({fn, ms}); return timers.length; };
const clearTimeout = () => {};
new Function('window', 'document', 'MutationObserver', 'addEventListener', 'setTimeout', 'clearTimeout', SCRIPT)(window, document, MutationObserver, addEventListener, setTimeout, clearTimeout);
const out = {};
out.hiddenFromStart = style.disabled === false && !style.mounted;       // style not yet mounted: no root element at that moment
out.log1 = log.slice();
rootReady = true; observerCb();
out.mounted = !!style.mounted;
out.disabledAtStart = style.disabled;                                 // false = the "cursor: none" rule is active
moveHandler({pointerType: 'touch'}); out.afterTouch = style.disabled;
moveHandler({pointerType: 'mouse'}); out.afterMouse = style.disabled;
out.delay = timers.length ? timers[timers.length - 1].ms : null;
timers[timers.length - 1].fn(); out.afterTimer = style.disabled;
console.log(JSON.stringify(out));
"""


def run():
    script = 'const SCRIPT = ' + json.dumps(cdp.CURSOR_SCRIPT) + ';\n' + HARNESS
    out = subprocess.run(['node', '-e', script], capture_output=True, text=True, check=True, timeout=30).stdout
    return json.loads(out)


def test_style_waits_for_the_root_element_and_hides_the_pointer_from_the_start():
    r = run()
    assert r['log1'] == ['observing']                  # no root element yet: nothing mounted, an observer waits
    assert r['mounted'] is True                        # the style is mounted as soon as the document has a root
    assert r['disabledAtStart'] is False               # the hiding rule is active before any movement


def test_pointer_shows_on_mouse_movement_only_and_hides_after_ten_seconds():
    r = run()
    assert r['afterTouch'] is False                    # a touch does not show the pointer
    assert r['afterMouse'] is True                     # a mouse movement does
    assert r['delay'] == 10000 == cdp.CURSOR_HIDE_MS
    assert r['afterTimer'] is False                    # hidden again after the delay
