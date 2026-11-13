"""A cookie a page writes from a frame names SameSite=None; Secure unless the page names a SameSite of its own."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from plugins.unitotem_viewer import cdp

pytestmark = pytest.mark.skipif(shutil.which('node') is None, reason='node is not installed')

HARNESS = r"""
const written = [];
function Document() {}
Object.defineProperty(Document.prototype, 'cookie', {configurable: true, get() { return 'a=1'; }, set(v) { written.push(v); }});
function run(top, protocol) {
  const document = new Document();
  const window = {};
  window.top = top ? window : {};
  const location = {protocol};
  new Function('window', 'document', 'location', 'Document', SCRIPT)(window, document, location, Document);
  return document;
}
const framed = run(false, 'https:');
framed.cookie = 'lang=it; path=/';
framed.cookie = 'x=1; SameSite=Lax';
framed.cookie = 'y=2; samesite = strict';
const read = framed.cookie;
run(true, 'https:').cookie = 'top=1';
run(false, 'http:').cookie = 'plain=1';
console.log(JSON.stringify({written, read}));
"""


def test_frame_cookie_script():
    script = 'const SCRIPT = ' + __import__('json').dumps(cdp.FRAME_COOKIE_SCRIPT) + ';\n' + HARNESS
    out = subprocess.run(['node', '-e', script], capture_output=True, text=True, check=True, timeout=30).stdout
    import json
    result = json.loads(out)
    assert result['written'] == ['lang=it; path=/; SameSite=None; Secure', 'x=1; SameSite=Lax', 'y=2; samesite = strict',
                                 'top=1', 'plain=1']
    assert result['read'] == 'a=1'


def test_script_is_injected_in_every_frame():
    assert cdp.FRAME_COOKIE_SCRIPT in cdp.PAGE_SCRIPTS
