#!/usr/bin/env python3
"""Syntax-only compile (Chromium's clang, the flags of the libcef_static target of an existing Linux out dir) of the touched CEF
translation units WITH and WITHOUT -DUSE_OZONE. Without it BUILDFLAG(IS_OZONE) is 0, which is what Windows and macOS see in shared
code: this checks that code path (types, includes, guards) as far as Linux headers allow. It is NOT a Windows/macOS build: no Windows
SDK/MSVC or Apple SDK is available (gn gen fails with KeyError 'WINDOWSSDKDIR' / mac_sdk.gni).
usage (on the build server, in a tree with out/Release_GN_x64 built): sudo python3 check-nonozone.py ozone|nonozone FILE...
  FILE is relative to chromium/src/cef, e.g. libcef/browser/views/window_impl.cc
"""
import re,subprocess,sys,shlex
O='/nas/cef-build/work/chromium/src/out/Release_GN_x64'
txt=open(O+'/obj/cef/libcef_static.ninja').read()
def var(n):
    m=re.search(r'^%s = (.*)$'%n,txt,re.M); return m.group(1)
defs=[d for d in shlex.split(var('defines')) if d!='-DUSE_OZONE']
inc=shlex.split(var('include_dirs'))
cf=[c for c in shlex.split(var('cflags')) if not c.startswith('-fprofile-use') and c not in('-Wno-profile-instr-unprofiled','-Wno-profile-instr-out-of-date')]
cc=shlex.split(var('cflags_cc'))
# drop module flags
def strip(l):
    out=[];i=0
    while i<len(l):
        if l[i]=='-Xclang' and i+1<len(l) and ('module' in l[i+1] or 'plugin' in l[i+1] or 'Xclang' in l[i+1]):
            i+=2; continue
        if 'module' in l[i] or 'plugin' in l[i]: i+=1; continue
        out.append(l[i]); i+=1
    return out
cf=strip(cf); cc=strip(cc)
clang=O+'/../../third_party/llvm-build/Release+Asserts/bin/clang++'
mode=sys.argv[1]  # nonozone | ozone
if mode=='ozone': defs.append('-DUSE_OZONE')
rc=0
for f in sys.argv[2:]:
    cmd=[clang,'-fsyntax-only','-Wno-unused-command-line-argument']+defs+inc+cf+cc+['../../cef/'+f]
    r=subprocess.run(cmd,cwd=O,capture_output=True,text=True)
    errs=[l for l in r.stderr.splitlines() if 'error' in l]
    print(mode,f,'rc',r.returncode,'errors',len(errs)); [print('  ',l[:230]) for l in errs[:6]]
    rc|=r.returncode
