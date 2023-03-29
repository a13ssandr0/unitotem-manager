from contextlib import closing
from os.path import exists
from re import compile
from select import select
from subprocess import PIPE, Popen, run

upgradableRe = compile(r"(?P<package>.*)/(?P<origin>.*?) (?P<new_version>.*?) (?P<architecture>.*?) \[upgradable from: (?P<old_version>.*?)]")
os_name_Re = compile(r'PRETTY_NAME="(.*?)"')

_last_log = []
REBOOT_REQ       = '/var/run/reboot-required'
REBOOT_REQ_PKGS  = '/var/run/reboot-required.pkgs'


def apt_update(upgrade=False):
    global _last_log
    cmd = ['/usr/bin/apt-get', 'dist-upgrade', '-y'] if upgrade else ['/usr/bin/apt-get', 'update']
    proc = Popen(cmd, stdout=PIPE, stderr=PIPE, bufsize=0)
    sout = proc.stdout
    serr = proc.stderr
    _last_log.clear()
    yield True
    with closing(sout), closing(serr):
        while proc.poll() is None:
            descriptors = select([sout, serr], [], [])[0]
            for d in descriptors:
                if d is sout and sout != None:
                    line = (True, sout.readline().decode())
                    _last_log += line
                    yield line
                elif d is serr and serr != None:
                    line = (False, serr.readline().decode())
                    _last_log += line
                    yield line
    yield False

def get_apt_log():
    return _last_log

def apt_list_upgrades():
    return list(map(lambda x: upgradableRe.match(x.strip()).groupdict(),
            filter(lambda l: 'upgradable' in l,
                run(['/usr/bin/apt', 'list', '--upgradable'],
                    stdout=PIPE, stderr=PIPE, env={'LANG':'C'}
                    ).stdout.decode().splitlines())))

def reboot_required():
    if exists(REBOOT_REQ_PKGS):
        with open(REBOOT_REQ_PKGS, 'r') as file:
            return filter(map(lambda x: x.strip(), file))
    elif exists(REBOOT_REQ):
        return True
    return False

def os_version():
    with open('/etc/os-release', 'r') as f:
        match = os_name_Re.search(f.read())
        if match:
            return match.group(1)