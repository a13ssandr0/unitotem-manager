from loguru import logger

from utils import passwords
from utils.models.security import security_settings
from utils.models.user import UserPerms, user_manager
from api.ws.responses import WSBroadcast, WSResponse, WSMulticast
from api.ws.wsmanager import Context
from api.ws.wsmanager import WSAPIBase


def _policy_problems(password: str, username: str) -> list[str]:
    """What the password policy objects to, when the node enforces it (nothing otherwise)."""
    return passwords.validate_password(password, username) if security_settings.enforce_password_policy else []


class Security(WSAPIBase):
    @staticmethod
    def users():
        return WSBroadcast(users={user: {'perms': list(data.permissions)} for user, data in user_manager.items()})

    @staticmethod
    @UserPerms.requires.none
    def current_user(ctx: Context):
        """The logged in user itself: every page needs it to know what to show, whatever its permissions."""
        return WSResponse(name=ctx.username, perms=list(user_manager[ctx.username].permissions))

    @staticmethod
    @UserPerms.requires.none
    def password_policy():
        """Whether the password policy is enforced for the passwords an administrator sets, and what it asks."""
        return WSBroadcast(enforced=security_settings.enforce_password_policy, rules=passwords.RULES)

    def set_password_policy(self, enforce: bool):
        """Opt in or out of enforcing the password policy when users are created or their password changes."""
        security_settings.enforce_password_policy = enforce
        security_settings.save()
        logger.info('Password policy {}', 'enforced' if enforce else 'not enforced')
        return self.password_policy()

    def add_user(self, username: str, password: str):
        if username in user_manager:
            return WSResponse(error="User already exists")
        if problems := _policy_problems(password, username):
            return WSResponse(error='Password refused', extra=problems)
        user_manager.add_user(username, password)
        logger.info(f'Created new user: {username}')
        return self.users()

    @staticmethod
    def set_user_password(username: str, password: str):
        if username not in user_manager:
            return WSResponse(error=f"User {username} does not exist")
        if problems := _policy_problems(password, username):
            return WSResponse(error='Password refused', extra=problems)
        user_manager.change_password(username, password)
        logger.info(f'Password changed for {username}')
        return WSMulticast(username, 'logout')

    def set_user_permissions(self, ctx: Context, username: str, perms: set[UserPerms]):
        if username not in user_manager:
            yield WSResponse(error=f"User {username} does not exist")
            return
        # An admin can never remove their own admin role
        if ctx.username == username and UserPerms.admin in user_manager[username].permissions and UserPerms.admin not in perms:
            yield WSResponse(error="An admin cannot remove their own admin role")
            yield self.users()
            return

        user_manager.change_perms(username, perms)
        logger.info(f'Changed permissions for {username}: {perms}')
        yield WSMulticast(ctx.username, 'reload')
        yield self.users()

    def delete_user(self, ctx: Context, username: str):
        # Only users with "settings" permissions can manage users and access this method, this means we just have
        # to check that the user is not trying to delete itself and the user is not the only one in the system (maybe redundant)
        if ctx.username == username or len(user_manager) == 1:
            return WSResponse(error="Cannot delete current user")
        elif username in user_manager:
            del user_manager[username]
        return self.users()
