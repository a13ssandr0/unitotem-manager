from loguru import logger

from utils.models.user import UserPerms


def check_permissions(func, user):
    name = func.__name__
    try: name = func.api_path
    except AttributeError: pass

    perms = {UserPerms.admin}
    try:
        if func.perms is None:
            logger.debug(f'{name} requires no permissions to be executed')
            return

        logger.debug(f'{name} requires {" or ".join(func.perms)} permission to be executed')
        perms = func.perms
    except AttributeError:
        logger.debug(f'{name} has no permissions set, assuming admin')

    if user.has_perm.admin or user.perms & perms:
        logger.info(f'User {user.name} is allowed to execute {name}')
    else:
        logger.critical(f'User {user.name} is not allowed to execute {name}')
        raise APIPermissionError


class APIPermissionError(Exception):
    pass