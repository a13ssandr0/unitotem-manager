__all__ = ['WSManager']



from json import dumps

from fastapi import WebSocket

class WSManager:

    def __init__(self, cache_last=False):
        self.active_connections: list[WebSocket] = []
        self.last = {} if cache_last else None
        # if last is not None we are using command cache.
        # this means every time a client connects will receive
        # the last command sent for each target.
        # this is needed for the viewer program that may connect after a command
        # was sent (i.e. the manager finishes starting before the viewer, or the 
        # viewer for whatever reason restarts)
        #
        # if we need caching, last is initialized to something different from None
        # this way we avoid usind two variables: one for setting and the other
        # for actual caching

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        if self.last != None:
            for cmd in self.last:
                await websocket.send_text(cmd)

    def disconnect(self, websocket: WebSocket):
        try:
            self.active_connections.remove(websocket)
        except ValueError:
            pass #it's not necessary to crash if not present

    async def send(self, websocket: WebSocket, target: str, **kwargs):
        text = dumps({'target': target, **kwargs})
        await websocket.send_text(text)
        if self.last != None:
            self.last[target] = text

    async def broadcast(self, target: str, **kwargs):
        text = dumps({'target': target, **kwargs})
        for connection in self.active_connections:
            await connection.send_text(text)
        if self.last != None:
            self.last[target] = text