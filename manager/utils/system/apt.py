from loguru import logger
from os.path import exists
from re import compile
from select import select
from subprocess import PIPE, Popen, run
from threading import Thread

# https://serverfault.com/questions/300749/apt-get-update-upgrade-list-without-changing-anything


class APT:
    _upgradableRe = compile(
        r"(?P<package>.*)/(?P<origin>.*?) (?P<new_version>.*?) (?P<architecture>.*?) \[upgradable from: (?P<old_version>.*?)]")


    def __init__(self):
        self.__thread = Thread(target=self.__run_apt_get, args=('update',), name='update')
        self.__last_log = []
        self.__ret_code = None

        self.__on_start = None
        self.__on_progress = None
        self.__on_end = None
        self.__on_end_post = None

    def __run_apt_get(self, *args):
        #TODO switch to pkexec ASAP

        # cmd = ['/usr/bin/apt-get', 'dist-upgrade', '-y'] if upgrade else ['/usr/bin/apt-get', 'update']
        self.__last_log.clear()
        upgrading = self.__thread.name == 'upgrade'
        with Popen(['/usr/bin/sudo', '/usr/bin/apt-get', *args], stdout=PIPE, stderr=PIPE, 
                   bufsize=0, env={'DEBIAN_FRONTEND':'noninteractive'}, text=True) as proc:
            if self.__on_start:
                self.__on_start(upgrading=upgrading)
            logger.info("APT: Running command 'sudo apt-get {}'", ' '.join(args))
            sout = proc.stdout
            serr = proc.stderr
            while proc.poll() is None:
                descriptors = select([sout, serr], [], [])[0]
                for d in descriptors:
                    if d is sout and sout is not None:
                        line = sout.readline()
                        if line.strip():
                            logger.info('APT: {}', line.rstrip())
                        self.__last_log.append((True, line))
                        if self.__on_progress:
                            self.__on_progress(upgrading=upgrading, is_stdout=True, data=line)
                    elif d is serr and serr is not None:
                        line = serr.readline()
                        if line.strip():
                            logger.warning('APT: {}', line.rstrip())
                        self.__last_log.append((False, line))
                        if self.__on_progress:
                            self.__on_progress(upgrading=upgrading, is_stdout=False, data=line)
            self.__ret_code = proc.returncode
            if self.__ret_code == 0:
                logger.success('APT: Program terminated successfully')
            else:
                logger.warning('APT: Program failed with code {}', proc.returncode)
            if self.__on_end:
                self.__on_end(upgrading=upgrading, returncode=proc.returncode)

        if self.__on_end_post:
            self.__on_end_post(upgrading=upgrading)

    def update(self):
        if not self.__thread.is_alive():
            self.__thread = Thread(target=self.__run_apt_get, args=('update',), name='update')
            self.__thread.start()

    def upgrade(self):
        if not self.__thread.is_alive():
            self.__thread = Thread(target=self.__run_apt_get, args=('dist-upgrade', '-y'), name='upgrade')
            self.__thread.start()

    def on_start(self, target):
        self.__on_start = target

    def on_progress(self, target):
        self.__on_progress = target

    def on_end(self, target):
        self.__on_end = target

    def on_end_post(self, target):
        self.__on_end_post = target

    @property
    def log(self):
        return self.__last_log

    @property
    def status(self):
        return self.__thread.name if self.__thread.is_alive() else None

    @property
    def returncode(self):
        return self.__ret_code

    @classmethod
    def list_upgradable(cls):
        return list(map(lambda x: cls._upgradableRe.match(x.strip()).groupdict(),
                        filter(lambda l: 'upgradable' in l,
                               run(['/usr/bin/apt', 'list', '--upgradable'],
                                   stdout=PIPE, stderr=PIPE, env={'LANG': 'C'}, text=True
                                   ).stdout.splitlines())))

    @property
    def reboot_required(self):
        try:
            with open('/var/run/reboot-required.pkgs', 'r') as file:
                return list(filter(None, map(lambda x: x.strip(), file.readlines())))
        except FileNotFoundError:
            if exists('/var/run/reboot-required'):
                return True
        return False


apt = APT()
