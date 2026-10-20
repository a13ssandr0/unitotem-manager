#!/usr/bin/env python3
"""Smoke test of the CEF Views API on Wayland: open a frameless window on a given output, report, close it.

usage: views_open.py [--output NAME] [--app-id ID] [--seconds N] [--url URL]
Needs a running compositor (compositor.sh start) and WAYLAND_DISPLAY=/run/unitotem/xdg/wayland-0 (no XDG_RUNTIME_DIR).
"""
import argparse, os, sys, time
from cefpython3 import cefpython as cef

ap = argparse.ArgumentParser()
ap.add_argument('--output')
ap.add_argument('--app-id', default='org.unitotem.test')
ap.add_argument('--seconds', type=float, default=8)
ap.add_argument('--url', default='data:text/html,<body style="background:%23246;color:white;font:60px sans-serif">Views on Wayland</body>')
a = ap.parse_args()
events = []

def log(*m):
    print('%.2f' % time.monotonic(), *m, flush=True)

class Delegate:
    def OnWindowCreated(self, window):
        log('OnWindowCreated', window)
        window.AddChildView(self.view)
        window.Show()
        window.SetFullscreen(True)
    def OnWindowDestroyed(self, window): log('OnWindowDestroyed'); events.append('destroyed')
    def OnWindowBoundsChanged(self, window, b): log('OnWindowBoundsChanged', b)
    def OnWindowFullscreenTransition(self, window, done): log('fullscreen transition', done)
    def IsFrameless(self, window): return True
    def GetInitialShowState(self, window): return cef.SHOW_STATE_FULLSCREEN
    def GetLinuxWindowProperties(self, window): return {'wayland_app_id': a.app_id}

class ViewDelegate:
    def OnBrowserCreated(self, bv, browser):
        log('OnBrowserCreated', browser.GetIdentifier()); browser.SetClientHandler(Handler())
    def OnBrowserDestroyed(self, bv, browser): log('OnBrowserDestroyed')

class Handler:
    def OnBeforeClose(self, browser): log('OnBeforeClose'); events.append('beforeclose')

cef.Initialize(settings={'log_severity': cef.LOGSEVERITY_WARNING, 'cache_path': '/tmp/views-test-profile',
                         'background_color': 0xFF000000},
               switches={'no-sandbox': '', 'ignore-gpu-blocklist': ''})
log('displays', cef.Display.GetAllDisplays())
d = Delegate()
d.view = cef.BrowserView.CreateBrowserView(url=a.url, delegate=ViewDelegate())
w = cef.Window.CreateTopLevelWindow(d)
log('created', w, 'fullscreen', w.IsFullscreen(), 'display', w.GetDisplay())
t0 = time.monotonic(); closed = False
while time.monotonic() - t0 < a.seconds + 3:
    cef.MessageLoopWork(); time.sleep(0.01)
    if not closed and time.monotonic() - t0 > a.seconds:
        log('closing'); w.Close(); closed = True
log('events', events)
cef.Shutdown()
