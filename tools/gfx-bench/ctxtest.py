import asyncio, json, urllib.request, websockets
BASE='http://127.0.0.1:9223'
JS='''(()=>{const out={};const tries={"webgl default":["webgl",{}],"webgl2 default":["webgl2",{}],"webgl2 aa:false xr":["webgl2",{antialias:false,xrCompatible:true}],"webgl2 xr":["webgl2",{xrCompatible:true}],"webgl aa:false":["webgl",{antialias:false}],"webgl stencil":["webgl",{stencil:true,depth:true,alpha:false}],"webgl2 preserve":["webgl2",{preserveDrawingBuffer:true}]};
for(const [k,[t,a]] of Object.entries(tries)){const c=document.createElement("canvas");c.width=64;c.height=64;const g=c.getContext(t,a);out[k]=g?("ok aa="+g.getContextAttributes().antialias):"NULL"}return JSON.stringify(out)})()'''
async def main():
    t=json.load(urllib.request.urlopen(urllib.request.Request(BASE+'/json/new?about:blank',method='PUT')))
    ws=await websockets.connect(t['webSocketDebuggerUrl'],max_size=None)
    await ws.send(json.dumps({'id':1,'method':'Runtime.evaluate','params':{'expression':JS,'returnByValue':True}}))
    while True:
        d=json.loads(await ws.recv())
        if d.get('id')==1: print(d['result']['result'].get('value')); break
    urllib.request.urlopen(BASE+'/json/close/'+t['id']).read()
asyncio.run(main())
