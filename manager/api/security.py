from loguru import logger

from utils import osusers, passwords
from utils.models.security import security_settings
from utils.models.user import UserPerms, user_manager
from api.ws.responses import WSBroadcast, WSResponse, WSMulticast
from api.ws.wsmanager import Context
from api.ws.wsmanager import WSAPIBase


def _policy_problems(password: str, username: str) -> list[str]:
    """What objects to the password under the password policy (an empty list means it is accepted)."""
    return passwords.validate_password(password, username)


class Security(WSAPIBase):
    @staticmethod
    def users():
        # Names and permissions go to the administrators only
        return WSMulticast(user_manager.admins(),
                           users={user: {'perms': list(data.permissions)} for user, data in user_manager.items()})

    @staticmethod
    @UserPerms.requires.none
    def current_user(ctx: Context):
        """The logged in user itself: every page needs it to know what to show, whatever its permissions."""
        user = user_manager.get_user(ctx.username)
        return WSResponse(name=ctx.username, perms=list(user.perms) if user else [])

    @staticmethod
    @UserPerms.requires.none
    def password_policy():
        """The password policy: the numbers and the checklist rows the password form shows."""
        return WSBroadcast(rules=passwords.rules_spec())

    @staticmethod
    def login_user_list():
        """Whether the login page lists the account names."""
        return WSMulticast(user_manager.admins(), show=security_settings.show_login_users)

    def set_login_user_list(self, show: bool):
        """Show the account names on the login page, or ask for a typed user name."""
        security_settings.show_login_users = show
        security_settings.save()
        logger.info('User list on the login page {}', 'shown' if show else 'hidden')
        return self.login_user_list()

    def add_user(self, username: str, password: str):
        if username in user_manager:
            return WSResponse(error="User already exists")
        if problems := _policy_problems(password, username):
            return WSResponse(error='Password refused', extra=problems)
        try:
            user_manager.add_user(username, password)
        except osusers.OSUserError as e:
            return WSResponse(error=str(e))
        logger.info(f'Created new user: {username}')
        return self.users()

    @staticmethod
    def set_user_password(ctx: Context, username: str, password: str):
        if username not in user_manager:
            return WSResponse(error=f"User {username} does not exist")
        if problems := _policy_problems(password, username):
            return WSResponse(error='Password refused', extra=problems)
        try:
            user_manager.change_password(username, password)
        except osusers.OSUserError as e:
            return WSResponse(error=str(e))
        logger.info(f'Password changed for {username}')
        # The sessions of that user end (the server closes their sockets); when it is one's own password the session it was
        # changed from stays, every other one ends
        user_manager.revoke_sessions(username, keep_sid=ctx.sid if username == ctx.username else None)
        return None

    def set_user_permissions(self, ctx: Context, username: str, perms: set[UserPerms]):
        if username not in user_manager:
            yield WSResponse(error=f"User {username} does not exist")
            return
        # An admin can never remove their own admin role
        if ctx.username == username and osusers.is_admin(username) and UserPerms.admin not in perms:
            yield WSResponse(error="An admin cannot remove their own admin role")
            yield self.users()
            return

        try:
            user_manager.change_perms(username, perms)
        except osusers.OSUserError as e:
            yield WSResponse(error=str(e))
            yield self.users()
            return
        logger.info(f'Changed permissions for {username}: {perms}')
        yield WSMulticast(ctx.username, 'reload')
        yield self.users()

    def delete_user(self, ctx: Context, username: str):
        # Only users with "settings" permissions can manage users and access this method, this means we just have
        # to check that the user is not trying to delete itself and the user is not the only one in the system (maybe redundant)
        if ctx.username == username or len(user_manager) == 1:
            return WSResponse(error="Cannot delete current user")
        elif username in user_manager:
            user_manager.delete_user(username)
        return self.users()
