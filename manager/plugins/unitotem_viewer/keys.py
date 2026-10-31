"""
Keyboard policy of the viewer windows: a public kiosk may have a keyboard attached, and the browser's own shortcuts (developer
tools, print, save, view source, navigation, new windows) must not work. Text editing keys do, the reload keys are not run by the
browser but sent to the page shown (see `Decision.RELOAD`).

Pure functions on the `KeyEvent` dictionary of the browser (Windows key codes, as in Chromium's keyboard_codes.h).
"""
import enum

SHIFT, CONTROL, ALT = 1 << 1, 1 << 2, 1 << 3      # EVENTFLAG_* values of CEF

VK_BACK, VK_TAB, VK_RETURN, VK_ESCAPE, VK_SPACE = 0x08, 0x09, 0x0D, 0x1B, 0x20
VK_END, VK_HOME, VK_LEFT, VK_UP, VK_RIGHT, VK_DOWN, VK_DELETE = 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2E
VK_F5, VK_R = 0x74, 0x52
EDITING_LETTERS = {ord(c) for c in 'ACVXYZ'}          # select all, copy, paste, cut, redo, undo
EDITING_KEYS = {VK_BACK, VK_DELETE, VK_LEFT, VK_RIGHT, VK_UP, VK_DOWN, VK_HOME, VK_END}


class Decision(enum.Enum):
    PASS = 'pass'          # the page gets the key
    BLOCK = 'block'        # nobody gets it
    RELOAD = 'reload'      # the page shown is asked to reload itself


def decide(modifiers: int, key_code: int) -> Decision:
    """What to do with a key press. Plain keys and Shift combinations are text; function keys and Ctrl/Alt combinations are the
    browser's shortcuts, except the editing ones."""
    if key_code == VK_F5 or (key_code == VK_R and modifiers & CONTROL and not modifiers & ALT):
        return Decision.RELOAD
    if 0x70 <= key_code <= 0x87:                       # F1..F24 (help, find, print preview, developer tools...)
        return Decision.BLOCK
    if modifiers & ALT:
        return Decision.BLOCK                          # Alt+Left/Right history, Alt+Home, menu accelerators
    if modifiers & CONTROL:
        if modifiers & SHIFT and key_code in EDITING_LETTERS - {ord('Z')}:
            return Decision.BLOCK
        if key_code in EDITING_LETTERS or key_code in EDITING_KEYS:
            return Decision.PASS
        return Decision.BLOCK                          # Ctrl+P/S/O/U/L/T/N/W/F/G/J/H/D/+/-/0, Ctrl+Shift+I/J/C...
    return Decision.PASS
