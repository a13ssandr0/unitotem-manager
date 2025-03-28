from loguru import logger

from models import Config, UserPerms
from ws.responses import WSBroadcast, WSResponse, WSMulticast
from ws.wsmanager import Context
from ws.wsmanager import WSAPIBase


class Security(WSAPIBase):
    def getUsers(self):
        return WSBroadcast(self.getUsers,
                           users=[(user, {'perms': list(data.perms)}) for user, data in Config.users.items()])

    def addUser(self, username: str, password: str):
        if username in Config.users:
            return WSResponse(self.addUser, error="User already exists")
        Config.add_user(user=username, password=password)
        Config.save()
        logger.info(f'Created new user: {username}')
        return self.getUsers()

    def setUserPass(self, ctx: Context, username: str, password: str):
        if username not in Config.users:
            return WSResponse(self.setUserPass, error=f"User {username} does not exist")
        Config.change_password(username, password)
        Config.save()
        logger.info(f'Password changed for {username}')
        return WSMulticast(username, 'logout')

    def setUserPerms(self, ctx: Context, username: str, perms: set[UserPerms]):
        if username not in Config.users:
            yield WSResponse(self.setUserPerms, error=f"User {username} does not exist")
            return
        if ctx.username == username and UserPerms.admin in Config.users[
            username].perms and UserPerms.admin not in perms:
            for user, userdata in Config.users.items():
                if user != ctx.username and UserPerms.admin in userdata.perms:
                    break
            else:
                # we have no other user with user management capabilities cannot continue
                yield WSResponse(self.setUserPerms, error="Cannot remove permissions from the only admin")
                yield self.getUsers()
                return

        Config.users[username].perms = perms
        Config.save()
        logger.info(f'Changed permissions for {username}: {perms}')
        yield WSMulticast(ctx.username, 'reload')
        yield self.getUsers()

    def delUser(self, ctx: Context, user: str):
        # Only users with "settings" permissions can manage users and access this method, this means we just have
        # to check that the user is not trying to delete itself and the user is not the only one in the system (maybe redundant)
        if ctx.username == user or len(Config.users) == 1:
            return WSResponse(self.delUser, error="Cannot delete current user")
        elif user in Config.users:
            del Config.users[user]
            Config.save()
        return self.getUsers()
