"""
Network related settings require the program to access files only writable by root.
In order to avoid elevating the whole manager like in previous versions,
a daemon is launched as root and listens to rpyc local connections and acts as
backend for changing netplan configuration and setting the hostname of the system
"""
import shutil
from pathlib import Path
from socket import gethostname
from re import compile, sub
from subprocess import PIPE, run
from typing import Union

import rpyc
import rpyc.utils.server as rpyc_server
from loguru import logger
from subprocess import run

ETC_HOSTNAME = '/etc/hostname'
ETC_HOSTS = '/etc/hosts'

hostnameRe = compile(r'^[a-zA-Z][a-zA-Z0-9]*(-*[a-zA-Z0-9]+)*$')


class Netplan(rpyc.Service):
    netplan_dir = Path('/etc/netplan/')

    def on_connect(self, conn):
        # noinspection PyAttributeOutsideInit
        self.logger = conn.root

    def exposed_set_hostname(self, hostname: str):
        hostname = hostname.strip()[:64]  # linux hostnames can't be longer than 64 characters
        if hostnameRe.match(hostname):
            self.logger.info('Setting new hostname to {}', hostname)
            run(['hostname', hostname])
            with open(ETC_HOSTNAME, 'w') as file:
                file.write(hostname)
            with open(ETC_HOSTS, 'r+') as file:
                hosts = file.read()
                file.seek(0)
                file.write(sub(f'127.0.1.1.*{gethostname()}', f'127.0.1.1\t{hostname}', hosts))
                file.truncate()
            return True
        else:
            self.logger.error('Invalid hostname')
            return False

    def exposed_set_netplan(self, filename: 'Union[str, None]', file_content: 'Union[str, dict]', apply=True):
        if isinstance(file_content, str):
            file_content = {filename: file_content}
        for name, content in file_content.items():
            file = self.netplan_dir / name
            self.logger.info("Writing file {}", file)
            with open(file, 'w') as netp:
                netp.write(content)
            file.chmod(0o500)
        return self.exposed_generate_netplan(apply)

    def exposed_generate_netplan(self, apply=True):
        self.logger.info("Generating Netplan configuration")
        gen_out = run(['/usr/sbin/netplan', 'generate'], stderr=PIPE, text=True).stderr.strip()
        if gen_out:
            self.logger.error("Netplan generation failed")
            for l in gen_out.splitlines():
                self.logger.debug(l)
            return gen_out
        if apply:
            self.logger.info("Applying Netplan configuration")
            run(['/usr/sbin/netplan', 'apply'])
        return apply

    def exposed_create_netplan(self, filename):
        file = self.netplan_dir / filename
        self.logger.info("Creating file {}", file)
        with open(file, 'w') as netp:
            netp.write('network:\n')
        file.chmod(0o500)

    def exposed_del_netplan_file(self, filename, apply=True):
        file = self.netplan_dir / filename
        self.logger.info('Deleting {}', file)
        file.unlink(missing_ok=True)
        return self.exposed_generate_netplan(apply)

    def exposed_get_netplan_file(self, filename):
        file = self.netplan_dir / filename
        self.logger.info("Reading file {}", file)
        if not file.exists():
            return ''
        with open(file, 'r') as netp:
            return netp.read()

    def exposed_get_netplan_file_list(self):
        self.logger.info('Getting files')
        return sorted([f.name for f in self.netplan_dir.glob('*.yaml')])


if __name__ == "__main__":
    sock_path = Path('/run/unitotem/nm.sock')
    sock_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info('Staring UniTotem admin daemon...')
    t = rpyc_server.ThreadedServer(Netplan, socket_path=str(sock_path))
    shutil.chown(sock_path, group='unitotem')
    sock_path.chmod(0o760)
    logger.success('Created socket {}', sock_path)
    try:
        t.start()
    finally:
        sock_path.unlink(missing_ok=True)
