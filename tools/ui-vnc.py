#!/usr/bin/env python3
"""
End-to-end test of the screen-in-the-browser feature against a running node (the test VM).

Over a plain websocket, speaking RFB by hand: the stream starts with a real RFB banner, a frame of the real screen
arrives (a red window is drawn on the node and found in the frame), view-only ignores a pointer event and control
accepts it, no TCP port is ever opened, the server and its directory are gone after the websocket closes, after a
drop and after the idle timeout, and nobody but an administrator gets in. Then in headless Chrome: the button, the
floating window, noVNC connected, the window staying open (and saying why) when the session ends, the Close button.

    tools/ui-vnc.py --host 192.168.122.50

The node needs the VNC server on its PATH and UNITOTEM_VNC_IDLE_TIMEOUT=20 in the manager's environment (see the
runner script in the report: the kiosk image has no tigervnc package and a read-only /usr).
"""
import argparse
import asyncio
import http.client
import json
import pathlib
import ssl
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
import importlib.util

import websockets

HERE = pathlib.Path(__file__).parent
spec = importlib.util.spec_from_file_location('ui_smoke', HERE / 'ui-smoke.py')
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)
PROBE = HERE / 'ws-probe.py'
VM_SSH = str(HERE / 'vm-ssh')

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

DRAW = '''
import sys, time
from Xlib import X, display
d = display.Display()
s = d.screen()
w = s.root.create_window(500, 400, 200, 100, 0, s.root_depth, X.InputOutput, X.CopyFromParent,
                         background_pixel=0xFF0000, override_redirect=True)
w.map()
d.flush()
time.sleep(600)
'''
POINTER = '''
from Xlib import display
p = display.Display().screen().root.query_pointer()
print(p.root_x, p.root_y)
'''
VM_ENV = 'DISPLAY=:0 XAUTHORITY=/run/unitotem-x11.auth '


def vm(command: str) -> str:
    return subprocess.run([VM_SSH, command], capture_output=True, text=True).stdout.strip()


def vnc_state() -> dict:
    """Facts about the VNC server on the node: its processes, listening TCP sockets of them, and session directories."""
    out = vm("pids=$(pgrep -x 'x0vncserver|X0tigervnc'); echo PIDS $pids; for p in $pids; do tr '\\0' ' ' < /proc/$p/cmdline; echo; done;"
             " echo TCP; ss -tlnp | grep -i -E 'X0tigervnc|:5900' ; echo DIRS; ls /run/unitotem-vnc 2>/dev/null")
    pids = out.split('PIDS', 1)[1].split('\n', 1)[0].split()
    tcp = out.split('TCP\n', 1)[1].split('DIRS')[0].strip()
    dirs = out.split('DIRS', 1)[1].split()
    return {'pids': pids, 'tcp': tcp, 'dirs': dirs, 'raw': out}


class Rfb:
    """Just enough of RFB over a websocket: handshake, Raw framebuffer requests and pointer events."""

    def __init__(self, ws):
        self.ws, self.buf = ws, b''

    async def read(self, n):
        while len(self.buf) < n:
            msg = await asyncio.wait_for(self.ws.recv(), 10)
            self.buf += msg if isinstance(msg, bytes) else msg.encode()
        data, self.buf = self.buf[:n], self.buf[n:]
        return data

    async def handshake(self):
        banner = await self.read(12)
        await self.ws.send(b'RFB 003.008\n')
        n = (await self.read(1))[0]
        types = await self.read(n)
        assert 1 in types, f'no "None" security type: {list(types)}'
        await self.ws.send(bytes([1]))
        assert struct.unpack('>I', await self.read(4))[0] == 0
        await self.ws.send(b'\x01')
        w, h = struct.unpack('>HH', await self.read(4))
        await self.read(16)
        name = await self.read(struct.unpack('>I', await self.read(4))[0])
        # 32 bits per pixel, B in the low byte, and Raw as the only encoding
        await self.ws.send(struct.pack('>B3xBBBBHHHBBB3x', 0, 32, 24, 0, 1, 255, 255, 255, 16, 8, 0))
        await self.ws.send(struct.pack('>BxHi', 2, 1, 0))
        return banner, w, h, name

    async def pixels(self, x, y, w, h):
        await self.ws.send(struct.pack('>BBHHHH', 3, 0, x, y, w, h))
        while True:
            kind = (await self.read(1))[0]
            if kind != 0:
                continue
            await self.read(1)
            (count,) = struct.unpack('>H', await self.read(2))
            out = {}
            for _ in range(count):
                rx, ry, rw, rh, enc = struct.unpack('>HHHHi', await self.read(12))
                assert enc == 0, f'unexpected encoding {enc}'
                data = await self.read(rw * rh * 4)
                for j in range(rh):
                    for i in range(rw):
                        b, g, r, _ = data[(j * rw + i) * 4:(j * rw + i) * 4 + 4]
                        out[(rx + i, ry + j)] = (r, g, b)
            return out

    async def pointer(self, x, y, buttons=0):
        await self.ws.send(struct.pack('>BBHH', 5, buttons, x, y))


