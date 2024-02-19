__all__ = [
    "DEFAULT_AP",
    "IF_WIRED",
    "IF_WIRELESS",
    "IF_ALL",
    "FALLBACK_AP_FILE",
    "create_netplan",
    "del_netplan_file",
    "do_ip_addr",
    "generate_netplan",
    "get_dns_list",
    "get_ifaces",
    "get_netplan_file_list",
    "get_netplan_file",
    "get_wifis",
    "set_hostname",
    "set_netplan",
    "start_hotspot",
    "stop_hostpot",
    "wifi_qr",
]



from os import listdir
from os import remove as removefile
from os.path import exists, isdir, join
from platform import node as get_hostname
from re import compile, sub
from socket import inet_aton, inet_ntoa
from struct import pack
from subprocess import PIPE, run
from typing import Union, Optional

from qrcode import make as make_qr
from qrcode.constants import ERROR_CORRECT_Q
from qrcode.image.svg import SvgPathFillImage
from ruamel.yaml import YAML
from starlette.websockets import WebSocket
from werkzeug.utils import secure_filename

from utils import Config
from utils.ws.wsmanager import WSAPIBase

DEFAULT_AP = None

ETC_HOSTNAME     = '/etc/hostname'
ETC_HOSTS        = '/etc/hosts'
ETC_RESOLV_CONF  = '/etc/resolv.conf'
NETPLAN_DIR      = '/etc/netplan/'
FALLBACK_AP_FILE = '/lib/netplan/99-unitotem-fb-ap.yaml'

IF_WIRED = 1
IF_WIRELESS = 2
IF_ALL = IF_WIRED | IF_WIRELESS

_YAML = YAML()


# from https://github.com/iancoleman/python-iwlist
# NO license provided

hostnameRe = compile(r'^[a-zA-Z][a-zA-Z0-9]*(-*[a-zA-Z0-9]+)*$')


cellNumberRe = compile(r"^Cell\s+(?P<cellnumber>.+)\s+-\s+Address:\s(?P<mac>.+)$")
regexps = [
    compile(r"^ESSID:\"(?P<essid>.*)\"$"),
    compile(r"^Protocol:(?P<protocol>.+)$"),
    compile(r"^Mode:(?P<mode>.+)$"),
    compile(r"^Frequency:(?P<frequency>[\d.]+) (?P<frequency_units>.+) \(Channel (?P<channel>\d+)\)$"),
    compile(r"^Encryption key:(?P<encryption>.+)$"),
    compile(r"^Quality=(?P<signal_quality>\d+)/(?P<signal_total>\d+)\s+Signal level=(?P<signal_level_dBm>.+) d.+$"),
    compile(r"^Signal level=(?P<signal_quality>\d+)/(?P<signal_total>\d+).*$"),
]

# Detect encryption type
wpaRe = compile(r"IE:\ WPA\ Version\ 1$")
wpa2Re = compile(r"IE:\ IEEE\ 802\.11i/WPA2\ Version\ 1$")


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


def get_ifaces(filter=IF_ALL, exclude=['lo']):
    wired = []
    wireless = []
    for i in listdir('/sys/class/net/'):
        if i not in exclude and isdir(join('/sys/class/net/', i)):
            (wireless if exists(join('/sys/class/net/', i, 'wireless')) else wired).append(i)
    return (wired if filter&IF_WIRED else []) + (wireless if filter&IF_WIRELESS else [])

def get_dns_list():
    with open(ETC_RESOLV_CONF, 'r') as resolv_conf:
        return [l.removeprefix('nameserver ').strip() for l in resolv_conf.readlines() if l.strip().startswith('nameserver')] # and not l.removeprefix('nameserver ').strip().startswith('127.')]


def set_netplan(filename: Union[str, None], file_content: Union[str, dict], apply = True):
    if isinstance(file_content, str):
        file_content = {filename: file_content}
    for name, content in file_content.items():
        with open(join(NETPLAN_DIR, name), 'w') as netp:
            netp.write(content)
    return generate_netplan(apply)

