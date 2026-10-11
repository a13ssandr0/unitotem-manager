#!/usr/bin/env python3
"""
Test of the web terminal: in headless Chrome open it from the navbar, check that `login` asks for an account, log in
with an operating-system account, run a command, close the window; and over a plain websocket check that nobody
without a session, and no web user without the admin capability, gets a terminal.

    tools/ui-terminal.py --host 192.168.122.50 --os-user root --os-password unitotem
"""
import argparse
import asyncio
import importlib.util
import json
import pathlib
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.request

import websockets

spec = importlib.util.spec_from_file_location('ui_smoke', pathlib.Path(__file__).with_name('ui-smoke.py'))
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)

PROBE = pathlib.Path(__file__).with_name('ws-probe.py')


async def refused(host, cookie, label, check):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    headers = {'Cookie': cookie} if cookie else {}
    try:
        async with websockets.connect(f'wss://{host}/terminal', ssl=ctx, additional_headers=headers) as ws:
            try:
                await asyncio.wait_for(ws.recv(), 3)
                check(label, False)
            except websockets.ConnectionClosed as exc:
                check(label, exc.rcvd is not None and exc.rcvd.code in (1008, 1013))
            except asyncio.TimeoutError:
                check(label, False)
    except Exception as exc:  # the server may refuse the handshake itself
        check(label, True)


async def main(args):
    failures = []

    def check(name, ok):
        print(('PASS ' if ok else 'FAIL ') + name)
        if not ok:
            failures.append(name)

    chrome = subprocess.Popen(['google-chrome', '--headless=new', '--remote-debugging-port=9336', '--no-sandbox',
                               '--ignore-certificate-errors', f'--user-data-dir={tempfile.mkdtemp()}',
                               '--window-size=1500,1000', 'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                page = next(t for t in json.load(urllib.request.urlopen('http://127.0.0.1:9336/json/list')) if t['type'] == 'page')
                break
            except Exception:
                time.sleep(0.3)
        async with websockets.connect(page['webSocketDebuggerUrl'], max_size=None) as ws:
            cdp = smoke.Cdp(ws)
            asyncio.get_running_loop().create_task(cdp.pump())
            for d in ('Page', 'Runtime'):
                await cdp.call(f'{d}.enable')
            base = f'https://{args.host}'
            await cdp.call('Page.navigate', url=base + '/login')
            await asyncio.sleep(2)
            await cdp.eval("fetch('/auth/token',{method:'POST',credentials:'include',body:new URLSearchParams("
                           f"{{username:'{args.user}',password:'{args.password}'}})}}).then(r=>r.status)", True)
            await cdp.call('Page.navigate', url=base + '/')
            await asyncio.sleep(4)

            async def screen():
                return await cdp.eval("[...document.querySelectorAll('.xterm-rows > div')].map(r => r.innerText).join('\\n')") or ''

            async def type_line(text):
                await cdp.eval("document.querySelector('.xterm-helper-textarea').focus()")
                await cdp.call('Input.insertText', text=text)
                for t in ('keyDown', 'keyUp'):
                    await cdp.call('Input.dispatchKeyEvent', type=t, key='Enter', code='Enter', windowsVirtualKeyCode=13,
                                   text='\r' if t == 'keyDown' else '')

            async def wait_for(fragment, seconds=10):
                for _ in range(int(seconds * 4)):
                    if fragment in await screen():
                        return True
                    await asyncio.sleep(0.25)
                return False

            check('the Terminal button is there for an admin',
                  await cdp.eval("!!document.querySelector('button[title=Terminal]')"))
            await cdp.eval("document.querySelector('button[title=Terminal]').click()")
            check('login asks for an account', await wait_for('login:'))
            await type_line(args.os_user)
            check('then for a password', await wait_for('assword'))
            await type_line(args.os_password)
            await type_line('echo TERMINAL-$((6*7))-OK; id -un')
            check('a command runs in the session', await wait_for('TERMINAL-42-OK'))
            check('as the operating-system user that logged in', await wait_for(args.os_user))
            await cdp.eval("document.querySelector('.floating-window button[title=Close]').click()")
            await asyncio.sleep(1)
            check('closing the window removes it', await cdp.eval("document.querySelectorAll('.floating-window').length") == 0)

        await refused(args.host, None, 'a connection without a session is refused', check)
        # a web user without the admin capability
        probe = [sys.executable, str(PROBE), '--host', args.host, '--timeout', '4']
        subprocess.run(probe + ['--send', 'Settings/Security/add_user', '--args',
                                json.dumps({'username': 'term-test', 'password': 'term-test-1'})], capture_output=True)
        subprocess.run(probe + ['--send', 'Settings/Security/set_user_permissions', '--args',
                                json.dumps({'username': 'term-test', 'perms': ['scheduler']})], capture_output=True)
        import http.client
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        conn = http.client.HTTPSConnection(args.host, context=ctx)
        conn.request('POST', '/auth/token', 'username=term-test&password=term-test-1',
                     {'Content-Type': 'application/x-www-form-urlencoded'})
        cookie = (conn.getresponse().getheader('set-cookie') or '').split(';')[0]
        await refused(args.host, cookie, 'a web user without the admin capability is refused', check)
        subprocess.run(probe + ['--send', 'Settings/Security/delete_user', '--args', json.dumps({'username': 'term-test'})],
                       capture_output=True)
    finally:
        chrome.terminate()
    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', required=True)
    ap.add_argument('--user', default='admin')
    ap.add_argument('--password', default='admin')
    ap.add_argument('--os-user', required=True)
    ap.add_argument('--os-password', required=True)
    asyncio.run(main(ap.parse_args()))