def login(host, user, password):
    conn = http.client.HTTPSConnection(host, context=CTX)
    conn.request('POST', '/auth/token', f'username={user}&password={password}',
                 {'Content-Type': 'application/x-www-form-urlencoded'})
    return (conn.getresponse().getheader('set-cookie') or '').split(';')[0]


def connect(host, cookie, query='', origin=None):
    headers = {'Cookie': cookie} if cookie else {}
    if origin:
        headers['Origin'] = origin
    return websockets.connect(f'wss://{host}/vnc{query}', ssl=CTX, additional_headers=headers, max_size=None)


async def refused(host, cookie, query='', origin=None):
    try:
        async with connect(host, cookie, query, origin) as ws:
            await asyncio.wait_for(ws.recv(), 4)
            return False
    except websockets.ConnectionClosed as exc:
        return exc.rcvd is not None and exc.rcvd.code in (1008, 1011, 1013)
    except Exception:
        return True


async def main(args):
    failures = []

    def check(name, ok, extra=''):
        print(('PASS ' if ok else 'FAIL ') + name + (f'   [{extra}]' if extra and not ok else ''))
        if not ok:
            failures.append(name)

    host = args.host
    cookie = login(host, args.user, args.password)
    probe = [sys.executable, str(PROBE), '--host', host, '--timeout', '4']
    drawer = None
    try:
        before = vnc_state()
        check('no VNC server and no session directory when nobody is watching',
              not before['pids'] and not before['dirs'] and not before['tcp'], before['raw'])

        vm('cat > /tmp/vnc-draw.py <<"EOF"\n' + DRAW + 'EOF\ncat > /tmp/vnc-ptr.py <<"EOF"\n' + POINTER + 'EOF')
        drawer = vm(VM_ENV + 'nohup /var/unitotem-venv/bin/python3 /tmp/vnc-draw.py >/dev/null 2>&1 & echo $!')
        time.sleep(1.5)

        # ── view-only session ──
        async with connect(host, cookie) as ws:
            rfb = Rfb(ws)
            banner, w, h, name = await rfb.handshake()
            check('the stream starts with a real RFB banner', banner.startswith(b'RFB 003.'), repr(banner))
            check('the desktop size is the real one', (w, h) == (3840, 2160), f'{w}x{h}')
            frame = await rfb.pixels(560, 430, 20, 20)
            check('a frame of the real screen arrives: the red window drawn on the node is in it',
                  all(p == (255, 0, 0) for p in frame.values()), str(list(frame.values())[:3]))
            during = vnc_state()
            check('exactly one server runs, with no TCP listener, bound to a unix socket',
                  len(during['pids']) == 1 and not during['tcp'] and '-rfbport -1' in during['raw']
                  and '-rfbunixpath /run/unitotem-vnc/' in during['raw'], during['raw'])
            check('it is view-only on the command line',
                  '-AcceptKeyEvents=0' in during['raw'] and '-AcceptPointerEvents=0' in during['raw'])
            perms = vm('stat -c "%a %U" /run/unitotem-vnc /run/unitotem-vnc/*')
            check('its directory and the socket are root-only', all(line.startswith(('700 root', '600 root'))
                                                                    for line in perms.splitlines()), perms)
            start = vm(VM_ENV + '/var/unitotem-venv/bin/python3 /tmp/vnc-ptr.py')
            await rfb.pointer(1234, 1000)
            await asyncio.sleep(1)
            check('view-only really ignores a pointer event', start != '' and vm(VM_ENV + '/var/unitotem-venv/bin/python3 /tmp/vnc-ptr.py') == start,
                  f'{start} -> {vm(VM_ENV + "/var/unitotem-venv/bin/python3 /tmp/vnc-ptr.py")}')
        await asyncio.sleep(2.5)
        after = vnc_state()
        check('closing the websocket stops the server and removes its directory',
              not after['pids'] and not after['dirs'], after['raw'])

        # ── session with control, on one output ──
        async with connect(host, cookie, '?control=1&output=DP-1') as ws:
            rfb = Rfb(ws)
            await rfb.handshake()
            await rfb.pointer(1234, 1000)
            await asyncio.sleep(1)
            moved = vm(VM_ENV + '/var/unitotem-venv/bin/python3 /tmp/vnc-ptr.py')
            check('control accepts the pointer event', moved == '1234 1000', moved)
            during = vnc_state()
            check('the output is restricted by -Geometry and input is switched on',
                  '-Geometry 3840x2160+0+0' in during['raw'] and '-AcceptPointerEvents=1' in during['raw'], during['raw'])
        await asyncio.sleep(2.5)
        check('and that server is gone as well', not vnc_state()['pids'])

        # ── abrupt drop: the TCP connection of the browser disappears, no close frame ──
        ws = await connect(host, cookie)
        rfb = Rfb(ws)
        await rfb.handshake()
        check('a server is running during the session', len(vnc_state()['pids']) == 1)
        ws.transport.abort()
        await asyncio.sleep(3)
        check('a dropped connection stops the server', not vnc_state()['pids'] and not vnc_state()['dirs'])

        # ── idle ──
        async with connect(host, cookie) as ws:
            rfb = Rfb(ws)
            await rfb.handshake()
            reason = None
            try:
                while True:
                    await asyncio.wait_for(ws.recv(), 60)
            except websockets.ConnectionClosed as exc:
                reason = exc.rcvd.reason if exc.rcvd else None
            check('an abandoned session is closed by the server as Idle', reason == 'Idle', str(reason))
        await asyncio.sleep(2)
        check('and its server stopped', not vnc_state()['pids'] and not vnc_state()['dirs'])

        # ── refusals ──
        check('an unknown output is refused', await refused(host, cookie, '?output=NOPE-9'))
        check('no VNC server was started for that', not vnc_state()['pids'])
        check('a connection without a session is refused', await refused(host, None))
        check('a page of another origin is refused even with an admin cookie',
              await refused(host, cookie, origin='https://evil.example'))
        subprocess.run(probe + ['--send', 'Settings/Security/add_user', '--args',
                                json.dumps({'username': 'vnc-test', 'password': 'vnc-test-1'})], capture_output=True)
        subprocess.run(probe + ['--send', 'Settings/Security/set_user_permissions', '--args',
                                json.dumps({'username': 'vnc-test', 'perms': ['scheduler', 'audio', 'power']})],
                       capture_output=True)
        low = login(host, 'vnc-test', 'vnc-test-1')
        check('a user with every capability except admin is refused', await refused(host, low))
        out = subprocess.run(probe + ['--send', 'Vnc/outputs'], capture_output=True, text=True).stdout
        check('the outputs call answers the admin with the outputs', '"DP-1"' in out and '"available": true' in out.replace(
            ':true', ': true'), out[:200])
        check('no VNC server was started for any refusal', not vnc_state()['pids'] and not vnc_state()['dirs'])
        subprocess.run(probe + ['--send', 'Settings/Security/delete_user', '--args', json.dumps({'username': 'vnc-test'})],
                       capture_output=True)

        # ── the browser ──
        await browser(host, args, check, vnc_state)
    finally:
        if drawer:
            vm(f'kill {drawer}')
        vm('rm -f /tmp/vnc-draw.py /tmp/vnc-ptr.py')
    sys.exit(1 if failures else 0)


