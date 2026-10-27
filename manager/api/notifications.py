from typing import List

from loguru import logger

from api.ws.responses import WSResponse
from api.ws.wsmanager import Context, WSAPIBase
from utils.models.user import UserPerms, user_manager
from utils.notifications import notifications

MAX_IDS = 200       # more than the list can hold: a longer request is a mistake or an attack, either way cut


def _perms(ctx: Context) -> set:
    user = user_manager.get_user(ctx.username)
    return set(user.perms) if user else set()


async def push_all():
    """Tell every open page what its user may see now (the list differs per user, so this is one message each)."""
    from api.ws.endpoints import WS
    for name, sockets in list(WS.active_users.items()):
        user = user_manager.get_user(name)
        if not sockets or user is None:
            continue
        try:
            await WS.multicast(name, 'Notifications/list', notifications=notifications.snapshot(user.perms))
        except Exception as exc:        # a socket that just closed must not keep the others from their update
            logger.debug('Cannot push the notifications to {}: {}', name, exc)


class Notifications(WSAPIBase):
    """What the node wants its users to know. Every function is open to any logged in user, and what they get (or
    can change) is only what their capabilities allow them to see."""

    @staticmethod
    @UserPerms.requires.none
    def list(ctx: Context):
        """The notifications this user may see, newest first (pushed again to every page whenever they change)."""
        return WSResponse(notifications=notifications.snapshot(_perms(ctx)))

    @staticmethod
    @UserPerms.requires.none
    def mark_read(ctx: Context, ids: List[str]):
        """Mark notifications as read (they stop counting in the badge)."""
        notifications.mark_read(ids[:MAX_IDS], _perms(ctx))

    @staticmethod
    @UserPerms.requires.none
    def dismiss(ctx: Context, ids: List[str]):
        """Remove notifications from the list, for everybody."""
        notifications.dismiss(ids[:MAX_IDS], _perms(ctx))

    @staticmethod
    @UserPerms.requires.none
    def dismiss_all(ctx: Context):
        """Remove every notification this user can see."""
        notifications.dismiss_all(_perms(ctx))

    @staticmethod
    @UserPerms.requires.none
    def invoke(ctx: Context, id: str, action: str = 'default'):
        """Click on a notification (`default`) or on one of its action buttons: the sender is told, and the notification closes
        unless the sender said it stays (freedesktop `resident`). Does nothing when the sender is not there any more."""
        notifications.invoke(id, action[:60], _perms(ctx))
