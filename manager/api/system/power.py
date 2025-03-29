from subprocess import run as cmd_run

from loguru import logger

from utils.models.user import UserPerms
from api.ws.responses import WSBroadcast, WSMulticast, WSResponse
from api.ws.wsmanager import WSAPIBase, Context


class Power(WSAPIBase):
    @staticmethod
    @UserPerms.requires.none
    def test_method(ctx: Context, txt='test'):
        """
        Debug method
        """
        logger.debug(txt)
        yield WSResponse(message='Response message test', extra=txt)
        yield WSMulticast(users=ctx.username, message='Multicast message test', extra=txt)
        yield WSBroadcast(message='Broadcast message test', extra=txt)
        yield {'message': 'Plain dict test', 'extra': txt}

    @staticmethod
    @UserPerms.requires.power
    def reboot():
        cmd_run(['/usr/bin/systemctl', 'reboot', '-i'])

    @staticmethod
    @UserPerms.requires.power
    def poweroff():
        cmd_run(['/usr/bin/systemctl', 'poweroff', '-i'])
