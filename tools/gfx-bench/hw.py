# Hardware counters sampler for venus (i915 throttle flags, GPU act freq, package temp/power, CPU throttle counters)
import glob, time
def rd(p):
    try: return open(p).read().strip()
    except Exception: return None
def num(p):
    v = rd(p); return int(v) if v and v.lstrip('-').isdigit() else None
G = '/sys/class/drm/card0/gt/gt0/'
FLAGS = ['pl1', 'pl2', 'pl4', 'prochot', 'ratl', 'thermal', 'vr_tdc', 'vr_thermalert']
def snap():
    s = {'t': time.time(), 'freq': num(G + 'rps_act_freq_mhz'), 'temp': (num('/sys/class/thermal/thermal_zone1/temp') or 0) / 1000,
         'uj': num('/sys/class/powercap/intel-rapl:0/energy_uj'),
         'cthr': sum(num(f) or 0 for f in glob.glob('/sys/devices/system/cpu/cpu*/thermal_throttle/core_throttle_count')),
         'pthr': sum(num(f) or 0 for f in glob.glob('/sys/devices/system/cpu/cpu*/thermal_throttle/package_throttle_count'))}
    for f in FLAGS: s[f] = num(G + 'throttle_reason_' + f) or 0
    return s
class Sampler:
    def __init__(self): self.a = snap(); self.w = [self.a]
    def sample(self): self.w.append(snap())
    def report(self):
        a, b, w = self.a, self.w[-1], self.w
        fr = [x['freq'] for x in w if x['freq'] is not None]
        flags = [f for f in FLAGS if any(x[f] for x in w)]
        pw = (b['uj'] - a['uj']) / 1e6 / (b['t'] - a['t']) if a['uj'] is not None and b['uj'] and b['uj'] >= a['uj'] else None
        return 'gt avg/max %d/%d MHz | pkg temp max %.0fC | pkg power %s W | gpu throttle flags seen: %s | cpu throttle +%d core +%d pkg' % (
            sum(fr) / max(len(fr), 1), max(fr or [0]), max(x['temp'] for x in w), '%.0f' % pw if pw else '?', ','.join(flags) or 'none',
            b['cthr'] - a['cthr'], b['pthr'] - a['pthr'])
