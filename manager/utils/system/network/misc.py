from os import listdir
from os.path import isdir, join, exists
from platform import node as get_hostname
from re import sub, compile


ETC_HOSTNAME     = '/etc/hostname'
ETC_HOSTS        = '/etc/hosts'
ETC_RESOLV_CONF  = '/etc/resolv.conf'

IF_WIRED = 1
IF_WIRELESS = 2
IF_ALL = IF_WIRED | IF_WIRELESS

hostnameRe = compile(r'^[a-zA-Z][a-zA-Z0-9]*(-*[a-zA-Z0-9]+)*$')


# def get_hostname():
#     return check_output('hostname').strip().decode()


def set_hostname(to_h: str, from_h: str = get_hostname()):
    to_h = to_h.strip()
    if hostnameRe.match(to_h):
        with open(ETC_HOSTNAME, 'w') as etc_hostname:
            etc_hostname.write(to_h)
        with open(ETC_HOSTS, 'r') as etc_hosts:
            hosts = etc_hosts.read()
        with open(ETC_HOSTS, 'w') as etc_hosts:
            etc_hosts.write(sub(f'127.0.1.1.*{from_h}', f'127.0.1.1\t{to_h}', hosts))


def get_ifaces(if_filter=IF_ALL, exclude=None):
    if exclude is None:
        exclude = ['lo']
    wired = []
    wireless = []
    for i in listdir('/sys/class/net/'):
        if i not in exclude and isdir(join('/sys/class/net/', i)):
            (wireless if exists(join('/sys/class/net/', i, 'wireless')) else wired).append(i)
    return (wired if if_filter & IF_WIRED else []) + (wireless if if_filter & IF_WIRELESS else [])


def get_dns_list():
    with open(ETC_RESOLV_CONF, 'r') as resolv_conf:
        return [l.removeprefix('nameserver ').strip() for l in resolv_conf.readlines() if
                l.strip().startswith('nameserver')]  # and not l.removeprefix('nameserver ').strip().startswith('127.')]
