#!/usr/bin/env python3
"""
Log in to a running manager and watch its WebSocket API from the command line.

The web UI is not the only way to check that the backend reacted to something:
this speaks the same /ws protocol directly, which makes it usable from a test
script and keeps the evidence in text form. Typical uses:

    # send one command and print the reply
    tools/ws-probe.py --send Viewers/list

    # watch what the backend pushes on its own (e.g. while hot-plugging a screen)
    tools/ws-probe.py --watch Viewers/list --timeout 20

Authentication is cookie-based (see routers/login.py: LoginManager is built with
use_cookie=True, use_header=False), so the token obtained from /auth/token has to
travel as a cookie on the WebSocket handshake, not as a header.
"""
import argparse
import asyncio
import json
import ssl
import sys

import httpx
import websockets


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--host', default='192.168.122.50',
                    help='manager host (default: the test VM)')
    ap.add_argument('--user', default='admin')
    ap.add_argument('--password', default='admin')
    ap.add_argument('--send', metavar='TARGET', action='append', default=[],
                    help='command to send, e.g. Viewers/list; repeatable. '
                         'Arguments go in --args as JSON.')
    ap.add_argument('--args', metavar='JSON', action='append', default=[],
                    help='JSON object of arguments for the matching --send')
    ap.add_argument('--watch', metavar='TARGET', action='append', default=[],
                    help='only print frames whose target matches; '
                         'omit to print everything')
    ap.add_argument('--timeout', type=float, default=10.0,
                    help='seconds to keep listening (default: 10)')
    ap.add_argument('--count', type=int, default=0,
                    help='stop after this many matching frames (0 = no limit)')
    opts = ap.parse_args()

    # The manager serves a self-signed certificate by default.
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    async with httpx.AsyncClient(verify=False, follow_redirects=False) as http:
        resp = await http.post(f'https://{opts.host}/auth/token',
                               data={'username': opts.user, 'password': opts.password})
        if resp.status_code not in (200, 303):
            print(f'login failed: HTTP {resp.status_code} {resp.text[:200]}', file=sys.stderr)
            return 1
        cookies = '; '.join(f'{k}={v}' for k, v in resp.cookies.items())
        if not cookies:
            print('login returned no cookie', file=sys.stderr)
            return 1

    matched = 0
    async with websockets.connect(f'wss://{opts.host}/ws', ssl=ssl_ctx,
                                  additional_headers={'Cookie': cookies},
                                  open_timeout=10) as ws:
        for i, target in enumerate(opts.send):
            payload = {'target': target}
            if i < len(opts.args):
                payload.update(json.loads(opts.args[i]))
            await ws.send(json.dumps(payload))

        loop = asyncio.get_running_loop()
        deadline = loop.time() + opts.timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            frame = json.loads(raw)
            if opts.watch and frame.get('target') not in opts.watch:
                continue
            print(json.dumps(frame, indent=2, sort_keys=True), flush=True)
            matched += 1
            if opts.count and matched >= opts.count:
                break

    return 0 if matched or not (opts.send or opts.watch) else 2


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
