"""Detect the local graphics stack and pick the Chromium switches that suit it.

Chromium leaves video-decode acceleration off by default on Linux, and the
switches that turn it on are not universally safe: asking for a decoder that
cannot initialise is worse than not asking, because Chromium can then abandon
acceleration altogether instead of falling back cleanly. So the viewer cannot
simply always pass them - it has to look at what the machine actually is.

This module is that look. It reports a `GpuProfile` describing the local
graphics stack and the extra `cef.Initialize(switches=...)` entries that suit
it. `manager/webview/app.py` merges those switches into the ones it already
sets; everything else here is diagnostics, surfaced next to
Settings/Display/getGPUFeatureStats so an operator can see which profile was
chosen and why.

Design rules, in order of importance:

- **An unknown machine gets no extra switches.** `PROFILES['unknown']` is
  empty on purpose: on hardware nobody has measured, today's behaviour is the
  behaviour we keep. A profile has to be earned by evidence, not guessed from
  a PCI ID.
- **Detection is filesystem-only.** No subprocesses, no `vainfo`, no libva
  calls - this runs during viewer startup on a read-only kiosk image where
  those may not exist. Everything comes from `/sys`, `/dev` and the presence
  of driver `.so` files, which is what libva itself keys on.
- **Profiles are data**, not branches, so adding hardware is a table entry and
  the table can be read by someone who is not going to read the code.

What has actually been measured, and what has not, is recorded per profile in
`VERIFIED` - do not silently promote an entry.
"""

__all__ = [
    "GpuProfile",
    "detect_gpu",
    "PROFILES",
    "VERIFIED",
]

import os
from dataclasses import dataclass, field
from glob import glob
from typing import Dict, List, Optional

from loguru import logger

# Where libva looks for its drivers. A driver is usable only if the matching
# <name>_drv_video.so is actually installed, which is exactly the check libva
# performs before it will hand Chromium a VA display.
_VA_DRIVER_DIRS = (
    "/usr/lib/x86_64-linux-gnu/dri",
    "/usr/lib/aarch64-linux-gnu/dri",
    "/usr/lib/dri",
)

# DRM driver name (as it appears in /sys/class/drm/card*/device/driver) ->
# the libva driver that serves it. Only entries whose VA-API path Chromium is
# willing to use belong here; see NVIDIA below for a counter-example.
_DRM_TO_VA_DRIVER = {
    "i915": ("iHD", "i965"),
    "xe": ("iHD",),
    "amdgpu": ("radeonsi",),
    "radeon": ("radeonsi",),
}

# Chromium refuses VA-API on NVIDIA regardless of what the system offers.
# media/gpu/vaapi/vaapi_wrapper.cc skips any DRM device whose driver reports
# "nvidia-drm", gated on the VaapiOnNvidiaGPUs feature which is disabled by
# default, with the comment "their VA-API drivers do not support Chromium and
# can sometimes cause crashes (crbug.com/1492880)". Verified on this build:
# forcing the feature on gets past the skip and then fails in the driver
# (`nvidia_drv_video.so init failed`, CUDA initialization error), so there is
# nothing to enable. NVIDIA therefore gets compositing flags only.
_NVIDIA_DRM_NAMES = ("nvidia", "nvidia-drm")

# Software rasterisers. There is no hardware decoder behind these, and asking
# for one is the exact mistake this module exists to avoid.
#
# These are matched against what /sys/class/drm/card*/device/driver actually
# reports, which is the driver bound to the DRM device's *parent* - so a
# virtio-gpu card reads "virtio-pci", not "virtio_gpu". Both spellings are
# listed rather than trying to resolve the real DRM driver name, which only
# lives in debugfs (absent under the proprietary NVIDIA driver, and not
# guaranteed to be mounted anywhere).
_SOFTWARE_DRM_NAMES = ("virtio_gpu", "virtio-pci", "virtio", "vmwgfx", "qxl",
                       "bochs-drm", "bochs", "simpledrm", "vkms", "cirrus",
                       "vboxvideo", "hyperv_drm")


