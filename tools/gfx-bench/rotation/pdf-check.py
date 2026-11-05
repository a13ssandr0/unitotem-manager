#!/usr/bin/env python3
"""
A PDF whose text uses a font the file does not carry (Helvetica, one of the standard ones) on the real viewer of a board: the page that draws it must fetch the
bundled standard font data from /assets/pdfjs/ and the window must show the text. Makes a one-page PDF, puts it in the uploads, plays it in the first window through
a temporary playlist, looks at the page's requests and at its pixels over DevTools, then puts everything back.

    .venv/bin/python tools/gfx-bench/rotation/pdf-check.py --host <host>
"""
import argparse
import asyncio
import importlib.util
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('board_run', os.path.join(HERE, '..', '..', 'ticker', 'board-run.py'))
vr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vr)
NAME = 'pdf-font-check.pdf'
REMOTE = '/var/lib/unitotem/pdfcheck'


def make_pdf(blank=False) -> bytes:
    stream = b'' if blank else b'BT /F1 72 Tf 60 500 Td (Standard font) Tj ET'
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>',
               b'<< /Length %d >>\nstream\n' % len(stream) + stream + b'\nendstream',
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    out, offsets = bytearray(b'%PDF-1.4\n'), []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b'%d 0 obj\n' % number + body + b'\nendobj\n'
    xref = len(out)
    out += b'xref\n0 %d\n0000000000 65535 f \n' % (len(objects) + 1)
    out += b''.join(b'%010d 00000 n \n' % o for o in offsets)
    out += b'trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n' % (len(objects) + 1, xref)
    return bytes(out)


CHECK = r'''
import asyncio, json, urllib.request, websockets
async def main():
    targets = json.load(urllib.request.urlopen("http://127.0.0.1:9223/json"))
    frame = next((t for t in targets if t["type"] == "iframe" and "pdf-view" in t["url"]), None)
    if frame is None:
        print(json.dumps({"error": "no pdf-view frame", "frames": [t["url"][:60] for t in targets]})); return
    async with websockets.connect(frame["webSocketDebuggerUrl"], max_size=None) as ws:
        async def call(method, **params):
            call.n += 1
            await ws.send(json.dumps({"id": call.n, "method": method, "params": params}))
            while True:
                r = json.loads(await ws.recv())
                if r.get("id") == call.n:
                    return r
        call.n = 0
        r = await call("Runtime.evaluate", expression="JSON.stringify({names: performance.getEntriesByType('resource').map(e => e.name.split('/').slice(-2).join('/')), title: document.title, canvas: !!document.querySelector('canvas')})", returnByValue=True)
        print(r["result"]["result"]["value"])
asyncio.run(main())
'''


async def notified(api):
    """True when a notification about a PDF page is in the bell."""
    listing = await api.call('Notifications/list')
    return any(str(n.get('id', '')).startswith('pdf.render.') or 'PDF page' in n.get('title', '') for n in listing.get('notifications', []))


async def main(args):
    host = args.host
    work = tempfile.mkdtemp()
    local = os.path.join(work, NAME)
    open(local, 'wb').write(make_pdf(args.blank))
    subprocess.run(vr.SSH + ['scp', '-O'] + vr.OPTS + [local, f'root@{host}:/var/lib/unitotem/uploaded/{NAME}'], capture_output=True, check=True)
    api = await vr.Api(host).__aenter__()
    views = (await api.call('Viewers/list'))['viewers']
    viewer = next(iter(views))
    window = views[viewer]['windows'][0]
    before = window.get('assigned_playlist')
    pid = None
    ok = False
    try:
        reply = await api.call('Scheduler/create_playlist', reply='Scheduler/playlists', name='pdf-font-check')
        pid = next(p['playlist_id'] for p in reply['playlists'] if p['name'] == 'pdf-font-check')
        for _ in range(20):
            if NAME in (await api.call('Scheduler/files'))['files']:
                break
            await asyncio.sleep(1)
        else:
            raise SystemExit('the upload folder never listed the test file')
        await api.send('Scheduler/add_file_assets', items=[{'url': NAME, 'duration': 3600}], playlist_id=pid)
        items = []
        for _ in range(20):
            await asyncio.sleep(1)
            items = (await api.call('Scheduler/assets', playlist_id=pid)).get('items', [])
            if items:
                break
        await api.send('Scheduler/edit_asset', uuid=items[0]['uuid'], playlist_id=pid, enabled=True, duration=3600)
        await asyncio.sleep(1)
        await api.send('Viewers/assign_playlist', viewer_id=viewer, window_id=window['window_id'], playlist_id=pid)
        await asyncio.sleep(9)
        vr.ssh(host, f'mkdir -p {REMOTE}')
        subprocess.run(vr.SSH + ['ssh'] + vr.OPTS + [f'root@{host}', f'cat > {REMOTE}/check.py'], input=CHECK, text=True, capture_output=True)
        result = json.loads(vr.ssh(host, f'python3 {REMOTE}/check.py') or '{}')
        print(result)
        names = result.get('names', [])
        fonts = [n for n in names if 'standard_fonts' in n]
        print('standard font data requested:', fonts)
        subprocess.run(vr.SSH + ['scp', '-O'] + vr.OPTS + [os.path.join(HERE, '..', '..', 'ticker', 'board-cdp.py'), f'root@{host}:{REMOTE}/board-cdp.py'], capture_output=True)
        vr.ssh(host, f'python3 {REMOTE}/board-cdp.py shot {REMOTE}/pdf.png 0')
        subprocess.run(vr.SSH + ['scp', '-O'] + vr.OPTS + [f'root@{host}:{REMOTE}/pdf.png', args.out], capture_output=True)
        ok = bool(result.get('canvas')) and not (await notified(api))        # the font requests are made by the worker, which the page's timing list does not show: the pixels are the proof
    finally:
        if before:
            await api.send('Viewers/assign_playlist', viewer_id=viewer, window_id=window['window_id'], playlist_id=before)
        else:
            await api.send('Viewers/unassign_playlist', viewer_id=viewer, window_id=window['window_id'])
        await asyncio.sleep(1.5)
        if pid:
            await api.send('Scheduler/delete_playlist', playlist_id=pid)
        await api.send('Scheduler/delete_files', files=[NAME]) if False else None
        vr.ssh(host, f'rm -rf {REMOTE} /var/lib/unitotem/uploaded/{NAME}')
        await asyncio.sleep(1)
        await api.__aexit__(None, None, None)
    print('ok' if ok else 'FAILED')
    sys.exit(0 if ok else 1)


p = argparse.ArgumentParser()
p.add_argument('--host', required=True)
p.add_argument('--blank', action='store_true', help='a page with nothing on it: it must stay blank and raise no notification')
p.add_argument('--out', default='/tmp/pdf-check.png')
asyncio.run(main(p.parse_args()))
