from loguru import logger

from utils.models.user import UserPerms, user_manager
from api.ws.responses import WSBroadcast, WSResponse, WSMulticast
from api.ws.wsmanager import Context
from api.ws.wsmanager import WSAPIBase


class Security(WSAPIBase):
    @staticmethod
    def getUsers():
        return WSBroadcast(users=[(user, {'perms': list(data.perms)}) for user, data in user_manager.items()])

    def addUser(self, username: str, password: str):
        if username in user_manager:
            return WSResponse(error="User already exists")
        user_manager.add_user(user=username, password=password)
        logger.info(f'Created new user: {username}')
        return self.getUsers()

    @staticmethod
    def setUserPass(username: str, password: str):
        if username not in user_manager:
            return WSResponse(error=f"User {username} does not exist")
        user_manager.change_password(username, password)
        logger.info(f'Password changed for {username}')
        return WSMulticast(username, 'logout')

    def setUserPerms(self, ctx: Context, username: str, perms: set[UserPerms]):
        if username not in user_manager:
            yield WSResponse(error=f"User {username} does not exist")
            return
        if ctx.username == username and UserPerms.admin in user_manager[
            username].perms and UserPerms.admin not in perms:
            for user, userdata in user_manager.items():
                if user != ctx.username and UserPerms.admin in userdata.perms:
                    break
            else:
                # we have no other user with user management capabilities cannot continue
                yield WSResponse(error="Cannot remove permissions from the only admin")
                yield self.getUsers()
                return

        user_manager[username].perms = perms
        user_manager.save()
        logger.info(f'Changed permissions for {username}: {perms}')
        yield WSMulticast(ctx.username, 'reload')
        yield self.getUsers()

    def delUser(self, ctx: Context, user: str):
        # Only users with "settings" permissions can manage users and access this method, this means we just have
        # to check that the user is not trying to delete itself and the user is not the only one in the system (maybe redundant)
        if ctx.username == user or len(user_manager) == 1:
            return WSResponse(error="Cannot delete current user")
        elif user in user_manager:
            del user_manager[user]
        return self.getUsers()
