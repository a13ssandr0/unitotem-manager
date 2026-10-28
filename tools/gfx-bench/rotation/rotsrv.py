#!/usr/bin/env python3
"""
Runs ON the board: test pages for the rotation benchmark, served on 127.0.0.1:8099.

    rotsrv.py DIR

`/run.html` is the one asset the window shows: it asks `/workload` which workload to run and reloads when that changes, so the
harness switches workloads without touching the playlist. Every page reports once a second to `POST /stat` (frames really
drawn, slow frames, dropped video frames, its own size); `GET /stats` returns what was reported. Videos are served from DIR
(with ranges). Set a workload: `GET /set?w=NAME`.
"""
import json
import sys
import time

from aiohttp import web

DIR = sys.argv[1]
state = {'w': 'idle'}
stats = []

PAGE = r"""<!doctype html><meta charset=utf-8><body style="margin:0;background:#000;overflow:hidden">
<script>
const W = '%WORKLOAD%';
let drawn = 0, slow = 0, last = 0, worst = 0, videos = [];
function tick(t) {
  if (last) { const d = t - last; if (d > 25) slow++; if (d > worst) worst = d; }
  last = t; requestAnimationFrame(tick);
}
requestAnimationFrame(tick);
setInterval(() => {
  let tot = 0, drop = 0;
  for (const v of videos) { const q = v.getVideoPlaybackQuality(); tot += q.totalVideoFrames; drop += q.droppedVideoFrames; }
  fetch('/stat', {method: 'POST', mode: 'no-cors', body: JSON.stringify({w: W, drawn, slow, worst, tot, drop, vw: innerWidth, vh: innerHeight, t: Date.now()})});
  worst = 0;
}, 1000);
setInterval(async () => { try { if ((await (await fetch('/workload')).text()) !== W) location.reload(); } catch (e) {} }, 500);
function video(n) {
  document.body.style.display = 'grid';
  document.body.style.gridTemplate = n === 1 ? '100vh / 100vw' : '50vh 50vh / 50vw 50vw';
  for (let i = 0; i < n; i++) {
    const v = document.createElement('video');
    v.muted = v.loop = v.autoplay = true; v.src = '/' + (W === 'video4k' ? 'v2160' : 'v1080') + '.mp4';
    v.style.cssText = 'width:100%;height:100%;object-fit:cover'; document.body.appendChild(v); videos.push(v);
    v.addEventListener('timeupdate', () => {}); (function f() { v.requestVideoFrameCallback(() => { drawn++; f(); }); })();
  }
}
function canvas2d() {
  const c = document.createElement('canvas'); c.width = innerWidth; c.height = innerHeight;
  c.style.cssText = 'width:100vw;height:100vh;display:block'; document.body.appendChild(c);
  const g = c.getContext('2d'); let n = 0;
  (function f() { g.fillStyle = 'hsl(' + (n * 5 % 360) + ',70%,40%)'; g.fillRect(0, 0, c.width, c.height);
    g.fillStyle = '#fff'; g.fillRect((n * 9) % c.width, (n * 5) % c.height, 120, 120); n++; drawn++; requestAnimationFrame(f); })();
}
function webgl(layers) {
  const c = document.createElement('canvas'); c.width = innerWidth; c.height = innerHeight;
  c.style.cssText = 'width:100vw;height:100vh;display:block'; document.body.appendChild(c);
  const gl = c.getContext('webgl2', {antialias: false}); if (!gl) { window.__noctx = true; return; }
  const sh = (t, s) => { const o = gl.createShader(t); gl.shaderSource(o, s); gl.compileShader(o); return o; };
  const p = gl.createProgram();
  gl.attachShader(p, sh(gl.VERTEX_SHADER, '#version 300 es\nin vec2 a;void main(){gl_Position=vec4(a,0,1);}'));
  gl.attachShader(p, sh(gl.FRAGMENT_SHADER, '#version 300 es\nprecision mediump float;uniform float t;out vec4 o;void main(){vec2 u=gl_FragCoord.xy/512.;float v=0.;for(int i=0;i<%LOOP%;i++){v+=sin(u.x*float(i)+t)*cos(u.y*float(i)-t);}o=vec4(.5+.5*sin(v+t),.5+.5*cos(v),.5,.3);}'.replace('%LOOP%', layers > 1 ? '40' : '4')));
  gl.linkProgram(p); gl.useProgram(p);
  const b = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, b);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
  gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
  gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
  const loc = gl.getUniformLocation(p, 't'); let n = 0;
  (function f() { gl.uniform1f(loc, n++ / 30); for (let i = 0; i < layers; i++) gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4); drawn++; requestAnimationFrame(f); })();
}
function crawl() {
  const d = document.createElement('div');
  d.style.cssText = 'position:absolute;top:45vh;white-space:nowrap;font:bold 8vh sans-serif;color:#fff;will-change:transform';
  d.textContent = Array(40).fill('Breaking news crawl lane   •   ').join('');
  document.body.appendChild(d); d.animate([{transform: 'translateX(100vw)'}, {transform: 'translateX(-6000px)'}], {duration: 60000, iterations: Infinity});
  (function f() { drawn++; requestAnimationFrame(f); })();
}
({idle() { (function f() { drawn++; requestAnimationFrame(f); })(); }, video: () => video(1), video4k: () => video(1), video4: () => video(4),
  canvas: canvas2d, webgl: () => webgl(1), webgl_heavy: () => webgl(8), crawl})[W]();
</script>"""


async def run(request):
    return web.Response(text=PAGE.replace('%WORKLOAD%', state['w']), content_type='text/html')


async def workload(request):
    return web.Response(text=state['w'])


async def set_workload(request):
    state['w'] = request.query['w']
    return web.Response(text=state['w'])


async def stat(request):
    entry = json.loads(await request.text())
    entry['at'] = time.time()
    stats.append(entry)
    del stats[:-600]
    return web.Response(text='ok')


async def get_stats(request):
    since = float(request.query.get('since', 0))
    return web.json_response([s for s in stats if s['at'] >= since])


app = web.Application()
app.add_routes([web.get('/run.html', run), web.get('/workload', workload), web.get('/set', set_workload),
                web.post('/stat', stat), web.get('/stats', get_stats), web.static('/', DIR)])
web.run_app(app, host='127.0.0.1', port=8099, print=None)
