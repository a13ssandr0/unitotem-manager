import asyncio, json, sys, urllib.request, websockets
sys.path.insert(0, '/tmp/vp'); import hw
BASE='http://127.0.0.1:9223'
async def main(x, w, h):
    t=json.load(urllib.request.urlopen(urllib.request.Request(BASE+'/json/new?about:blank',method='PUT')))
    br=await websockets.connect(json.load(urllib.request.urlopen(BASE+'/json/version'))['webSocketDebuggerUrl'],max_size=None)
    ws=await websockets.connect(t['webSocketDebuggerUrl'],max_size=None)
    n=[0]
    async def call(s,m,**p):
        n[0]+=1; i=n[0]; await s.send(json.dumps({'id':i,'method':m,'params':p}))
        while True:
            d=json.loads(await s.recv())
            if d.get('id')==i: return d.get('result')
    ev=lambda e: call(ws,'Runtime.evaluate',returnByValue=True,expression=e)
    wid=await call(br,'Browser.getWindowForTarget',targetId=t['id'])
    await call(br,'Browser.setWindowBounds',windowId=wid['windowId'],bounds={'left':x,'top':0,'width':w,'height':h})
    s=hw.Sampler()
    await call(ws,'Page.navigate',url='file:///tmp/vp/glbench.html')
    last=0
    for _ in range(300):
        await asyncio.sleep(1); s.sample()
        r=(await ev('JSON.stringify({d:window.__done,r:window.__res,i:window.__info})'))['result'].get('value')
        if not r: continue
        r=json.loads(r)
        if r['i'] and not last: print('canvas %dx%d %s maxSamples=%s' % (r['i']['W'],r['i']['H'],r['i']['renderer'],r['i']['maxSamples']),flush=True)
        for x_ in r['r'][last:]: print('  %-48s %8s ms  %7s fps  %s' % (x_['name'],x_.get('ms',''),x_.get('fps',''),x_.get('extra','')),flush=True)
        last=len(r['r'])
        if r['d']: break
    print(s.report())
    urllib.request.urlopen(BASE+'/json/close/'+t['id']).read()
asyncio.run(main(int(sys.argv[1]),int(sys.argv[2]),int(sys.argv[3])))
