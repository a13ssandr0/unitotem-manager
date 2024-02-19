import utils.constants as const
from utils.audio import *
from utils.commons import *
from utils.cpu import *
from utils.lsblk import *
from utils.models import *
from utils.network import *
from utils.objs import *
from utils.security import *
from utils.system import *
from utils.ws.endpoints import DISPLAYS, WINDOW, REMOTE_WS, UI_WS, WS, router as ws_endpoints_router
from utils.ws.wsmanager import WSManager

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
