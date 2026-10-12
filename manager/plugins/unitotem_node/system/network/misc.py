from os import listdir
from os.path import isdir, join, exists
from socket import gethostname
from re import sub, compile



IF_WIRED = 1
IF_WIRELESS = 2
IF_ALL = IF_WIRED | IF_WIRELESS

def set_hostname(aaa):
    pass


def get_ifaces(if_filter=IF_ALL, exclude=None):
    if exclude is None:
        exclude = ['lo']
    wired = []
    wireless = []
    for i in listdir('/sys/class/net/'):
        if i not in exclude and isdir(join('/sys/class/net/', i)):
            (wireless if exists(join('/sys/class/net/', i, 'wireless')) else wired).append(i)
    return (wired if if_filter & IF_WIRED else []) + (wireless if if_filter & IF_WIRELESS else [])

def get_default_wireless():
    try:
        return get_ifaces(IF_WIRELESS)[0]
    except IndexError:
        return None

def get_dns_list():
    with open('/etc/resolv.conf', 'r') as resolv_conf:
        return [l.removeprefix('nameserver ').strip() for l in resolv_conf.readlines() if
                l.strip().startswith('nameserver')]  # and not l.removeprefix('nameserver ').strip().startswith('127.')]
