#!/usr/bin/env python3
"""
The terminal window must only ever close because the user ended their session or closed it: login giving up (it times
out after 60 s at the prompt, and stops after three wrong passwords) must leave the terminal open at a fresh prompt.

    tools/terminal-login-keepalive.py --host 192.168.122.50 [--skip-timeout]
"""
import argparse
import asyncio
import http.client
import ssl
import sys

import websockets


async def read_until(ws, needle, seconds):
    buf = b''
    end = asyncio.get_running_loop().time() + seconds
    while asyncio.get_running_loop().time() < end:
        try:
            buf += await asyncio.wait_for(ws.recv(), 1)
        except asyncio.TimeoutError:
            pass
        if needle in buf:
            return True, buf
    return False, buf


async def main(args):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    conn = http.client.HTTPSConnection(args.host, context=ctx)
    conn.request('POST', '/auth/token', 'username=admin&password=admin', {'Content-Type': 'application/x-www-form-urlencoded'})
    cookie = conn.getresponse().getheader('set-cookie').split(';')[0]
    failures = []

    def check(name, ok):
        print(('PASS ' if ok else 'FAIL ') + name)
        if not ok:
            failures.append(name)

    async with websockets.connect(f'wss://{args.host}/terminal', ssl=ctx, additional_headers={'Cookie': cookie}) as ws:
        ok, _ = await read_until(ws, b'login:', 8)
        check('login prompt appears', ok)
        for attempt in range(1, 5):
            await ws.send(b'nobody\r')
            await read_until(ws, b'assword', 8)
            await ws.send(b'wrong\r')
            ok, _ = await read_until(ws, b'login:', 15)
            check(f'wrong password #{attempt}: back at a login prompt, window still open', ok)
        if not args.skip_timeout:
            ok, buf = await read_until(ws, b'timed out', 75)
            check('login times out after 60 s ...', ok)
            after = buf[buf.index(b'timed out'):]
            ok = b'login:' in after
            if not ok:
                ok, _ = await read_until(ws, b'login:', 10)
            check('... and the terminal is back at a fresh prompt, still connected', ok)
            await ws.send(b'\r')
            check('the connection is still usable', ws.state.name == 'OPEN')
    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', required=True)
    ap.add_argument('--skip-timeout', action='store_true')
    asyncio.run(main(ap.parse_args()))
