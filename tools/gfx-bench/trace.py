# Chrome trace of a webgl_clear page at 1080p; summarises where the frame time goes.  usage: trace.py [page] [seconds]
import asyncio, json, sys, urllib.request, websockets, collections
BASE='http://127.0.0.1:9223'
async def main(page, secs):
    t=json.load(urllib.request.urlopen(urllib.request.Request(BASE+'/json/new?about:blank',method='PUT')))
    br=await websockets.connect(json.load(urllib.request.urlopen(BASE+'/json/version'))['webSocketDebuggerUrl'],max_size=None)
    ws=await websockets.connect(t['webSocketDebuggerUrl'],max_size=None); n=[0]; events=[]; done=asyncio.Event()
    async def call(s,m,**p):
        n[0]+=1; i=n[0]; await s.send(json.dumps({'id':i,'method':m,'params':p}))
        while True:
            d=json.loads(await s.recv())
            if d.get('id')==i: return d.get('result')
    wid=await call(br,'Browser.getWindowForTarget',targetId=t['id'])
    await call(br,'Browser.setWindowBounds',windowId=wid['windowId'],bounds={'left':0,'top':0,'width':1920,'height':1080})
    await call(ws,'Page.navigate',url='file:///tmp/vp/raf_%s.html'%page); await asyncio.sleep(4)
    async def reader():
        async for m in br:
            d=json.loads(m)
            if d.get('method')=='Tracing.dataCollected': events.extend(d['params']['value'])
            if d.get('method')=='Tracing.tracingComplete': done.set(); return
    r=asyncio.ensure_future(reader())
    n[0]+=1; await br.send(json.dumps({'id':n[0],'method':'Tracing.start','params':{'traceConfig':{'includedCategories':['gpu','viz','cc','toplevel','benchmark','latencyInfo','gpu.angle','disabled-by-default-gpu.service'],'recordMode':'recordContinuously'}}}))
    await asyncio.sleep(secs)
    n[0]+=1; await br.send(json.dumps({'id':n[0],'method':'Tracing.end'})); await asyncio.wait_for(done.wait(),60)
    urllib.request.urlopen(BASE+'/json/close/'+t['id']).read()
    st=collections.defaultdict(lambda:[0,0.0,0.0])
    for e in events:
        if e.get('ph')=='X' and 'dur' in e:
            s=st[(e['name'])]; s[0]+=1; s[1]+=e['dur']; s[2]=max(s[2],e['dur'])
    print('events',len(events),'| top by total time (name  count  avg_ms  max_ms)')
    for k,v in sorted(st.items(),key=lambda kv:-kv[1][1])[:28]: print('  %-62s %5d %8.2f %8.2f'%(k[:62],v[0],v[1]/v[0]/1000,v[2]/1000))
asyncio.run(main(sys.argv[1] if len(sys.argv)>1 else 'webgl_clear', int(sys.argv[2]) if len(sys.argv)>2 else 3))
