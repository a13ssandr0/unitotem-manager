#!/usr/bin/env python3
"""
Runs ON the board: CPU of the whole process tree of the manager (the viewer's CEF subprocesses are children), as the
delta of /proc/<pid>/stat over a window, not `ps` (a lifetime average). Prints % of ONE core, total and per process kind.

    board-cpu.py SECONDS [LABEL]
"""
import collections
import os
import subprocess
import sys
import time

TICK = os.sysconf('SC_CLK_TCK')


def snapshot(root):
    procs = {}
    for name in os.listdir('/proc'):
        if not name.isdigit():
            continue
        try:
            raw = open(f'/proc/{name}/stat').read()
            rest = raw[raw.rindex(')') + 2:].split()
            comm = raw[raw.index('(') + 1:raw.rindex(')')]
            cmdline = open(f'/proc/{name}/cmdline').read().replace('\0', ' ')
            procs[int(name)] = (int(rest[1]), int(rest[11]) + int(rest[12]), comm, cmdline)   # ppid, utime+stime, name
        except (OSError, ValueError, IndexError):
            pass
    tree, todo = {root}, [root]
    while todo:
        cur = todo.pop()
        for pid, (ppid, *_r) in procs.items():
            if ppid == cur and pid not in tree:
                tree.add(pid)
                todo.append(pid)
    return {pid: procs[pid] for pid in tree if pid in procs}


def kind(comm, cmdline):
    for t in ('gpu-process', 'renderer', 'utility', 'zygote'):
        if f'--type={t}' in cmdline:
            return t
    return 'manager (python)' if 'init.py' in cmdline else comm


def main():
    seconds = float(sys.argv[1])
    label = sys.argv[2] if len(sys.argv) > 2 else ''
    root = int(subprocess.check_output(['systemctl', 'show', '-p', 'MainPID', '--value', 'unitotem-manager']).strip())
    a = snapshot(root)
    t0 = time.monotonic()
    time.sleep(seconds)
    b = snapshot(root)
    dt = time.monotonic() - t0
    per = collections.Counter()
    for pid, (_pp, ticks, comm, cmd) in b.items():
        before = a.get(pid, (0, ticks))[1]
        per[kind(comm, cmd)] += ticks - before
    total = sum(per.values())
    print(f'{label}: {100 * total / TICK / dt:.1f}% of one core over {dt:.0f}s, {len(b)} processes; ' +
          ', '.join(f'{k} {100 * v / TICK / dt:.1f}%' for k, v in per.most_common() if v))


main()
