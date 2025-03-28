from re import compile
from subprocess import PIPE, run

from utils.system.network.misc import get_ifaces, IF_WIRELESS

# from https://github.com/iancoleman/python-iwlist
# NO license provided



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
wpaRe = compile(r"IE: WPA Version 1$")
wpa2Re = compile(r"IE: IEEE 802\.11i/WPA2 Version 1$")


# from https://github.com/iancoleman/python-iwlist
# NO license provided
def get_wifis(interface=get_ifaces(IF_WIRELESS)[0]):
    #TODO sanity check on `interface`

    lines = run(["/usr/sbin/iwlist", interface, "scan"],
            stdout=PIPE, stderr=PIPE, check=False).stdout.decode().splitlines()
    cells = []
    for line in lines:
        line = line.strip()
        cell_number = cellNumberRe.search(line)
        if cell_number is not None:
            cells.append(cell_number.groupdict())
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