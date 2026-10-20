# Timeline of Chromium's Wayland frame pipeline (frame callbacks, swaps, BeginFrames) in the viewer's own window.
# usage: wltrace.py [page] [window_ms]   (needs /tmp/vp/raf_<page>.html: run raf.py once first, page = hash printed in /tmp/vp)
import asyncio, json, sys, urllib.request, websockets, glob
BASE='http://127.0.0.1:9223'
CATS=['wayland','viz','gpu','toplevel','benchmark','cc']
KEEP=('CreateFrameCallback','WaitForFrameCallback','Display::DrawAndSwap','Display::DrawAndSwap','BeginFrame','SwapBuffers','OnFrameDone','FrameCallback','SkiaOutputSurfaceImplOnGpu::SwapBuffers','DisplayScheduler::BeginFrame','SubmitCompositorFrame','PresentationFeedback','BufferQueue','DidReceiveSwapBuffersAck','OnSubmission','OnPresentation','SwapCompletion')
async def main(fn, win):
    ts=json.load(urllib.request.urlopen(BASE+'/json')); t=next(p for p in ts if p['type']=='page' and 'unitotem-first-boot' not in p['url'])
    br=await websockets.connect(json.load(urllib.request.urlopen(BASE+'/json/version'))['webSocketDebuggerUrl'],max_size=None)
    ws=await websockets.connect(t['webSocketDebuggerUrl'],max_size=None); n=[0]; events=[]; done=asyncio.Event()
    async def call(s,m,**p):
        n[0]+=1; i=n[0]; await s.send(json.dumps({'id':i,'method':m,'params':p}))
        while True:
            d=json.loads(await s.recv())
            if d.get('id')==i: return d.get('result')
    await call(ws,'Page.navigate',url='file://'+fn); await asyncio.sleep(5)
    async def reader():
        async for m in br:
            d=json.loads(m)
            if d.get('method')=='Tracing.dataCollected': events.extend(d['params']['value'])
            if d.get('method')=='Tracing.tracingComplete': done.set(); return
    asyncio.ensure_future(reader())
    n[0]+=1; await br.send(json.dumps({'id':n[0],'method':'Tracing.start','params':{'traceConfig':{'includedCategories':CATS,'recordMode':'recordContinuously'}}}))
    await asyncio.sleep(3)
    n[0]+=1; await br.send(json.dumps({'id':n[0],'method':'Tracing.end'})); await asyncio.wait_for(done.wait(),60)
    ev=[e for e in events if e.get('ph') in ('I','X','i','b','e','n') and any(k in e.get('name','') for k in KEEP)]
    ev.sort(key=lambda e:e['ts'])
    names={}
    for e in ev: names[e['name']]=names.get(e['name'],0)+1
    print('names:',names)
    mid=ev[len(ev)//2]['ts']
    for e in ev:
        if mid<=e['ts']<mid+win*1000: print('%8.2f ms %-3s pid%-6s %-50s %s'%((e['ts']-mid)/1000,e['ph'],e['pid'],e['name'][:50],('dur=%.2f'%(e['dur']/1000)) if 'dur' in e else ''))
asyncio.run(main(sys.argv[1], int(sys.argv[2]) if len(sys.argv)>2 else 120))