async def browser(host, args, check, vnc_state):
    port = 9338
    chrome = subprocess.Popen(['google-chrome', '--headless=new', f'--remote-debugging-port={port}', '--no-sandbox',
                               '--ignore-certificate-errors', f'--user-data-dir={tempfile.mkdtemp()}',
                               '--window-size=1500,1000', 'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                page = next(t for t in json.load(urllib.request.urlopen(f'http://127.0.0.1:{port}/json/list')) if t['type'] == 'page')
                break
            except Exception:
                time.sleep(0.3)
        async with websockets.connect(page['webSocketDebuggerUrl'], max_size=None) as ws:
            cdp = smoke.Cdp(ws)
            asyncio.get_running_loop().create_task(cdp.pump())
            for d in ('Page', 'Runtime'):
                await cdp.call(f'{d}.enable')
            base = f'https://{host}'
            await cdp.call('Page.navigate', url=base + '/login')
            await asyncio.sleep(2)
            await cdp.eval("fetch('/auth/token',{method:'POST',credentials:'include',body:new URLSearchParams("
                           f"{{username:'{args.user}',password:'{args.password}'}})}}).then(r=>r.status)", True)
            await cdp.call('Page.navigate', url=base + '/')
            await asyncio.sleep(4)
            check('the "Watch the screen" button is in the top bar for an admin',
                  await cdp.eval("!!document.querySelector('button[title=\"Watch the screen\"]')"))
            await cdp.eval("document.querySelector('button[title=\"Watch the screen\"]').click()")
            status = ''
            for _ in range(40):
                status = await cdp.eval("document.querySelector('.vnc-status')?.innerText") or ''
                if status == 'View only':
                    break
                await asyncio.sleep(0.5)
            check('noVNC connects and the window says View only', status == 'View only', status)
            canvas = await cdp.eval("(() => { const c = document.querySelector('.vnc-host canvas');"
                                    " return c ? [c.width, c.height] : null })()")
            check('a canvas with the real screen size is there', canvas == [3840, 2160], str(canvas))
            inside = await cdp.eval("(() => { const r = document.querySelector('.floating-window').getBoundingClientRect();"
                                    " return r.left >= 0 && r.top >= 0 && r.right <= innerWidth && r.bottom <= innerHeight })()")
            check('the window is inside the page', inside)
            title = await cdp.eval("document.querySelector('.floating-title-text').innerText")
            check('its title says what it shows', title == 'Screen: whole desktop', title)
            check('a server runs while the window is open', len(vnc_state()['pids']) == 1)
            await cdp.eval("document.querySelector('.floating-window .v-switch input').click()")
            for _ in range(40):
                status = await cdp.eval("document.querySelector('.vnc-status')?.innerText") or ''
                if status == 'Controlling':
                    break
                await asyncio.sleep(0.5)
            check('switching Control on starts a new session that is controlling', status == 'Controlling', status)
            check('with exactly one server and input accepted',
                  len(vnc_state()['pids']) == 1 and '-AcceptPointerEvents=1' in vnc_state()['raw'])
            await cdp.eval("document.querySelector('.floating-window button[title=Close]').click()")
            await asyncio.sleep(3)
            check('closing the window removes it and stops the server',
                  await cdp.eval("document.querySelectorAll('.floating-window').length") == 0
                  and not vnc_state()['pids'] and not vnc_state()['dirs'])
            await cdp.eval("document.querySelector('button[title=\"Watch the screen\"]').click()")
            await asyncio.sleep(3)
            check('reopened, it is view-only again, whatever the previous session was',
                  await cdp.eval("document.querySelector('.vnc-status')?.innerText") == 'View only')
            # the session ends while the window is open: the window stays and explains
            vm("kill $(pgrep -x 'x0vncserver|X0tigervnc')")
            ended = ''
            for _ in range(20):
                ended = await cdp.eval("document.querySelector('.vnc-overlay')?.innerText") or ''
                if ended:
                    break
                await asyncio.sleep(0.5)
            check('when the server dies the window stays open and says why',
                  bool(ended) and await cdp.eval("document.querySelectorAll('.floating-window').length") == 1, ended)
            await cdp.eval("document.querySelector('.floating-window button[title=Reconnect]').click()")
            await asyncio.sleep(3)
            check('and the reload button starts a new session',
                  await cdp.eval("document.querySelector('.vnc-status')?.innerText") == 'View only')
            await cdp.eval("document.querySelector('.floating-window button[title=Close]').click()")
            await asyncio.sleep(3)
            check('which stops with the window again', not vnc_state()['pids'])
    finally:
        chrome.terminate()


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', required=True)
    ap.add_argument('--user', default='admin')
    ap.add_argument('--password', default='admin')
    asyncio.run(main(ap.parse_args()))
