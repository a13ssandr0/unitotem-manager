#!/usr/bin/env python3
"""List every line a CEF patch series adds or removes that is NOT inside a `BUILDFLAG(IS_OZONE)` conditional, with the enclosing
preprocessor conditions. Windows and macOS are not built here, so this is the review aid for "the series changes nothing on them": each listed
line must be a comment, an include, a declaration that is itself under `CEF_API_ADDED(CEF_EXPERIMENTAL)`, or something else with an argued
effect (the output is the argument, see docs/cef-wayland/pr-cef.md).
usage: list-nonozone-changes.py CEF_DIR BASE_COMMIT      (CEF_DIR is a git checkout with the series applied on top of BASE_COMMIT)"""
import re, subprocess, sys
cef, base = sys.argv[1], sys.argv[2]
def git(*a): return subprocess.check_output(['git', '-C', cef] + list(a), text=True)
def stacks(text):
    """Per line: tuple of the enclosing #if conditions (innermost last); an #else/#elif negates/replaces the last one."""
    st, out = [], []
    for line in text.splitlines():
        m = re.match(r'\s*#\s*(if|ifdef|ifndef|elif|else|endif)\b(.*)', line)
        if m:
            k, rest = m.group(1), m.group(2).strip()
            if k in ('if', 'ifdef', 'ifndef'): st.append(('' if k == 'if' else k + ' ') + rest)
            elif k == 'elif': st[-1] = rest
            elif k == 'else': st[-1] = '!(' + st[-1] + ')' if not st[-1].startswith('!(') else st[-1]
            elif k == 'endif': out.append(tuple(st)); st.pop(); continue
        out.append(tuple(st))
    return out
def ozone_only(stack):
    return any('IS_OZONE' in c and not c.startswith('!') for c in stack) or any('CEF_API_ADDED' in c and not c.startswith('!') for c in stack)
files = git('diff', '--name-only', base + '..HEAD').split()
total = 0
for f in files:
    if not re.search(r'\.(cc|h|mm)$', f): continue
    new = git('show', 'HEAD:' + f); old = git('show', base + ':' + f) if f in git('ls-tree', '-r', '--name-only', base, f) else ''
    ns, os_ = stacks(new), stacks(old) if old else []
    diff = git('diff', '-U0', base + '..HEAD', '--', f)
    ol = nl = 0
    for line in diff.splitlines():
        m = re.match(r'@@ -(\d+)(?:,\d+)? \+(\d+)', line)
        if m: ol, nl = int(m.group(1)), int(m.group(2)); continue
        if line.startswith('+++') or line.startswith('---'): continue
        if line.startswith('+'):
            stk = ns[nl - 1] if nl - 1 < len(ns) else (); nl += 1
            if not ozone_only(stk) and line[1:].strip(): total += 1; print('%s:%d  +  %-70s  %s' % (f, nl - 1, line[1:].strip()[:70], list(stk)))
        elif line.startswith('-'):
            stk = os_[ol - 1] if ol - 1 < len(os_) else (); ol += 1
            if not ozone_only(stk) and line[1:].strip(): total += 1; print('%s:%d  -  %-70s  %s' % (f, ol - 1, line[1:].strip()[:70], list(stk)))
print('lines outside IS_OZONE / CEF_API_ADDED:', total)
