"""Date and time of the node: time zone and network time synchronisation, through systemd-timedated."""
from datetime import datetime

from loguru import logger

from api.ws.responses import WSBroadcast, WSResponse
from api.ws.wsmanager import WSAPIBase


async def status_fields() -> dict:
    """The whole state of the page: what timedated reports, the zones to choose from, and the local clock."""
    from unitotem_node.system import dbus_system
    now = datetime.now().astimezone()
    return {
        **(await dbus_system.timedate_status()),
        'timezones': await dbus_system.list_timezones(),
        'local_time': now.isoformat(timespec='seconds'),
        'utc_offset': int(now.utcoffset().total_seconds()),
    }


class DateTime(WSAPIBase):
    """Time zone and network time (NTP). Every change answers with the full status, which is also pushed to all pages."""

    async def status(self):
        try:
            return WSBroadcast(DateTime.status, **await status_fields())
        except Exception as e:
            logger.error('DateTime.status failed: {}', e)
            return WSResponse(error=str(e))

    async def set_timezone(self, timezone: str):
        from unitotem_node.system import dbus_system
        try:
            await dbus_system.set_timezone(timezone)
        except Exception as e:
            logger.error('DateTime.setTimezone failed: {}', e)
            return WSResponse(error=str(e))
        return await self.status()

    async def set_ntp(self, enabled: bool):
        from unitotem_node.system import dbus_system
        try:
            await dbus_system.set_ntp(enabled)
        except Exception as e:
            logger.error('DateTime.setNtp failed: {}', e)
            return WSResponse(error=str(e))
        return await self.status()
