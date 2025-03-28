from os.path import exists
from select import select
from subprocess import Popen, PIPE, run

# https://serverfault.com/questions/300749/apt-get-update-upgrade-list-without-changing-anything


upgradableRe = compile(
    r"(?P<package>.*)/(?P<origin>.*?) (?P<new_version>.*?) (?P<architecture>.*?) \[upgradable from: (?P<old_version>.*?)]")

_last_log = []
_upd_list_cache = []
# APT_THREAD           = Thread(target=apt_update, name='update')
REBOOT_REQ = '/var/run/reboot-required'
REBOOT_REQ_PKGS = '/var/run/reboot-required.pkgs'


def apt_update(upgrade=False):
    global _last_log
    cmd = ['/usr/bin/apt-get', 'dist-upgrade', '-y'] if upgrade else ['/usr/bin/apt-get', 'update']
    _last_log.clear()
    with Popen(cmd, stdout=PIPE, stderr=PIPE, bufsize=0) as proc:
        yield True
        sout = proc.stdout
        serr = proc.stderr
        while proc.poll() is None:
            descriptors = select([sout, serr], [], [])[0]
            for d in descriptors:
                if d is sout and sout is not None:
                    line = (True, sout.readline().decode())
                    _last_log.append(line)
                    yield line
                elif d is serr and serr is not None:
                    line = (False, serr.readline().decode())
                    _last_log.append(line)
                    yield line
    yield False


def get_apt_log():
    return _last_log


def apt_list_upgrades():
    return list(map(lambda x: upgradableRe.match(x.strip()).groupdict(),  # type: ignore
                    filter(lambda l: 'upgradable' in l,
                           run(['/usr/bin/apt', 'list', '--upgradable'],
                               stdout=PIPE, stderr=PIPE, env={'LANG': 'C'}
                               ).stdout.decode().splitlines())))


def reboot_required():
    if exists(REBOOT_REQ_PKGS):
        with open(REBOOT_REQ_PKGS, 'r') as file:
            return list(filter(None, map(lambda x: x.strip(), file.readlines())))
    elif exists(REBOOT_REQ):
        return True
    return False
