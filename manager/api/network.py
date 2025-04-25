from socket import gethostname
from typing import Optional

import rpyc
from loguru import logger
from rpyc.utils.factory import unix_connect
from werkzeug.utils import secure_filename

from api.models import Config
from api.ws.responses import WSBroadcast, WSResponse
from api.ws.wsmanager import WSAPIBase
from utils.models.user import UserPerms
from utils.system.network.hotspot import is_hotspot_enabled, stop_hotspot
from utils.system.network.ip import do_ip_addr
from utils.system.network.wifi import get_access_points, scan_access_points


# from https://github.com/RedHatInsights/insights-core
# Licensed under Apache License 2.0


class Settings(WSAPIBase):
    @UserPerms.requires.none
    def hostname(self):
        return WSBroadcast(hostname=gethostname())

    def setHostname(self, hostname: str):
        try:
            with unix_connect(self.Netplan._nm_sock, service=RemoteLogger) as conn:
                if conn.root.set_hostname(hostname):
                    return self.hostname()
                else:
                    return WSResponse(error='Invalid hostname')
        except FileNotFoundError:
            logger.error('Network backend service not running')
            return WSResponse(error='Network backend service not running')

    @staticmethod
    async def get_wifis():
        await scan_access_points()
        return WSBroadcast(wifis=await get_access_points())

    class Netplan(WSAPIBase):
        _nm_sock = '/run/unitotem/nm.sock'

        def newFile(self, filename: str):
            try:
                with unix_connect(self._nm_sock, service=RemoteLogger) as conn:
                    conn.root.create_netplan(filename)
                return self.getFile()
            except FileNotFoundError:
                logger.error('Network backend service not running')
                return WSResponse(error='Network backend service not running')

        def getFile(self, filename: Optional[str] = None):
            try:
                with unix_connect(self._nm_sock, service=RemoteLogger) as conn:
                    netplan_files = conn.root.get_netplan_file_list()
                    if filename is not None and filename in netplan_files:
                        return WSBroadcast(files={str(filename): conn.root.get_netplan_file(filename)})
                    return WSBroadcast(files={str(f): conn.root.get_netplan_file(f) for f in netplan_files})
            except FileNotFoundError:
                logger.error('Network backend service not running')
                return WSResponse(error='Network backend service not running')

        async def changeFile(self, filename: Optional[str] = None, content: str = '', apply: bool = True):
            try:
                with unix_connect(self._nm_sock, service=RemoteLogger) as conn:
                    if filename is not None:
                        res = conn.root.set_netplan(secure_filename(filename), content, apply)
                    else:
                        res = conn.root.generate_netplan(apply)
                    # noinspection PySimplifyBooleanCheck
                    if res is True:
                        if await is_hotspot_enabled() and do_ip_addr(True):
                            # AP is still enabled, but now we are connected, AP is no longer needed
                            await stop_hotspot()
                            # TODO: check if showing first boot with QR
                            Config.assets.next_a()
                    elif isinstance(res, str):
                        yield WSResponse(error='Netplan error', extra=res)
                yield self.getFile()
                if filename is not None:
                    yield WSResponse('Settings/Netplan/showFile', filename=filename)
            except FileNotFoundError:
                logger.error('Network backend service not running')
                yield WSResponse(error='Network backend service not running')

        def deleteFile(self, filename: Optional[str] = None, apply: bool = True):
            try:
                with unix_connect(self._nm_sock, service=RemoteLogger) as conn:
                    res = conn.root.del_netplan_file(filename, apply)
                    if isinstance(res, str):
                        yield WSResponse(error='Netplan error', extra=res)
                yield self.getFile()
            except FileNotFoundError:
                logger.error('Network backend service not running')
                yield WSResponse(error='Network backend service not running')


class RemoteLogger(rpyc.Service):
    _logger = logger.patch(lambda r: r.update(name='unitotem-admind', function='Netplan', line=''))

    exposed_trace = _logger.trace
    exposed_debug = _logger.debug
    exposed_info = _logger.info
    exposed_success = _logger.success
    exposed_warning = _logger.warning
    exposed_error = _logger.error
    exposed_critical = _logger.critical
    exposed_exception = _logger.exception
    exposed_log = _logger.log
