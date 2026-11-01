from typing import List

from loguru import logger

from api.ws.responses import WSResponse
from api.ws.wsmanager import Context, WSAPIBase
from utils.models.user import UserPerms, user_manager
from utils.notifications import notifications, visible_to

MAX_IDS = 200       # more than the list can hold: a longer request is a mistake or an attack, either way cut


def _eligible(item) -> set:
    """The users who can see a notification: it is hidden for good when all of them have dismissed it."""
    return {name for name, data in user_manager.items() if visible_to(item, data.permissions)}


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
            await WS.multicast(name, 'Notifications/list', notifications=notifications.snapshot(user.perms, name))
        except Exception as exc:        # a socket that just closed must not keep the others from their update
            logger.debug('Cannot push the notifications to {}: {}', name, exc)


class Notifications(WSAPIBase):
    """What the node wants its users to know. Every function is open to any logged in user, and what they get (or
    can change) is only what their capabilities allow them to see."""

    @staticmethod
    @UserPerms.requires.none
    def list(ctx: Context):
        """The notifications this user may see, newest first (pushed again to every page whenever they change)."""
        return WSResponse(notifications=notifications.snapshot(_perms(ctx), ctx.username))

    @staticmethod
    @UserPerms.requires.none
    def mark_read(ctx: Context, ids: List[str]):
        """Mark notifications as read (they stop counting in the badge)."""
        notifications.mark_read(ids[:MAX_IDS], _perms(ctx), ctx.username)

    @staticmethod
    @UserPerms.requires.none
    def dismiss(ctx: Context, ids: List[str]):
        """Remove notifications from this user's list (they stay for the others until every user who can see them has dismissed them)."""
        notifications.dismiss(ids[:MAX_IDS], _perms(ctx), ctx.username, _eligible)

    @staticmethod
    @UserPerms.requires.none
    def dismiss_all(ctx: Context):
        """Remove every notification this user can see from their list."""
        notifications.dismiss_all(_perms(ctx), ctx.username, _eligible)

    @staticmethod
    @UserPerms.requires.none
    def invoke(ctx: Context, id: str, action: str = 'default'):
        """Click on a notification (`default`) or on one of its action buttons: the sender is told, and the notification closes
        unless the sender said it stays (freedesktop `resident`). Does nothing when the sender is not there any more."""
        notifications.invoke(id, action[:60], _perms(ctx))
