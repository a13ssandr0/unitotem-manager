# Runs ON venus: Aquarium with growing fish counts, fps + GPU frequency + CPU per step.
import os, asyncio, json, sys, time, urllib.request, subprocess, glob
import websockets
BASE = 'http://127.0.0.1:9223'
def tick():
    t = 0
    for p in subprocess.run(['pgrep', '-f', 'unitotem-manager|cefpython3|subprocess --type'], capture_output=True, text=True).stdout.split():
        try: f = open('/proc/%s/stat' % p).read().rsplit(')', 1)[1].split(); t += int(f[11]) + int(f[12])
        except Exception: pass
    return t
def rd(p):
    try: return open(p).read().strip()
    except Exception: return '?'
def temps():
    return max([int(rd(f)) // 1000 for f in glob.glob('/sys/class/thermal/thermal_zone*/temp') if rd(f).lstrip('-').isdigit()] or [0])
class Cdp:
    def __init__(self, ws): self.ws, self.n, self.futs = ws, 0, {}
    async def run(self):
        async for m in self.ws:
            d = json.loads(m)
            if 'id' in d and d['id'] in self.futs: self.futs.pop(d['id']).set_result(d)
    async def call(self, method, **params):
        self.n += 1; f = asyncio.get_event_loop().create_future(); self.futs[self.n] = f
        await self.ws.send(json.dumps({'id': self.n, 'method': method, 'params': params}))
        return (await f).get('result')
async def connect(url):
    ws = await websockets.connect(url, max_size=None); c = Cdp(ws); asyncio.ensure_future(c.run()); return c
async def ev(c, e):
    r = await c.call('Runtime.evaluate', expression=e, returnByValue=True)
    return r.get('result', {}).get('value')
async def main(counts, secs):
    t = json.load(urllib.request.urlopen(urllib.request.Request(BASE + '/json/new?about:blank', method='PUT')))
    c = await connect(t['webSocketDebuggerUrl'])
    ver = json.load(urllib.request.urlopen(BASE + '/json/version'))
    br = await connect(ver['webSocketDebuggerUrl'])
    w = await br.call('Browser.getWindowForTarget', targetId=t['id'])
    await br.call('Browser.setWindowBounds', windowId=w['windowId'], bounds={'left': int(os.environ.get('GL_X', 0)), 'top': 0, 'width': int(os.environ.get('GL_W', 1920)), 'height': int(os.environ.get('GL_H', 1080))})
    await c.call('Page.navigate', url='https://webglsamples.org/aquarium/aquarium.html')
    await asyncio.sleep(15)
    if os.environ.get('GL_NATIVE'):
        print('canvas before:', await ev(c, 'document.querySelector("canvas").width+"x"+document.querySelector("canvas").height'))
        await ev(c, '(()=>{const k=document.querySelector("canvas");k.width=k.clientWidth*devicePixelRatio;k.height=k.clientHeight*devicePixelRatio;window.dispatchEvent(new Event("resize"))})()')
        await asyncio.sleep(2)
        print('canvas after:', await ev(c, 'document.querySelector("canvas").width+"x"+document.querySelector("canvas").height'))
    print('viewport:', await ev(c, 'innerWidth+"x"+innerHeight'))
    print('options:', await ev(c, 'JSON.stringify([...document.querySelectorAll("#optionList li, #optionsContainer li, [id^=setSetting]")].map(e=>e.id+":"+e.textContent.trim().slice(0,12)).slice(0,40))'))
    for n in counts:
        ok = await ev(c, '''(()=>{const l=[...document.querySelectorAll("[id^=setSetting],#optionList li,li")].find(e=>e.textContent.trim()==="%d");if(!l)return false;l.click();return true})()''' % n)
        await asyncio.sleep(4)
        a = tick(); t0 = time.time()
        fr = []
        for _ in range(secs):
            await asyncio.sleep(1)
            fr.append(await ev(c, 'document.getElementById("fps").textContent'))
        cpu = (tick() - a) / (time.time() - t0)
        print('fish %6d set=%s fps=%s gt=%sMHz temp=%sC cpu=%d%%' % (n, ok, fr[-5:], rd('/sys/class/drm/card0/gt_cur_freq_mhz'), temps(), cpu), flush=True)
    urllib.request.urlopen(BASE + '/json/close/' + t['id']).read()
asyncio.run(main([int(x) for x in sys.argv[2].split(',')], int(sys.argv[1])))
