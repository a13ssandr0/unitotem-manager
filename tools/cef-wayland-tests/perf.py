#!/usr/bin/env python3
"""Frame rate, CPU and memory of a full-screen page in (a) a Views window (the new API) and (b) a Chrome-style window opened through
DevTools (Target.createTarget newWindow, the workaround the viewer uses today), on the same compositor/output.

usage: perf.py --mode views|devtools [--gpu pi|vaapi|none] [--page webgl2|webgl2aa|canvas2d|idle] [--output DP-1] [--seconds 15]
Prints one JSON line: fps (from requestAnimationFrame draws counted by the page), cpu (% of one core, whole process tree), pss_mb.
Env as for outputs.py. The page reports through document.title, read from the DevTools /json list (same for both modes).
"""
import argparse, asyncio, json, os, sys, time, urllib.request
from cefpython3 import cefpython as cef

ap = argparse.ArgumentParser()
ap.add_argument('--mode', required=True, choices=['views', 'devtools'])
ap.add_argument('--gpu', default='none', choices=['none', 'pi', 'vaapi'])
ap.add_argument('--page', default='webgl2', choices=['webgl2', 'webgl2aa', 'canvas2d', 'idle'])
ap.add_argument('--output')
ap.add_argument('--seconds', type=float, default=15)
a = ap.parse_args()
PORT = 9223
GPU = {'none': {}, 'pi': {'use-gl': 'angle', 'use-angle': 'gles', 'webgl-antialiasing-mode': 'none'},
       'vaapi': {'enable-features': 'AcceleratedVideoDecodeLinuxGL,AcceleratedVideoDecodeLinuxZeroCopyGL,VaapiVideoDecoder'}}
if a.gpu == 'pi': os.environ.setdefault('UNITOTEM_NO_SCANOUT_BUFFERS', '1')   # the viewer sets it for the pi profile (patch 0002)

JS = {'webgl2': 'const g=c.getContext("webgl2",{antialias:false});d=()=>{g.clearColor((n%60)/60,0,0,1);g.clear(g.COLOR_BUFFER_BIT)}',
      'webgl2aa': 'const g=c.getContext("webgl2");d=()=>{g.clearColor((n%60)/60,0,0,1);g.clear(g.COLOR_BUFFER_BIT)}',
      'canvas2d': 'const g=c.getContext("2d");d=()=>{g.fillStyle="hsl("+(n*5%360)+",80%,50%)";g.fillRect(0,0,c.width,c.height)}',
      'idle': 'd=()=>{}'}[a.page]
HTML = ('<body style="margin:0;background:#000"><canvas id=c style="width:100vw;height:100vh;display:block"></canvas><script>'
        'const c=document.getElementById("c");c.width=innerWidth;c.height=innerHeight;let n=0,d,last=0,cnt=0;%s;'
        '(function f(t){d();n++;cnt++;if(t-last>=1000){document.title="fps:"+(cnt*1000/(t-last)).toFixed(1)+" "+innerWidth+"x"+innerHeight;last=t;cnt=0}requestAnimationFrame(f)})(0)'
        '</script>') % JS
URL = 'data:text/html;base64,' + __import__('base64').b64encode(HTML.encode()).decode()

def descendants(pid):
    out, todo = [], [pid]
    kids = {}
    for p in os.listdir('/proc'):
        if p.isdigit():
            try: kids.setdefault(int(open('/proc/%s/stat' % p).read().rsplit(')', 1)[1].split()[1]), []).append(int(p))
            except Exception: pass
    while todo:
        x = todo.pop(); out.append(x); todo += kids.get(x, [])
    return out

def ticks(pids):
    t = 0
    for p in pids:
        try: f = open('/proc/%d/stat' % p).read().rsplit(')', 1)[1].split(); t += int(f[11]) + int(f[12])
        except Exception: pass
    return t

def pss_mb(pids):
    t = 0
    for p in pids:
        try:
            for l in open('/proc/%d/smaps_rollup' % p):
                if l.startswith('Pss:'): t += int(l.split()[1])
        except Exception: pass
    return t / 1024

def pump(sec):
    end = time.monotonic() + sec
    while time.monotonic() < end: cef.MessageLoopWork(); time.sleep(0.005)

def targets():
    return json.load(urllib.request.urlopen('http://127.0.0.1:%d/json' % PORT))

class Delegate:
    def OnWindowCreated(self, window):
        window.SetToFillLayout(); window.AddChildView(self.view)
        d = cef.Display.FindByOutputName(a.output) if a.output else None
        window.SetFullscreenOnDisplay(d.GetID()) if d else window.SetFullscreen(True)
        window.Show()
    def IsFrameless(self, window): return True
    def GetLinuxWindowProperties(self, window): return {'wayland_app_id': 'org.unitotem.perf'}

async def devtools_window():
    import websockets
    ver = json.load(urllib.request.urlopen('http://127.0.0.1:%d/json/version' % PORT))
    async with websockets.connect(ver['webSocketDebuggerUrl'], max_size=None) as ws:
        async def call(m, **p):
            call.n += 1; await ws.send(json.dumps({'id': call.n, 'method': m, 'params': p}))
            while True:
                d = json.loads(await ws.recv())
                if d.get('id') == call.n: return d.get('result')
        call.n = 0
        t = await call('Target.createTarget', url=URL, newWindow=True)
        w = await call('Browser.getWindowForTarget', targetId=t['targetId'])
        await call('Browser.setWindowBounds', windowId=w['windowId'], bounds={'windowState': 'fullscreen'})

sw = {'no-sandbox': '', 'ignore-gpu-blocklist': '', 'remote-debugging-address': '127.0.0.1', 'disable-web-security': '',
      'user-data-dir': '/tmp/views-perf-profile', 'autoplay-policy': 'no-user-gesture-required'}
sw.update(GPU[a.gpu])
cef.Initialize(settings={'log_severity': cef.LOGSEVERITY_ERROR, 'cache_path': '/tmp/views-perf-profile', 'remote_debugging_port': PORT,
                         'background_color': 0xFF000000}, switches=sw)
if a.mode == 'views':
    d = Delegate(); d.view = cef.BrowserView.CreateBrowserView(url=URL)
    w = cef.Window.CreateTopLevelWindow(d)
else:
    pump(2); asyncio.run(devtools_window())
pump(6)
me = os.getpid(); pids = descendants(me)
t0, c0 = time.monotonic(), ticks(pids)
titles = []
end = t0 + a.seconds
while time.monotonic() < end:
    pump(1)
    pg = [t for t in targets() if t['type'] == 'page' and t['title'].startswith('fps:')]
    if pg: titles.append(pg[0]['title'])
pids = descendants(me)
cpu = (ticks(pids) - c0) / os.sysconf('SC_CLK_TCK') / (time.monotonic() - t0) * 100
fps = [float(t.split()[0][4:]) for t in titles]
res = {'mode': a.mode, 'gpu': a.gpu, 'page': a.page, 'size': titles[-1].split()[1] if titles else None,
       'fps': round(sum(fps) / len(fps), 1) if fps else None, 'min_fps': min(fps) if fps else None,
       'cpu_pct_core': round(cpu, 1), 'pss_mb': round(pss_mb(pids)), 'processes': len(pids)}
print(json.dumps(res), flush=True)
os._exit(0)
