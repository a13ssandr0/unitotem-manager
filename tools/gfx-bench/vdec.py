# Plays a video fullscreen in a tab placed on a given screen; reports decoder, dropped frames, CPU of the manager tree, GPU/CPU counters.
import asyncio, json, sys, time, subprocess, urllib.request, websockets
sys.path.insert(0, '/tmp/vp'); import hw
BASE='http://127.0.0.1:9223'
def tick():
    t=0
    for p in subprocess.run(['pgrep','-f','unitotem-manager|cefpython3|subprocess --type'],capture_output=True,text=True).stdout.split():
        try: f=open('/proc/%s/stat'%p).read().rsplit(')',1)[1].split(); t+=int(f[11])+int(f[12])
        except Exception: pass
    return t
async def main(video, x, w, h, secs):
    open('/tmp/vp/vdec.html','w').write('<!doctype html><body style="margin:0;background:#000"><video id=v autoplay muted loop style="width:100vw;height:100vh;object-fit:contain" src="file://%s"></video>' % video)
    t=json.load(urllib.request.urlopen(urllib.request.Request(BASE+'/json/new?about:blank',method='PUT')))
    br=await websockets.connect(json.load(urllib.request.urlopen(BASE+'/json/version'))['webSocketDebuggerUrl'],max_size=None)
    ws=await websockets.connect(t['webSocketDebuggerUrl'],max_size=None); n=[0]; props={}
    async def call(s,m,**p):
        n[0]+=1; i=n[0]; await s.send(json.dumps({'id':i,'method':m,'params':p}))
        while True:
            d=json.loads(await s.recv())
            if d.get('id')==i: return d.get('result')
            if d.get('method')=='Media.playerPropertiesChanged':
                for q in d['params']['properties']: props[q['name']]=q['value']
    ev=lambda e: call(ws,'Runtime.evaluate',returnByValue=True,expression=e)
    wid=await call(br,'Browser.getWindowForTarget',targetId=t['id'])
    await call(br,'Browser.setWindowBounds',windowId=wid['windowId'],bounds={'left':x,'top':0,'width':w,'height':h})
    await call(ws,'Media.enable')
    await call(ws,'Page.navigate',url='file:///tmp/vp/vdec.html')
    await asyncio.sleep(8)
    q0=(await ev('(()=>{const v=document.getElementById("v"),q=v.getVideoPlaybackQuality();return {t:v.currentTime,vw:v.videoWidth,vh:v.videoHeight,tot:q.totalVideoFrames,dr:q.droppedVideoFrames,win:[innerWidth,innerHeight]}})()'))['result']['value']
    print('video %dx%d window %s' % (q0['vw'],q0['vh'],q0['win']),flush=True)
    s=hw.Sampler(); a=tick(); t0=time.time()
    for _ in range(secs):
        await asyncio.sleep(1); s.sample()
        await call(ws,'Runtime.evaluate',expression='0')   # drain events
    cpu=(tick()-a)/(time.time()-t0)
    q1=(await ev('(()=>{const v=document.getElementById("v"),q=v.getVideoPlaybackQuality();return {t:v.currentTime,tot:q.totalVideoFrames,dr:q.droppedVideoFrames}})()'))['result']['value']
    dt=q1['tot']-q0['tot']; dd=q1['dr']-q0['dr']
    print('decoder=%s platform=%s' % (props.get('kVideoDecoderName'), props.get('kIsPlatformVideoDecoder')))
    print('window of %ds: frames %d (%.1f fps) dropped %d (%.1f%%) | tree cpu %d%% of one core' % (secs,dt,dt/secs,dd,100*dd/max(dt,1),cpu))
    print(s.report())
    urllib.request.urlopen(BASE+'/json/close/'+t['id']).read()
asyncio.run(main(sys.argv[1],*map(int,sys.argv[2:6])))
