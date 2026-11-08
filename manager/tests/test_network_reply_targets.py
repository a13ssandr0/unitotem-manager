"""The replies of edit_connection and delete_connection name the function they answer: the page listens to those targets."""
import asyncio

from api.ws.responses import WSBroadcast, WSResponse
from unitotem_node.api.network import Network
from unitotem_node.system.network import networkmanager as nm


def name_the_api(monkeypatch):
    # the registry sets api_path on every endpoint when the manager starts (api.ws.endpoints)
    for name in ('edit_connection', 'delete_connection', 'connections'):
        monkeypatch.setattr(getattr(Network, name), 'api_path', f'Settings/Network/{name}', raising=False)


def test_edit_and_delete_replies_carry_their_own_targets(monkeypatch):
    name_the_api(monkeypatch)
    listed = [{'path': '/conn/1', 'id': 'home'}]

    async def fake_edit(**kwargs):
        pass

    async def fake_delete(conn_path):
        pass

    async def fake_list():
        return listed

    monkeypatch.setattr(nm, 'edit_connection', fake_edit)
    monkeypatch.setattr(nm, 'delete_connection', fake_delete)
    monkeypatch.setattr(nm, 'get_all_connections', fake_list)

    edited = asyncio.run(Network.edit_connection('/conn/1', ip_method='auto'))
    deleted = asyncio.run(Network.delete_connection('/conn/1'))
    assert isinstance(edited, WSBroadcast) and edited.target == 'Settings/Network/edit_connection'
    assert edited.kwargs == {'connections': listed}
    assert isinstance(deleted, WSBroadcast) and deleted.target == 'Settings/Network/delete_connection'
    assert deleted.kwargs == {'connections': listed}


def test_edit_and_delete_errors_carry_their_own_targets(monkeypatch):
    name_the_api(monkeypatch)

    async def boom(*args, **kwargs):
        raise RuntimeError('bus down')

    monkeypatch.setattr(nm, 'edit_connection', boom)
    monkeypatch.setattr(nm, 'delete_connection', boom)
    edited = asyncio.run(Network.edit_connection('/conn/1', ip_method='auto'))
    deleted = asyncio.run(Network.delete_connection('/conn/1'))
    assert isinstance(edited, WSResponse) and edited.target == 'Settings/Network/edit_connection'
    assert edited.kwargs == {'error': 'bus down'}
    assert isinstance(deleted, WSResponse) and deleted.target == 'Settings/Network/delete_connection'
    assert deleted.kwargs == {'error': 'bus down'}