@dataclass
class GpuProfile:
    """What the local graphics stack is, and what to tell Chromium about it."""

    name: str
    """Key into PROFILES: the profile that was selected."""

    reason: str
    """Human-readable justification, logged and shown in the web UI."""

    drm_drivers: List[str] = field(default_factory=list)
    """DRM kernel drivers bound to the local cards, e.g. ['i915']."""

    va_drivers: List[str] = field(default_factory=list)
    """libva drivers actually installed and usable for those cards."""

    v4l2_decoders: List[str] = field(default_factory=list)
    """/dev/video* nodes that look like stateless/stateful video decoders."""

    switches: Dict[str, str] = field(default_factory=dict)
    """Extra cef.Initialize(switches=...) entries for this machine."""

    verified: bool = False
    """True only where the profile has been measured on real hardware."""

    def as_dict(self) -> dict:
        return {
            "profile": self.name,
            "reason": self.reason,
            "drm_drivers": self.drm_drivers,
            "va_drivers": self.va_drivers,
            "v4l2_decoders": self.v4l2_decoders,
            "switches": self.switches,
            "verified": self.verified,
        }


# --------------------------------------------------------------------------
# The table. Feature names are spelled as Chromium 147 spells them; a
# misspelled feature is ignored in silence, which is indistinguishable from
# acceleration being unavailable, so they were read out of libcef.so rather
# than taken from documentation:
#   AcceleratedVideoDecodeLinuxGL          present
#   AcceleratedVideoDecodeLinuxZeroCopyGL  present
#   VaapiVideoDecoder                      present
#   VaapiVideoDecodeLinuxGL                absent - deliberately not requested
# --------------------------------------------------------------------------
_VAAPI_FEATURES = (
    "AcceleratedVideoDecodeLinuxGL,"
    "AcceleratedVideoDecodeLinuxZeroCopyGL,"
    "VaapiVideoDecoder"
)

PROFILES: Dict[str, Dict[str, str]] = {
    # Nothing at all: the machine has not been characterised, so it keeps
    # exactly the behaviour it has today. This is the default and it is
    # deliberately empty.
    "unknown": {},

    # Software rasterisation (llvmpipe behind virtio-gpu, VMware, QXL...).
    # There is no decoder to reach; extra switches would only add risk.
    "software": {},

    # NVIDIA proprietary or nouveau. Chromium will not use VA-API here (see
    # _NVIDIA_DRM_NAMES), so only the blocklist override is worth setting -
    # it is what lets compositing and rasterisation run on the GPU on driver
    # combinations Chromium ships as blocked.
    "nvidia": {
        "ignore-gpu-blocklist": "",
    },

    # Intel and AMD with a libva driver installed: the case Chromium's Linux
    # VA-API path is actually written for.
    "vaapi": {
        "ignore-gpu-blocklist": "",
        "enable-features": _VAAPI_FEATURES,
    },

    # V4L2 stateless/stateful decoders (Raspberry Pi and friends). No VA-API
    # driver is involved, and asking for one risks Chromium giving up on the
    # V4L2 path that does work.
    "v4l2": {
        "ignore-gpu-blocklist": "",
        "enable-features": "AcceleratedVideoDecodeLinuxGL,"
                           "AcceleratedVideoDecodeLinuxZeroCopyGL",
    },
}

# Which profiles have been measured on real hardware, and which are reasoned
# from the Chromium source only. Keep this honest: it is the difference
# between a result and a hope.
VERIFIED: Dict[str, bool] = {
    "unknown": True,    # trivially - it changes nothing
    "software": True,   # measured on virtio-gpu/llvmpipe in the test VM
    "nvidia": True,     # measured on a passed-through GTX 1060, driver 535
    "vaapi": False,     # no Intel/AMD machine available to test on
    "v4l2": False,      # no Raspberry Pi available to test on
}


