"""Names of outputs (screens). While the kiosk ran an X server it named them with RandR (`HDMI-1`), the compositor names them
after the DRM connector (`HDMI-A-1`): what was saved under the old name (playlist assignments, screen settings, touch mapping)
still has to find its output."""
import re

_HDMI_A_RE = re.compile(r'^HDMI-A-(\d+)$')
_CARD_PREFIX_RE = re.compile(r'^card\d+-')


def canonical_output_name(name: str) -> str:
    """One name for the same output whatever the source: the `cardN-` prefix of a DRM connector is dropped and
    `HDMI-A-n` is folded into `HDMI-n`. Everything else is left alone."""
    stripped = _CARD_PREFIX_RE.sub('', name)
    m = _HDMI_A_RE.match(stripped)
    return f'HDMI-{m.group(1)}' if m else stripped


def find_renamed(old: str, current: list[str]) -> str | None:
    """The name in `current` that is the same output as `old` under another spelling, if there is exactly one."""
    if old in current:
        return None
    wanted = canonical_output_name(old)
    matches = [name for name in current if canonical_output_name(name) == wanted]
    return matches[0] if len(matches) == 1 else None
