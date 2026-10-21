#!/usr/bin/env python3
"""Regression check of the pre-existing X11 paths of cefpython (they must not change with the Views work), on an X session:
 1. WindowInfo.SetAsChild(winId of a Qt/PySide6 widget): the browser's X window must be a child of the widget's window
 2. CreateBrowserSync() with no parent window (hello_world.py style): CEF's own native top-level window
 3. Browser.CloseBrowser() of both: OnBeforeClose fires
usage: x11_embed.py   (env DISPLAY, XAUTHORITY; QT_QPA_PLATFORM=xcb)"""
import os, sys, time
os.environ['QT_QPA_PLATFORM'] = 'xcb'
from cefpython3 import cefpython as cef
from PySide6 import QtCore, QtWidgets
from Xlib import display as xdisplay
xd = xdisplay.Display()
res = []
def check(ok, what):
    print(('PASS ' if ok else 'FAIL ') + what, flush=True); res.append(ok)
closed = []
class H:
    def OnBeforeClose(self, browser): closed.append(browser.GetIdentifier())
app = QtWidgets.QApplication(sys.argv)
cef.Initialize(settings={'log_severity': cef.LOGSEVERITY_ERROR, 'cache_path': '/tmp/x11-embed-profile'}, switches={'no-sandbox': ''})
print('ozone-platform switch:', cef.GetCommandLineSwitch('ozone-platform') if hasattr(cef, 'GetCommandLineSwitch') else '?', flush=True)
def pump(s):
    t = time.monotonic()
    while time.monotonic() - t < s: cef.MessageLoopWork(); app.processEvents(); time.sleep(0.01)
w = QtWidgets.QWidget(); w.resize(800, 600); w.show(); pump(1)
wi = cef.WindowInfo(); wi.SetAsChild(int(w.winId()), [0, 0, 800, 600])
b1 = cef.CreateBrowserSync(window_info=wi, url='data:text/html,embedded'); b1.SetClientHandler(H())
pump(3)
h = b1.GetWindowHandle()
parent = xd.create_resource_object('window', h).query_tree().parent.id
check(parent == int(w.winId()), 'SetAsChild: CEF window %#x is a child of the Qt window %#x (parent %#x)' % (h, int(w.winId()), parent))
b2 = cef.CreateBrowserSync(url='data:text/html,toplevel', window_title='x11 toplevel'); b2.SetClientHandler(H())
pump(3)
h2 = b2.GetWindowHandle(); win2 = xd.create_resource_object('window', h2)
root = xd.screen().root
check(win2.query_tree().parent.id == root.id, 'no parent: CEF native window %#x is a top-level window (parent root)' % h2)
props = [win2.get_full_property(xd.intern_atom(a), 0) for a in ('_NET_WM_NAME', 'WM_NAME')]
name = next((p.value.decode() if isinstance(p.value, bytes) else str(p.value) for p in props if p is not None and p.value), None)
print('  title properties:', [p and p.value for p in props], flush=True)
print('  info (not asserted, identical before the Views work): window title property %r' % name, flush=True)
b2.CloseBrowser(True); b1.CloseBrowser(True); pump(3)
check(len(closed) == 2, 'OnBeforeClose for both browsers: %s' % closed)
cef.Shutdown()
sys.exit(0 if all(res) else 1)