def generate_netplan(apply = True):
    gen_out = run(['netplan', 'generate'], stderr=PIPE).stderr.decode().strip()
    if gen_out:
        return gen_out
    if apply: run(['netplan', 'apply'])
    return apply

def create_netplan(filename):
    with open(join(NETPLAN_DIR, filename), 'w') as netp: netp.write('network:\n')

def del_netplan_file(filename, apply = True):
    removefile(join(NETPLAN_DIR, filename))
    return generate_netplan(apply)

def get_netplan_file(filename):
    if not exists(join(NETPLAN_DIR, filename)): return ''
    with open(join(NETPLAN_DIR, filename), 'r') as netp:
        return netp.read()
    
def get_netplan_file_list():
    return [file for file in listdir(NETPLAN_DIR) if file.endswith('.yaml')]

def start_hotspot(wifi_iface = get_ifaces(IF_WIRELESS)[0], ssid = get_hostname(), password = None):
    if password == None:
        password = sub(r'[^0-9A-Fa-f]', '', do_ip_addr()[wifi_iface]['mac']).upper()[-8:]
    if exists(FALLBACK_AP_FILE):
        with open(FALLBACK_AP_FILE, 'r') as netp_hotspot:
            conf = dict(_YAML.load(netp_hotspot))
            if 'network' in conf and 'wifis' in conf['network'] and wifi_iface in conf['network']['wifis'] and ssid in conf['network']['wifis'][wifi_iface]['access-points'] and 'mode' in conf['network']['wifis'][wifi_iface]['access-points'][ssid] and conf['network']['wifis'][wifi_iface]['access-points'][ssid]['mode'] == 'ap' and conf['network']['wifis'][wifi_iface]['access-points'][ssid]['password'] == password:
                return (ssid, password)
    with open(FALLBACK_AP_FILE, 'w') as netp_hotspot:
        netp_hotspot.write('# This file is automatically generated to create first time wireless access point, ANY CHANGES WILL BE LOST!\n')
        _YAML.dump({
            'network': {
                'wifis': {
                    f'{wifi_iface}': {
                        'dhcp4': True,
                        'optional': True,
                        'access-points': {
                            f'{ssid}':{
                                'password': f'{password}',
                                'mode': 'ap'
                            }
                        }
                    }
                }
            }
        }, netp_hotspot)
    run(['netplan', 'apply'])
    return (ssid, password)

def stop_hostpot():
    if exists(FALLBACK_AP_FILE):
        removefile(FALLBACK_AP_FILE)
        run(['netplan', 'apply'])

def wifi_qr(ssid, passwd):
    return sub(r'(?:(?:width)|(?:height))=\".*?mm"', '',
        make_qr(f'WIFI:S:{ssid};T:WPA;P:{passwd};;',
            error_correction=ERROR_CORRECT_Q,
            image_factory=SvgPathFillImage).to_string().decode())

# from https://github.com/iancoleman/python-iwlist
# NO license provided
def get_wifis(interface=get_ifaces(IF_WIRELESS)[0]):
    #TODO sanity check on `interface`

    lines = run(["/usr/sbin/iwlist", interface, "scan"],
            stdout=PIPE, stderr=PIPE, check=False).stdout.decode().splitlines()
    cells = []
    for line in lines:
        line = line.strip()
        cellNumber = cellNumberRe.search(line)
        if cellNumber is not None:
            cells.append(cellNumber.groupdict())
            continue
        wpa = wpaRe.search(line)
        if wpa is not None :
            cells[-1].update({'encryption':'wpa'})
        wpa2 = wpa2Re.search(line)
        if wpa2 is not None :
            cells[-1].update({'encryption':'wpa2'}) 
        for expression in regexps:
            result = expression.search(line)
            if result is not None:
                if 'encryption' in result.groupdict() :
                    if result.groupdict()['encryption'] == 'on' :
                        cells[-1].update({'encryption': 'wep'})
                    else :
                        cells[-1].update({'encryption': 'off'})
                else :
                    cells[-1].update(result.groupdict())
                continue
    for cell in cells:
        if 'frequency' in cell:
            cell['frequency'] = float(cell['frequency'])
        for attr in ['cellnumber', 'channel', 'signal_quality', 'signal_total', 'signal_level_dBm']:
            if attr in cell:
                try:
                    cell[attr] = int(cell[attr])
                except ValueError:
                    pass
        if 'essid' in cell:
            cell['essid'] = cell['essid'].replace(r'\x00', '')
    cells = sorted(cells, key = lambda x: int(x['signal_quality']), reverse=True)
    return cells