def _read_drm_drivers() -> List[str]:
    """Kernel DRM drivers bound to the local cards, deduplicated."""
    names = []
    for card in sorted(glob("/sys/class/drm/card[0-9]*")):
        # .../card0/device/driver is a symlink whose basename is the driver.
        link = os.path.join(card, "device", "driver")
        try:
            name = os.path.basename(os.path.realpath(link))
        except OSError:
            continue
        if name and name not in names and os.path.exists(link):
            names.append(name)
    # The proprietary NVIDIA driver does not always present a card*/device/
    # driver link, but it always presents this.
    if os.path.exists("/proc/driver/nvidia/version") and "nvidia" not in names:
        names.append("nvidia")
    return names


def _installed_va_drivers() -> List[str]:
    """libva drivers present on disk, by their short name ('iHD', 'radeonsi')."""
    found = []
    for d in _VA_DRIVER_DIRS:
        for so in glob(os.path.join(d, "*_drv_video.so")):
            name = os.path.basename(so)[: -len("_drv_video.so")]
            if name not in found:
                found.append(name)
    return found


def _v4l2_decoders() -> List[str]:
    """/dev/video* nodes whose driver looks like a video decoder.

    Read from sysfs rather than by opening the device: opening a V4L2 node has
    side effects, and the viewer must not disturb a decoder it is about to use.
    """
    out = []
    for dev in sorted(glob("/sys/class/video4linux/video*")):
        try:
            with open(os.path.join(dev, "name")) as f:
                name = f.read().strip()
        except OSError:
            continue
        low = name.lower()
        if "decode" in low or "-dec" in low or low.endswith("dec"):
            out.append(f"/dev/{os.path.basename(dev)} ({name})")
    return out


def detect_gpu() -> GpuProfile:
    """Work out which profile this machine belongs to.

    Never raises: a detection failure degrades to the 'unknown' profile, which
    changes nothing about how the viewer starts.
    """
    try:
        drm = _read_drm_drivers()
        va = _installed_va_drivers()
        v4l2 = _v4l2_decoders()
    except Exception as exc:  # noqa: BLE001 - detection must never break startup
        logger.warning(f"GPU detection failed ({exc}); using the 'unknown' "
                       f"profile, which adds no Chromium switches")
        return _make("unknown", "detection failed")

    if any(d in _NVIDIA_DRM_NAMES for d in drm):
        return _make("nvidia",
                     "NVIDIA driver bound; Chromium does not use VA-API on "
                     "NVIDIA (vaapi_wrapper.cc skips nvidia-drm devices)",
                     drm, va, v4l2)

    # VA-API only counts when the *matching* driver is installed: a machine
    # with an Intel GPU but no iHD/i965 package has no VA-API, and libva will
    # fail exactly the way Chromium copes with worst.
    for d in drm:
        wanted = _DRM_TO_VA_DRIVER.get(d)
        if wanted and any(w in va for w in wanted):
            return _make("vaapi",
                         f"{d} with libva driver "
                         f"{next(w for w in wanted if w in va)} installed",
                         drm, va, v4l2)

    if v4l2:
        return _make("v4l2",
                     f"V4L2 decoder present ({v4l2[0]}) and no VA-API driver",
                     drm, va, v4l2)

    if drm and all(d in _SOFTWARE_DRM_NAMES for d in drm):
        return _make("software",
                     f"software rasterisation only ({', '.join(drm)})",
                     drm, va, v4l2)

    return _make("unknown",
                 f"no profile matches (drm={drm or 'none'}, va={va or 'none'})",
                 drm, va, v4l2)


def _make(name: str, reason: str, drm: Optional[List[str]] = None,
          va: Optional[List[str]] = None,
          v4l2: Optional[List[str]] = None) -> GpuProfile:
    return GpuProfile(
        name=name,
        reason=reason,
        drm_drivers=drm or [],
        va_drivers=va or [],
        v4l2_decoders=v4l2 or [],
        switches=dict(PROFILES[name]),
        verified=VERIFIED[name],
    )
