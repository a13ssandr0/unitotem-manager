#!/usr/bin/env python3
"""
Who may open the developer tools of a window, on a real node: an administrator can; an account without the admin capability
gets nothing (no address, no front end files, no protocol), and a token asked for by one session is useless in another, in a
foreign Origin, twice, or for another page. The account made for the check is always removed.

    tools/devtools-access.py --host <host> --user root --password unitotem
"""
import argparse
import asyncio
import json
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import websockets

PROBE = Path(__file__).parent / 'ws-probe.py'
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
GUEST, GUEST_PASSWORD = 'dt-guest', 'dt-guest-pass-1'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def request(host, path, cookie='', data=None):
    req = urllib.request.Request(f'https://{host}{path}', data=data, headers={'Cookie': cookie, 'Content-Type': 'application/json'})
    opener = urllib.request.build_opener(NoRedirect, urllib.request.HTTPSHandler(context=CTX))
    try:
        reply = opener.open(req)
        return reply.status, reply.read(), reply.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


def login(host, user, password):
    req = urllib.request.Request(f'https://{host}/auth/token', data=f'username={user}&password={password}'.encode(),
                                 headers={'Content-Type': 'application/x-www-form-urlencoded'})
    opener = urllib.request.build_opener(NoRedirect, urllib.request.HTTPSHandler(context=CTX))
    try:
        reply = opener.open(req)
    except urllib.error.HTTPError as e:
        reply = e
    return (reply.headers.get('set-cookie') or '').split(';')[0]


async def socket(host, cookie, token_path, origin=None):
    """The protocol answer, or the reason the socket was refused."""
    try:
        async with websockets.connect(f'wss://{host}/devtools/ws/{token_path}', ssl=CTX, origin=origin or f'https://{host}',
                                      additional_headers={'Cookie': cookie}) as ws:
            await ws.send(json.dumps({'id': 1, 'method': 'Runtime.evaluate', 'params': {'expression': '6*7'}}))
            return json.loads(await asyncio.wait_for(ws.recv(), 6))['result']['result']['value']
    except websockets.ConnectionClosed as e:
        return f'closed {e.rcvd.code if e.rcvd else "?"}'
    except Exception as e:                                          # noqa: BLE001 - the reason is the result
        return f'error {type(e).__name__}'


def api(host, admin_user, admin_password, command, **args):
    subprocess.run([sys.executable, str(PROBE), '--host', host, '--user', admin_user, '--password', admin_password, '--timeout', '4',
                    '--send', command, '--args', json.dumps(args)], capture_output=True)


async def main(args):
    failures = []

    def check(label, ok):
        print(('ok      ' if ok else 'FAILED  ') + label)
        if not ok:
            failures.append(label)

    admin = login(args.host, args.user, args.password)
    api(args.host, args.user, args.password, 'Settings/Security/add_user', username=GUEST, password=GUEST_PASSWORD)
    api(args.host, args.user, args.password, 'Settings/Security/set_user_permissions', username=GUEST, perms=['scheduler'])
    try:
        guest = login(args.host, GUEST, GUEST_PASSWORD)
        check('the guest account exists and can log in', bool(guest))
        window = json.dumps({'window_id': 0}).encode()

        status, body, _ = request(args.host, '/devtools/open', admin, window)
        check('an administrator gets the address', status == 200 and b'/devtools/ws/' in body)
        admin_path = json.loads(body)['path'].split('/devtools/ws/')[1] if status == 200 else ''

        status, _, _ = request(args.host, '/devtools/open', guest, window)
        check(f'a user without the admin capability gets no address ({status})', status in (401, 403))
        status, _, _ = request(args.host, '/devtools/inspector.html', guest)
        check(f'... and no front end file ({status})', status in (401, 403))
        status, _, _ = request(args.host, '/devtools/inspector.html', '')
        check(f'nobody logged in gets no front end file ({status})', status in (307, 401, 403))

        check("the guest cannot use the administrator's token", (await socket(args.host, guest, admin_path)).startswith('closed 1008'))
        check('an administrator with the token gets the protocol', await socket(args.host, admin, admin_path) == 42)
        check('the same token twice is refused', (await socket(args.host, admin, admin_path)).startswith('closed 1008'))
        _, body, _ = request(args.host, '/devtools/open', admin, window)
        fresh = json.loads(body)['path'].split('/devtools/ws/')[1]
        check('a foreign Origin is refused', (await socket(args.host, admin, fresh, 'https://evil.example')).startswith('closed 1008'))
        _, body, _ = request(args.host, '/devtools/open', admin, window)
        fresh = json.loads(body)['path'].split('/devtools/ws/')[1]
        other_page = fresh.split('/')[0] + '/' + 'A' * 32
        check('the token of one page does not open another', (await socket(args.host, admin, other_page)).startswith('closed 1008'))
    finally:
        api(args.host, args.user, args.password, 'Settings/Security/delete_user', username=GUEST)
    print('FAILED: ' + '; '.join(failures) if failures else 'all ok')
    return 1 if failures else 0


p = argparse.ArgumentParser()
p.add_argument('--host', required=True)
p.add_argument('--user', default='admin')
p.add_argument('--password', default='admin')
sys.exit(asyncio.run(main(p.parse_args())))
