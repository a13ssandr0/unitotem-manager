#!/usr/bin/env python3
"""The old path: cef.WindowInfo() without a parent on Wayland (Chrome style window). Does CloseBrowser() destroy the toplevel?
Prints the toplevels (wlrctl) before and after. Evidence for the note in docs/cef-wayland/native-path.md."""
import subprocess, sys, time, os
from cefpython3 import cefpython as cef
WLRCTL = '/var/lib/unitotem/wl/usr/bin/wlrctl'
def toplevels():
    return subprocess.run([WLRCTL, 'toplevel', 'list'], capture_output=True, text=True,
                          env=dict(os.environ, LD_LIBRARY_PATH='/var/lib/unitotem/wl/extra')).stdout.strip().splitlines()
ev = []
class H:
    def OnBeforeClose(self, browser): ev.append('OnBeforeClose')
cef.Initialize(settings={'log_severity': cef.LOGSEVERITY_ERROR, 'cache_path': '/tmp/views-test-profile'}, switches={'no-sandbox': ''})
b = cef.CreateBrowserSync(window_info=cef.WindowInfo(), url='data:text/html,old+path')
b.SetClientHandler(H())
t0 = time.monotonic()
def pump(s):
    t = time.monotonic()
    while time.monotonic() - t < s: cef.MessageLoopWork(); time.sleep(0.01)
pump(4); print('before close:', toplevels(), flush=True)
b.CloseBrowser(True); pump(4)
print('after CloseBrowser:', toplevels(), ev, flush=True)
cef.Shutdown()
print('after Shutdown:', toplevels(), flush=True)
