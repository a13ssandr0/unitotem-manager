#!/usr/bin/env python3
"""
Runs ON the board: measures SECONDS of whatever the window is showing and prints one JSON line.

    rotmeasure.py SECONDS

CPU of the manager's whole process tree (viewer + CEF subprocesses) and of the compositor, as /proc deltas in % of one
core; GPU engine busy % from the DRM fdinfo of every process; temperature and throttling; and what the test page reported
(frames drawn per second, slow frames, dropped video frames) from rotsrv.py.
"""
import collections
import glob
import json
import os
import subprocess
import sys
import time
import urllib.request

TICK = os.sysconf('SC_CLK_TCK')


def procs():
    out = {}
    for name in os.listdir('/proc'):
        if name.isdigit():
            try:
                raw = open(f'/proc/{name}/stat').read()
                rest = raw[raw.rindex(')') + 2:].split()
                out[int(name)] = (int(rest[1]), int(rest[11]) + int(rest[12]), raw[raw.index('(') + 1:raw.rindex(')')])
            except (OSError, ValueError, IndexError):
                pass
    return out


def cpu_sets():
    manager = int(subprocess.run(['systemctl', 'show', '-p', 'MainPID', '--value', 'unitotem-manager'], capture_output=True, text=True).stdout)
    p = procs()
    tree, todo = {manager}, [manager]
    while todo:
        cur = todo.pop()
        for pid, (ppid, _, _) in p.items():
            if ppid == cur and pid not in tree:
                tree.add(pid)
                todo.append(pid)
    comp = {pid for pid, (_, _, comm) in p.items() if comm == 'labwc'}
    return tree, comp


def ticks(pids):
    p = procs()
    return sum(p[x][1] for x in pids if x in p)


def gpu():
    seen, busy = set(), collections.Counter()
    for path in glob.glob('/proc/[0-9]*/fdinfo/*'):
        try:
            text = open(path).read()
        except OSError:
            continue
        if 'drm-driver' not in text:
            continue
        pid = path.split('/')[2]
        kv = dict(l.split(':', 1) for l in text.splitlines() if ':' in l)
        key = (pid, kv.get('drm-client-id', path).strip())
        if key in seen:
            continue
        seen.add(key)
        for k, v in kv.items():
            if k.startswith('drm-engine-') and not k.startswith('drm-engine-capacity'):
                busy[k[len('drm-engine-'):]] += int(v.split()[0])
    return busy


def rd(path):
    try:
        return open(path).read().strip()
    except OSError:
        return None


def hw():
    t = rd('/sys/class/thermal/thermal_zone0/temp')
    throttled = subprocess.run(['vcgencmd', 'get_throttled'], capture_output=True, text=True).stdout.strip() if os.path.exists('/usr/bin/vcgencmd') else None
    freq = rd('/sys/class/drm/card0/gt/gt0/rps_act_freq_mhz')
    return {'temp': int(t) / 1000 if t else None, 'throttled': throttled, 'gt_mhz': int(freq) if freq else None}


def main(seconds):
    tree, comp = cpu_sets()
    t0, c0, k0, g0, since = time.time(), ticks(tree), ticks(comp), gpu(), time.time()
    maxtemp = 0
    for _ in range(seconds):
        time.sleep(1)
        maxtemp = max(maxtemp, hw()['temp'] or 0)
    dt = time.time() - t0
    c1, k1, g1 = ticks(tree), ticks(comp), gpu()
    stats = json.load(urllib.request.urlopen(f'http://127.0.0.1:8099/stats?since={since}'))
    res = {'viewer_cpu': round((c1 - c0) / TICK / dt * 100), 'compositor_cpu': round((k1 - k0) / TICK / dt * 100),
           'gpu_busy': {k: round((g1[k] - g0[k]) / 1e9 / dt * 100) for k in g1}, 'temp_max': maxtemp, **hw()}
    if len(stats) >= 2:
        a, b = stats[0], stats[-1]
        span = b['at'] - a['at']
        res.update(fps=round((b['drawn'] - a['drawn']) / span, 1), slow_per_s=round((b['slow'] - a['slow']) / span, 1),
                   worst_ms=round(max(s['worst'] for s in stats)), size=f"{b['vw']}x{b['vh']}",
                   dropped_pct=round(100 * (b['drop'] - a['drop']) / max(b['tot'] - a['tot'], 1), 1) if b['tot'] else None)
    print(json.dumps(res))


main(int(sys.argv[1]))
