from platform import node as get_hostname
from typing import Optional

from werkzeug.utils import secure_filename

import utils.system.network.hotspot
from models import Config, UserPerms
from utils.system.network.hotspot import stop_hostpot
from utils.system.network.ip import do_ip_addr
from utils.system.network.misc import set_hostname
from utils.system.network.netplan import set_netplan, generate_netplan, create_netplan, del_netplan_file, \
    get_netplan_file, get_netplan_file_list
from utils.system.network.wifi import get_wifis
from ws.responses import WSBroadcast, WSResponse
from ws.wsmanager import WSAPIBase


# from https://github.com/RedHatInsights/insights-core
# Licensed under Apache License 2.0


class Settings(WSAPIBase):
    @UserPerms.requires.none
    async def hostname(self, hostname: Optional[str] = None):
        if hostname is not None:
            set_hostname(hostname)
        return WSBroadcast(hostname=get_hostname())

    async def get_wifis(self):
        return WSBroadcast(wifis=get_wifis())

    class Netplan(WSAPIBase):
        async def newFile(self, filename: str):
            create_netplan(filename)
            return self.getFile()

        async def getFile(self, filename: Optional[str] = None):
            netplan_files = get_netplan_file_list()
            if filename is not None and filename in netplan_files:
                return WSBroadcast(files={filename: get_netplan_file(filename)})
            return WSBroadcast(files={f: get_netplan_file(f) for f in netplan_files})

        async def changeFile(self, filename: Optional[str] = None, content: str = '', apply: bool = True):
            if filename is not None:
                res = set_netplan(secure_filename(filename), content, apply)
            else:
                res = generate_netplan(apply)
            # noinspection PySimplifyBooleanCheck
            if res is True:
                if utils.system.network.hotspot.DEFAULT_AP and do_ip_addr(
                        True):  # AP is still enabled, but now we are connected, AP is no longer needed
                    stop_hostpot()
                    utils.system.network.hotspot.DEFAULT_AP = None
                    Config.assets.next_a()
            elif isinstance(res, str):
                return WSResponse(error='Netplan error', extra=res)
            return self.getFile()

        async def deleteFile(self, filename: Optional[str] = None, apply: bool = True):
            res = del_netplan_file(filename, apply)
            if isinstance(res, str):
                return WSResponse(error='Netplan error', extra=res)
            return self.getFile()
