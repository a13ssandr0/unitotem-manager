from json import dumps

from fastapi import WebSocket
from PIL import Image
from utils.audio import *
from utils.configuration import *
from utils.cpu import *
from utils.lsblk import *
from utils.login import *
from utils.network import *
from utils.system import *
from utils.objs import *
from utils.files import *


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





def get_dominant_color(pil_img, palette_size=16): # https://stackoverflow.com/a/61730849/9655651
    # Resize image to speed up processing
    img = pil_img.copy()
    img.thumbnail((100, 100))
    # Reduce colors (uses k-means internally)
    paletted = img.convert('P', palette=Image.ADAPTIVE, colors=palette_size)
    # Find the color that occurs most often
    palette = paletted.getpalette()
    color_counts = sorted(paletted.getcolors(), reverse=True)
    palette_index = color_counts[0][1]
    dominant_color = palette[palette_index*3:palette_index*3+3]
    return hex((dominant_color[0]<<16) + (dominant_color[1]<<8) + dominant_color[2])


# Taken from raspi-config, not needed as of now, maybe in the future...

# def set_config_var(key:str, value:str, filename:str):
#     if key and value and filename:
#         made_change = False
#         out = []
#         with open(filename, 'r') as file:
#             for line in file.readlines():
#                 if match(r'^#?\s*'+key+r'=.*$', line.strip()):
#                     line=key+"="+value
#                     made_change=True
#                 out.append(line)
        
#         if not made_change:
#             out.append(line)

#         with open(filename, 'w') as file:
#             file.writelines(out)


# def get_config_var(key:str, filename:str):
#     if key and filename:
#         with open(filename, 'r') as file:
#             for line in file.readlines():
#                 out = match(r'^\s*'+key+r'=(.*)$', line.strip())
#                 if out:
#                     return out.group()







