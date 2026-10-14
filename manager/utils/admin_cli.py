"""
`unitotem-manager --create-admin NAME`: create an administrator (or reset the password of an existing user and make it
an administrator) from the machine itself, then exit.

For root on the device: it is how a node is recovered when nobody can log in, and how automation (the test VM) sets up
accounts without the claim code. It is not reachable from the network, and it leaves the node claimed. The password is
asked on the terminal, or read from standard input when there is no terminal (`echo "$PASSWORD" | unitotem-manager
--create-admin NAME`); it is never taken from the command line, where every user could read it.
"""
import getpass
import os
import socket
import sys

from utils import passwords


def _read_password() -> str:
    if sys.stdin.isatty():
        first = getpass.getpass('New password: ')
        if first != getpass.getpass('Repeat the password: '):
            sys.exit('The passwords do not match')
        return first
    return sys.stdin.readline().rstrip('\n')


def _service_is_running(port: int) -> bool:
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=1):
            return True
    except OSError:
        return False


def create_admin(username: str, port_secure: int) -> int:
    """Returns the process exit status."""
    if os.geteuid() != 0:
        print('Only root can do this', file=sys.stderr)
        return 1
    # The running manager keeps the users in memory and writes them back: an account created behind its back is lost
    if _service_is_running(port_secure):
        print('The manager is running: stop it first (systemctl stop unitotem-manager), then start it again.',
              file=sys.stderr)
        return 1
    password = _read_password()
    problems = passwords.validate_username(username) + passwords.validate_password(password, username)
    if problems:
        print('\n'.join(problems), file=sys.stderr)
        return 1

    from utils.claim import claim_manager
    from utils.models.user import UserPerms, user_manager
    try:
        user_manager.load()
    except FileNotFoundError:
        pass
    user_manager.add_user(username, password, {UserPerms.admin})
    claim_manager.arm()                  # claimed now: removes a pending claim code and its file
    print(f'Administrator {username} is ready.')
    return 0
