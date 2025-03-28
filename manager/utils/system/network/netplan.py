from os import remove as removefile, listdir
from os.path import join, exists
from subprocess import run, PIPE
from typing import Union

NETPLAN_DIR = '/etc/netplan/'


def set_netplan(filename: Union[str, None], file_content: Union[str, dict], apply=True):
    if isinstance(file_content, str):
        file_content = {filename: file_content}
    for name, content in file_content.items():
        with open(join(NETPLAN_DIR, name), 'w') as netp:
            netp.write(content)
    return generate_netplan(apply)


def generate_netplan(apply=True):
    gen_out = run(['netplan', 'generate'], stderr=PIPE).stderr.decode().strip()
    if gen_out:
        return gen_out
    if apply: run(['netplan', 'apply'])
    return apply


def create_netplan(filename):
    with open(join(NETPLAN_DIR, filename), 'w') as netp: netp.write('network:\n')


def del_netplan_file(filename, apply=True):
    removefile(join(NETPLAN_DIR, filename))
    return generate_netplan(apply)


def get_netplan_file(filename):
    if not exists(join(NETPLAN_DIR, filename)): return ''
    with open(join(NETPLAN_DIR, filename), 'r') as netp:
        return netp.read()


def get_netplan_file_list():
    return [file for file in listdir(NETPLAN_DIR) if file.endswith('.yaml')]
