from _socket import inet_ntoa, inet_aton
from struct import pack
from subprocess import run, PIPE


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
            # noinspection PyUnboundLocalVariable
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
            # noinspection PyTypeChecker
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