# from https://github.com/RedHatInsights/insights-core
# Licensed under Apache License 2.0
def do_ip_addr(get_default=False):
    ip_addr = run(['/usr/sbin/ip', 'addr'],
            stdout=PIPE, stderr=PIPE, check=False).stdout.decode().splitlines()
    r = {}
    current = {}
    rx_next_line = False
    tx_next_line = False
    def_iface = None
    ifaces = {}
    ifaces_out = {}
    with open("/proc/net/route") as fh:
        for line in fh:
            fields = line.strip().split()
            try:
                int(fields[1], 16)
                if fields[0] not in ifaces: ifaces[fields[0]] = {'nets': [], 'gtws':[]}
                if not def_iface: def_iface = fields[0]
                if fields[1] != '00000000' or not int(fields[3], 16) & 2:
                    ifaces[fields[0]]['nets'] += [{'dst': fields[1], 'mask': fields[7]}]
                    continue
                ifaces[fields[0]]['gtws'] += [fields[2]]
            except ValueError:
                pass
        for iface, prop in ifaces.items():
            if iface not in ifaces_out: ifaces_out[iface] = {}
            for net in prop['nets']:
                subnet = inet_ntoa(pack("<L", int(net['dst'], 16)))
                ifaces_out[iface][subnet] = {
                    'gateway': None, 
                    'mask': inet_ntoa(pack("<L", int(net['mask'], 16)))
                }
                for gtw in prop['gtws']:
                    if (int(net['mask'], 16) & int(gtw, 16)) == int(net['dst'], 16):
                        ifaces_out[iface][subnet]['gateway'] = inet_ntoa(pack("<L", int(gtw, 16)))
                        break

    ip_addr = [l.strip() for l in ip_addr if "Message truncated" not in l]
    for line in filter(None, ip_addr):
        if rx_next_line and current:
            split_content = line.split()
            current["rx_bytes"] = int(split_content[0])
            current["rx_packets"] = int(split_content[1])
            current["rx_errors"] = int(split_content[2])
            current["rx_dropped"] = int(split_content[3])
            current["rx_overrun"] = int(split_content[4])
            current["rx_mcast"] = int(split_content[5])
            rx_next_line = False
        if tx_next_line and current:
            split_content = line.split()
            current["tx_bytes"] = int(split_content[0])
            current["tx_packets"] = int(split_content[1])
            current["tx_errors"] = int(split_content[2])
            current["tx_dropped"] = int(split_content[3])
            current["tx_carrier"] = int(split_content[4])
            current["tx_collsns"] = int(split_content[5])
            tx_next_line = False
        elif line[0].isdigit() and "state" in line:
            split_content = line.split()
            idx, name, _ = line.split(":", 2)
            virtual = "@" in name
            if virtual:
                name, physical_name = name.split("@")
            current = {
                "index": int(idx),
                "name": name.strip(),
                "physical_name": physical_name if virtual else None,
                "virtual": virtual,
                "flags": split_content[2].strip("<>").split(","),
                "addr": [],
                "default": name.strip() == def_iface
            }
            # extract properties
            for i in range(3, len(split_content), 2):
                key, value = (split_content[i], split_content[i + 1])
                current[key] = int(value) if key in ["mtu", "qlen"] else value
            r[current["name"]] = current
        elif line.startswith("link"):
            split_content = line.split()
            current["type"] = split_content[0].split("/")[1]
            if "peer" in line and len(split_content) >= 3:
                current["peer_ip"] = split_content[1]
                current["peer"] = split_content[3]
            elif len(split_content) >= 2:
                current["mac"] = split_content[1]
                if "promiscuity" in split_content:
                    current["promiscuity"] = split_content[
                        split_content.index('promiscuity') + 1]
        elif 'vxlan' in line:
            split_content = line.split()
            current['vxlan'] = split_content
        elif 'openvswitch' in line:
            split_content = line.split()
            current['openvswitch'] = split_content
        elif 'geneve' in line:
            split_content = line.split()
            current['geneve'] = split_content
        elif line.startswith("inet"):
            split_content = line.split()
            p2p = "peer" in split_content
            addr, mask = split_content[3 if p2p else 1].split("/")
            gateway = None
            if current['name'] in ifaces_out:
                for subn, propts in ifaces_out[current['name']].items():
                    try:
                        _addr = int.from_bytes(inet_aton(addr), 'big')
                        _mask = 0xffffffff << (32-int(mask)) & 0xffffffff
                        _subn = int.from_bytes(inet_aton(subn), 'big')
                        if _addr & _mask == _subn:
                            gateway = propts['gateway']
                            break
                    except OSError:
                        pass # ipv6 address
            current["addr"].append({
                "addr": addr,
                "mask": mask,
                "gateway": gateway,
                "local_addr": split_content[1] if p2p else None,
                "p2p": p2p
            })
        elif line.startswith("RX"):
            rx_next_line = True
        elif line.startswith("TX"):
            tx_next_line = True
    return r[def_iface] if get_default and def_iface else False if get_default and not def_iface else r


