# rAF cadence of REAL WebGL/2D draws (counts only frames drawn, reports missing context), at the actual window size.
import asyncio, json, sys, urllib.request, websockets
BASE='http://127.0.0.1:9223'
def page(kind, attrs='{}'):
    if kind=='webgl1': ctx='c.getContext("webgl",%s)'%attrs; d='g.clearColor((n++%60)/60,0,0,1);g.clear(g.COLOR_BUFFER_BIT)'
    elif kind=='webgl2': ctx='c.getContext("webgl2",%s)'%attrs; d='g.clearColor((n++%60)/60,0,0,1);g.clear(g.COLOR_BUFFER_BIT)'
    else: ctx='c.getContext("2d")'; d='g.fillStyle="hsl("+(n++*5%360)+",80%,50%)";g.fillRect(0,0,50,50)'
    return '<canvas id=c style="width:100vw;height:100vh;display:block"></canvas><script>const c=document.getElementById("c");c.width=innerWidth;c.height=innerHeight;const g=%s;window.__ok=!!g;window.__d=0;let n=0;(function f(){if(!g)return;%s;window.__d++;requestAnimationFrame(f)})()</script>'%(ctx,d)
PAGES={'canvas2d':page('2d'),'webgl2 default':page('webgl2'),'webgl2 aa:false':page('webgl2','{antialias:false}'),'webgl2 aa+alpha false':page('webgl2','{antialias:false,alpha:false}'),'webgl2 desync':page('webgl2','{antialias:false,desynchronized:true}'),'webgl1 default':page('webgl1'),'webgl1 aa:false':page('webgl1','{antialias:false}')}
if len(sys.argv)>4: PAGES={k:v for k,v in PAGES.items() if k in sys.argv[4].split('|')}
async def main(x,w,h):
    br=await websockets.connect(json.load(urllib.request.urlopen(BASE+'/json/version'))['webSocketDebuggerUrl'],max_size=None)
    for name,html in PAGES.items():
        fn='/tmp/vp/raf_%d.html'%abs(hash(name)); open(fn,'w').write('<!doctype html>'+html)
        t=json.load(urllib.request.urlopen(urllib.request.Request(BASE+'/json/new?about:blank',method='PUT')))
        ws=await websockets.connect(t['webSocketDebuggerUrl'],max_size=None); n=[0]
        async def call(s,m,**p):
            n[0]+=1; i=n[0]; await s.send(json.dumps({'id':i,'method':m,'params':p}))
            while True:
                d=json.loads(await s.recv())
                if d.get('id')==i: return d.get('result')
        ev=lambda e: call(ws,'Runtime.evaluate',returnByValue=True,expression=e)
        if w:
            wid=await call(br,'Browser.getWindowForTarget',targetId=t['id'])
            await call(br,'Browser.setWindowBounds',windowId=wid['windowId'],bounds={'left':x,'top':0,'width':w,'height':h})
        await call(ws,'Page.navigate',url='file://'+fn); await asyncio.sleep(4)
        info=(await ev('JSON.stringify({ok:window.__ok,wh:innerWidth+"x"+innerHeight,d:window.__d})'))['result']['value']; info=json.loads(info)
        await asyncio.sleep(6)
        d1=(await ev('window.__d'))['result']['value']
        print('%-24s [%s] %s' % (name, info['wh'], ('%.1f fps' % ((d1-info['d'])/6)) if info['ok'] else 'NO CONTEXT'),flush=True)
        urllib.request.urlopen(BASE+'/json/close/'+t['id']).read()
asyncio.run(main(*map(int,sys.argv[1:4])))
