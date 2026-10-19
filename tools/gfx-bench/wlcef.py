# Standalone CEF browser (no Qt, no X11 parent) for Wayland experiments. usage: wlcef.py URL  (env WLCEF_EXTRA="k=v,k2=v2" adds switches)
import os, sys
from cefpython3 import cefpython as cef
sw = {'no-proxy-server': '', 'disable-extensions': '', 'autoplay-policy': 'no-user-gesture-required',
      'remote-debugging-address': '127.0.0.1', 'user-data-dir': '/tmp/wlcef-profile', 'disable-web-security': '',
      'allow-file-access-from-files': '', 'ignore-gpu-blocklist': '', 'ozone-platform': 'wayland', 'no-sandbox': '',
      'enable-features': 'AcceleratedVideoDecodeLinuxGL,AcceleratedVideoDecodeLinuxZeroCopyGL,VaapiVideoDecoder'}
for kv in filter(None, os.environ.get('WLCEF_EXTRA', '').split(',')):
    k, _, v = kv.partition('='); sw[k] = v
cef.Initialize(settings={'windowless_rendering_enabled': False, 'remote_debugging_port': 9223, 'log_severity': cef.LOGSEVERITY_WARNING,
                         'cache_path': '/tmp/wlcef-profile', 'background_color': 0xFF000000}, switches=sw)
b = cef.CreateBrowserSync(window_info=cef.WindowInfo(), url=sys.argv[1])
cef.MessageLoop()
cef.Shutdown()