class Settings(WSAPIBase):
    async def hostname(self, hostname: Optional[str] = None):
        if hostname is not None:
            set_hostname(hostname)
        await self.ws.broadcast('settings/hostname', hostname=get_hostname())

    async def get_wifis(self):
        await self.ws.broadcast('settings/get_wifis', wifis=get_wifis())

    class Netplan(WSAPIBase):
        async def newFile(self, filename: str):
            create_netplan(filename)
            await self.ws.broadcast('settings/netplan/file/get', files={f: get_netplan_file(f) for f in get_netplan_file_list()})

        async def getFile(self, filename: Optional[str] = None):
            netplan_files = get_netplan_file_list()
            if filename is not None:
                if filename in netplan_files:
                    await self.ws.broadcast('settings/netplan/file/get', files={filename: get_netplan_file(filename)})
            await self.ws.broadcast('settings/netplan/file/get', files={f: get_netplan_file(f) for f in netplan_files})

        async def changeFile(self, ws: WebSocket, filename: Optional[str] = None, content: str = '', apply: bool = True):
            global DEFAULT_AP
            if filename is not None:
                res = set_netplan(secure_filename(filename), content, apply)
            else:
                res = generate_netplan(apply)
            # noinspection PySimplifyBooleanCheck
            if res is True:
                if DEFAULT_AP and do_ip_addr(True):  # AP is still enabled, but now we are connected, AP is no longer needed
                    stop_hostpot()
                    DEFAULT_AP = None
                    Config.assets.next_a()
            elif isinstance(res, str):
                await self.ws.send(ws, 'error', error='Netplan error', extra=res)
            await self.ws.broadcast('settings/netplan/file/get', files={f: get_netplan_file(f) for f in get_netplan_file_list()})

        async def deleteFile(self, ws: WebSocket, filename: Optional[str] = None, apply: bool = True):
            res = del_netplan_file(filename, apply)
            if isinstance(res, str):
                await self.ws.send(ws, 'error', error='Netplan error', extra=res)
            await self.ws.broadcast('settings/netplan/file/get', files={f: get_netplan_file(f) for f in get_netplan_file_list()})