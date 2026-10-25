#!/usr/bin/env python3
"""End-to-end check of the CEF Views API on a Wayland compositor (labwc) with several outputs.

Opens one frameless window per output (fullscreen ON that output through Window.SetFullscreenOnDisplay, with an app_id), checks
that each is fullscreen on the right output (CEF's own view: Window.GetDisplay() and the window bounds, and the compositor's:
wlrctl toplevel list), optionally unplugs/re-plugs an output (wlr-randr --off/--on) and moves the window back, closes everything and
checks that no toplevel is left and OnBeforeClose fired for each browser.

usage: outputs.py [--outputs DP-1,DP-2] [--hotplug DP-2] [--url URL]     (prints PASS/FAIL lines, exit status 1 on any FAIL)
Env: WAYLAND_DISPLAY=/run/unitotem/xdg/wayland-0 (no XDG_RUNTIME_DIR), PYTHONPATH with the cefpython3 under test,
LD_LIBRARY_PATH=/var/lib/unitotem/wl/extra for wlrctl/wlr-randr.
"""
import argparse, json, os, re, subprocess, sys, time
from cefpython3 import cefpython as cef

W = os.environ.get('W', '/var/lib/unitotem/wl')
WL = os.environ.get('WL_TOOLS', W + '/usr/bin')
ENV = dict(os.environ, LD_LIBRARY_PATH=W + '/extra')
ap = argparse.ArgumentParser()
ap.add_argument('--outputs')
ap.add_argument('--hotplug')
ap.add_argument('--url', default='data:text/html,<body style="margin:0;background:%23c60;color:white;font:8vw sans-serif">CEF Views</body>')
a = ap.parse_args()
fails = []

def check(ok, what):
    print(('PASS ' if ok else 'FAIL ') + what, flush=True)
    if not ok: fails.append(what)

def run(*cmd):
    return subprocess.run([os.path.join(WL, cmd[0])] + list(cmd[1:]), capture_output=True, text=True, env=ENV).stdout

def outputs():
    return {o['name']: o for o in json.loads(run('wlr-randr', '--json')) if o['enabled']}

def toplevels():
    return [l for l in run('wlrctl', 'toplevel', 'list').splitlines() if l.strip()]

def pump(sec):
    end = time.monotonic() + sec
    while time.monotonic() < end:
        cef.MessageLoopWork(); time.sleep(0.01)

class Win:
    def __init__(self, output, n):
        self.output, self.app_id, self.destroyed, self.before_close = output, 'org.unitotem.test.%d' % n, False, False
        self.view = cef.BrowserView.CreateBrowserView(url=a.url, delegate=self)
        self.window = cef.Window.CreateTopLevelWindow(self)
    # window delegate
    def OnWindowCreated(self, window):
        window.SetToFillLayout(); window.AddChildView(self.view)
        d = cef.Display.FindByOutputName(self.output)
        window.SetFullscreenOnDisplay(d.GetID()) if d else window.SetFullscreen(True)
        window.Show()
    def OnWindowDestroyed(self, window): self.destroyed = True
    def IsFrameless(self, window): return True
    def GetLinuxWindowProperties(self, window): return {'wayland_app_id': self.app_id}
    # browser view delegate
    def OnBrowserCreated(self, bv, browser): browser.SetClientCallback('OnBeforeClose', self.on_before_close)
    def on_before_close(self, browser): self.before_close = True

names = a.outputs.split(',') if a.outputs else sorted(outputs())
cef.Initialize(settings={'log_severity': cef.LOGSEVERITY_ERROR, 'cache_path': '/tmp/views-test-profile', 'background_color': 0xFF000000},
               switches={'no-sandbox': '', 'ignore-gpu-blocklist': ''})
check(cef.Display.GetDisplayCount() == len(outputs()), 'CEF sees %d displays (compositor: %d)' % (cef.Display.GetDisplayCount(), len(outputs())))
for d in cef.Display.GetAllDisplays(): print('  display', d.GetID(), d.GetBounds(), repr(d.GetLabel()))
wins = [Win(n, i) for i, n in enumerate(names)]
pump(5)
out = outputs()
check(len(toplevels()) == len(wins), 'compositor lists %d toplevels: %s' % (len(toplevels()), toplevels()))
for w in wins:
    o = out[w.output]; x, y = o['position']['x'], o['position']['y']
    mode = [m for m in o['modes'] if m['current']][0]
    check(w.window.IsFullscreen(), '%s: fullscreen' % w.output)
    disp = w.window.GetDisplay()
    check(disp and re.search(r'\(%s( via [^)]*)?\)$' % w.output, disp.GetLabel()), '%s: window.GetDisplay() = %s' % (w.output, disp and disp.GetLabel()))
    b = w.window.GetClientAreaBoundsInScreen()
    # Wayland gives a client no global position: compare the size (the outputs differ in mode)
    check(b[2:] == [mode['width'], mode['height']], '%s: size %s == output mode %s' % (w.output, b[2:], [mode['width'], mode['height']]))
    check(any(w.app_id in l for l in toplevels()), '%s: app_id %s listed' % (w.output, w.app_id))
if a.hotplug:
    w = [w for w in wins if w.output == a.hotplug][0]
    run('wlr-randr', '--output', a.hotplug, '--off'); pump(3)
    check(a.hotplug not in outputs(), '%s off' % a.hotplug)
    print('  displays while off:', [d.GetLabel() for d in cef.Display.GetAllDisplays()], 'window on', w.window.GetDisplay() and w.window.GetDisplay().GetLabel())
    run('wlr-randr', '--output', a.hotplug, '--on'); pump(3)
    check(a.hotplug in outputs(), '%s on again' % a.hotplug)
    d = cef.Display.FindByOutputName(a.hotplug)
    check(d is not None, 'display %s known again (id %s)' % (a.hotplug, d and d.GetID()))
    if d:
        w.window.SetFullscreenOnDisplay(d.GetID()); pump(3)
        disp = w.window.GetDisplay()
        check(disp and re.search(r'\(%s( via [^)]*)?\)$' % a.hotplug, disp.GetLabel()), 'window moved back to %s: %s' % (a.hotplug, disp and disp.GetLabel()))
        check(w.window.IsFullscreen(), 'still fullscreen')
        m = [m for m in outputs()[a.hotplug]['modes'] if m['current']][0]
        check(w.window.GetClientAreaBoundsInScreen()[2:] == [m['width'], m['height']], 'moved window has the size of %s' % a.hotplug)
for w in wins: w.window.Close()
pump(5)
check(all(w.destroyed for w in wins), 'OnWindowDestroyed for all')
check(all(w.before_close for w in wins), 'OnBeforeClose for all')
check(len(toplevels()) == 0, 'no toplevel left: %s' % toplevels())
cef.Shutdown()
sys.exit(1 if fails else 0)
