#!/usr/bin/env python3
"""cef.Display.AddObserver() on a real hot-plug: one frameless fullscreen window per output; OUTPUT is switched off and on again with
wlr-randr; the windows follow OnDisplayAdded / OnDisplayRemoved with no polling. Prints the timings (all in ms, from the moment the
wlr-randr command is started): output removed, display added, FindByOutputName() found it, the new window on the right output.

usage: observer_hotplug.py OUTPUT      (env as outputs.py; at least two outputs so that the compositor keeps running)
"""
import json, os, re, subprocess, sys, time
from cefpython3 import cefpython as cef

OUT = sys.argv[1]
WL = os.environ.get('WL_TOOLS', '/var/lib/unitotem/wl/usr/bin')
ENV = dict(os.environ, LD_LIBRARY_PATH='/var/lib/unitotem/wl/extra')
t0 = [0.0]
log = []
fails = []
def ms(): return round((time.monotonic() - t0[0]) * 1000)
def note(*a):
    log.append((ms(), ' '.join(str(x) for x in a))); print('%6d ms  %s' % (ms(), ' '.join(str(x) for x in a)), flush=True)
def check(ok, what):
    print(('PASS ' if ok else 'FAIL ') + what, flush=True)
    if not ok: fails.append(what)
def wlr(*args): return subprocess.Popen([os.path.join(WL, 'wlr-randr')] + list(args), env=ENV, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
def name_of(d):
    m = re.search(r'\(([^ )]+)( via [^)]*)?\)$', d.GetLabel()); return m.group(1) if m else None
def pump(sec):
    end = time.monotonic() + sec
    while time.monotonic() < end: cef.MessageLoopWork(); time.sleep(0.002)

class Screens:
    def __init__(self): self.windows = {}; self.destroyed = 0; self.polls = 0
    def open(self, display):
        n = name_of(display)
        if display.GetID() in self.windows: return
        view = cef.BrowserView.CreateBrowserView(url='data:text/html,<body style="background:%23063;color:white;font:8vw sans-serif">' + str(n))
        self.windows[display.GetID()] = (n, cef.Window.CreateTopLevelWindow(Delegate(view, display, self)))
    def OnDisplayAdded(self, display):
        n = name_of(display)
        found = cef.Display.FindByOutputName(n) if n else None
        note('OnDisplayAdded', n, 'id', display.GetID(), '-> FindByOutputName in the callback:', 'found' if found else 'NOT found')
        self.added = (ms(), found is not None)
        self.open(display)
    def OnDisplayRemoved(self, display):
        n = name_of(display); note('OnDisplayRemoved', n, 'id', display.GetID())
        self.removed = ms()
        w = self.windows.pop(display.GetID(), None)
        if w and not w[1].IsClosed(): w[1].Close()
    def OnDisplayMetricsChanged(self, display): pass

class Delegate:
    def __init__(self, view, display, owner): self.view, self.display, self.owner = view, display, owner
    def OnWindowCreated(self, window):
        window.SetToFillLayout(); window.AddChildView(self.view); window.SetFullscreenOnDisplay(self.display.GetID()); window.Show()
        note('window created for', name_of(self.display))
    def OnWindowDestroyed(self, window): self.owner.destroyed += 1
    def IsFrameless(self, window): return True
    def GetLinuxWindowProperties(self, window): return {'wayland_app_id': 'org.unitotem.test.observer'}

cef.Initialize(settings={'log_severity': cef.LOGSEVERITY_ERROR, 'cache_path': '/tmp/views-test-profile', 'background_color': 0xFF000000},
               switches={'no-sandbox': '', 'ignore-gpu-blocklist': ''})
s = Screens(); s.added = None; s.removed = None
cef.Display.AddObserver(s)
for d in cef.Display.GetAllDisplays(): s.open(d)
pump(4)
names = sorted(n for n, _ in s.windows.values())
check(OUT in names and len(names) >= 2, 'a window on each output at start: %s' % names)
t0[0] = time.monotonic(); p = wlr('--output', OUT, '--off'); note('wlr-randr --off started')
pump(3)
check(s.removed is not None, 'OnDisplayRemoved fired (after %s ms)' % s.removed)
check(OUT not in [n for n, _ in s.windows.values()], 'the window of %s was closed' % OUT)
t0[0] = time.monotonic(); p = wlr('--output', OUT, '--on'); note('wlr-randr --on started')
pump(4)
check(s.added is not None, 'OnDisplayAdded fired (after %s ms)' % (s.added and s.added[0]))
check(s.added is not None and s.added[1], 'FindByOutputName(%s) already found the display inside OnDisplayAdded' % OUT)
w = [x for x in s.windows.values() if x[0] == OUT]
check(len(w) == 1, 'a new window was opened on %s' % OUT)
if w:
    d = w[0][1].GetDisplay()
    check(d is not None and name_of(d) == OUT, 'the new window is on %s (GetDisplay: %s)' % (OUT, d and name_of(d)))
    check(w[0][1].IsFullscreen(), 'the new window is fullscreen')
cef.Display.RemoveObserver(s)
for n, win in list(s.windows.values()): win.Close()
pump(3)
check(not subprocess.run([os.path.join(WL, 'wlrctl'), 'toplevel', 'list'], env=ENV, capture_output=True, text=True).stdout.strip(), 'no toplevel left')
cef.Shutdown()
sys.exit(1 if fails else 0)
