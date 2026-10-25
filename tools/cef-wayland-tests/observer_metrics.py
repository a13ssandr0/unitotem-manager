#!/usr/bin/env python3
"""cef.Display.AddObserver() on a mode change of the only output (works where an output cannot be switched off, e.g. a Pi): the mode of OUTPUT is
changed with wlr-randr and OnDisplayMetricsChanged must fire with the new bounds; then the old mode is restored.
usage: observer_metrics.py OUTPUT WIDTHxHEIGHT"""
import os, subprocess, sys, time
from cefpython3 import cefpython as cef
OUT, MODE = sys.argv[1], sys.argv[2]
W = os.environ.get('W', '/var/lib/unitotem/wl')
WLR = os.environ.get('WLR', W + '/usr/bin/wlr-randr')
ENV = dict(os.environ, LD_LIBRARY_PATH=W + '/extra')
events = []; t0 = [0.0]
class Obs:
    def OnDisplayMetricsChanged(self, d): events.append((round((time.monotonic() - t0[0]) * 1000), d.GetBounds()))
    def OnDisplayAdded(self, d): events.append(('added', d.GetBounds()))
    def OnDisplayRemoved(self, d): events.append(('removed', d.GetBounds()))
def pump(s):
    end = time.monotonic() + s
    while time.monotonic() < end: cef.MessageLoopWork(); time.sleep(0.002)
cef.Initialize(settings={'log_severity': cef.LOGSEVERITY_ERROR, 'cache_path': '/run/cefsuite/profile'}, switches={'no-sandbox': ''})
cef.Display.AddObserver(Obs())
if os.environ.get('WITH_WINDOW'):
    class D:
        def OnWindowCreated(self, w):
            w.SetToFillLayout(); w.AddChildView(self.v); w.SetFullscreen(True); w.Show()
        def IsFrameless(self, w): return True
    d = D(); d.v = cef.BrowserView.CreateBrowserView(url='data:text/html,metrics'); win = cef.Window.CreateTopLevelWindow(d); pump(3)
start = cef.Display.GetPrimaryDisplay().GetBounds()
print('start bounds', start, flush=True)
fails = 0
for target in (MODE, '%dx%d' % (start[2], start[3])):
    events.clear(); t0[0] = time.monotonic()
    p = subprocess.Popen([WLR, '--output', OUT, '--mode', target], env=ENV, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    pump(4)
    out = p.communicate()[0].decode().strip()
    if out: print('wlr-randr:', out, flush=True)
    print('wlr-randr rc', p.returncode, flush=True)
    b = cef.Display.GetPrimaryDisplay().GetBounds()
    ok = any(isinstance(e[0], int) for e in events) and 'x'.join(map(str, b[2:])) == target
    print(('PASS' if ok else 'FAIL'), 'mode', target, '-> OnDisplayMetricsChanged events (ms, bounds):', events, 'bounds now', b, flush=True)
    fails += not ok
cef.Shutdown()
sys.exit(1 if fails else 0)
