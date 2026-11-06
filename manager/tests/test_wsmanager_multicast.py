"""WSManager: a message field may carry the same name as a leading parameter (Security/users sends a `users` field)."""
import asyncio
import json

from api.ws.wsmanager import WSManager


class FakeSocket:
    def __init__(self):
        self.sent = []

    async def send_text(self, text):
        self.sent.append(json.loads(text))


def run(coro):
    return asyncio.run(coro)


def manager_with(user, socket):
    ws = WSManager()
    ws.active_connections.append(socket)
    ws.active_users[user].append(socket)
    return ws


def test_multicast_with_a_users_field_reaches_the_recipient():
    admin = FakeSocket()
    ws = manager_with('admin', admin)
    run(ws.multicast(['admin'], 'Settings/Security/users', users={'admin': {'perms': ['admin']}}))
    assert admin.sent == [{'target': 'Settings/Security/users', 'users': {'admin': {'perms': ['admin']}}}]


def test_multicast_only_reaches_the_listed_users():
    admin, other = FakeSocket(), FakeSocket()
    ws = manager_with('admin', admin)
    ws.active_users['operator'].append(other)
    run(ws.multicast('admin', 'Settings/Security/users', users={}))
    assert len(admin.sent) == 1 and other.sent == []


def test_send_and_broadcast_accept_fields_named_like_their_parameters():
    socket = FakeSocket()
    ws = manager_with('admin', socket)
    run(ws.send(socket, 'x', websocket='w'))
    run(ws.broadcast('y', nocache2=1))
    assert [m['target'] for m in socket.sent] == ['x', 'y']
