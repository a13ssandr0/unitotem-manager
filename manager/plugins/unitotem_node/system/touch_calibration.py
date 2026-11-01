"""
Guided setup of a touch panel: what the visitor's taps tell about how the panel sits on its screen.

Pure functions and one small state machine, no hardware and no compositor in here (the service that reads the raw events and draws the targets is
touch_setup.py). Two methods, in order of effort:

* corners: the visitor touches the four corners of the picture, in order, without any precision. The panel is one of the eight ways a rectangle can
  be turned and mirrored; the one under which the four taps land in the four corners they were asked in is the answer (no distances: quadrants).
* points: the visitor touches five targets at known places (the four corners a little inside, and the centre). The full affine matrix that carries the
  raw positions to the targets is solved by least squares (a panel that is scaled, offset or slightly rotated, like a touch frame on a whiteboard);
  it is refused when the taps do not fit it.

Frames. Raw positions are the panel's own, normalised to 0..1 by its reported range. The targets are drawn on the picture the visitor sees, i.e. AFTER
the output's turn and mirror, which the compositor applies to a mapped panel by itself (touch.py): picture = T(panel_matrix(raw)). So the setup solves the panel
matrix against T^-1 of the targets. `Affine` is (a, b, c, d, e, f) with x' = a x + b y + c, y' = d x + e y + f, the order libinput's calibration matrix uses.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from . import touch
from .screens import wayland

Point = tuple[float, float]
Affine = tuple[float, float, float, float, float, float]

IDENTITY: Affine = (1, 0, 0, 0, 1, 0)
# Where the targets are on the picture (fractions of its width and height). A little inside, so a finger on the glass's rim still counts.
CORNER_TARGETS: list[Point] = [(0.12, 0.12), (0.88, 0.12), (0.88, 0.88), (0.12, 0.88)]
POINT_TARGETS: list[Point] = [*CORNER_TARGETS, (0.5, 0.5)]
MAX_RESIDUAL = 0.08         # a tap further than 8 % of the picture from where the best fit puts it means the visitor touched something else
MIN_SPREAD = 0.15           # the raw taps must cover this much of the panel in both directions, or no matrix can be told from them
STEP_TIMEOUT = 60.0         # without a touch for this long the setup ends by itself


# ── affine matrices ───────────────────────────────────────────────────────────

def apply(m: Affine, p: Point) -> Point:
    return (m[0] * p[0] + m[1] * p[1] + m[2], m[3] * p[0] + m[4] * p[1] + m[5])


def invert(m: Affine) -> Affine:
    a, b, c, d, e, f = m
    det = a * e - b * d
    if abs(det) < 1e-9:
        raise ValueError('singular matrix')
    return (e / det, -b / det, (b * f - c * e) / det, -d / det, a / det, (c * d - a * f) / det)


def output_matrix(transform: str) -> Affine:
    """What an output's transform does to a point of the unturned picture (normalised): the matrix of the equivalent window orientation, whose
    values were checked against the compositor on real boards (touch.CALIBRATION)."""
    for (turn, flip), name in wayland.CONTENT_TO_TRANSFORM.items():
        if name == transform:
            return touch.CALIBRATION[(turn, flip)]
    return IDENTITY


def orientation_of(matrix: Affine) -> Optional[tuple[int, int]]:
    """The (turn, flip) a panel matrix stands for, when it is one of the eight turns and mirrors (None for any other matrix)."""
    for pair, m in touch.CALIBRATION.items():
        if all(abs(x - y) < 1e-6 for x, y in zip(m, matrix)):
            return pair
    return None


# ── what the taps say ─────────────────────────────────────────────────────────

class SetupError(Exception):
    """A setup that cannot be concluded; the text says what to do, in one sentence, for the visitor."""


def _quadrant(p: Point) -> tuple[bool, bool]:
    return (p[0] >= 0.5, p[1] >= 0.5)


def corners_orientation(raw: list[Point], transform: str = 'normal') -> tuple[int, int]:
    """(turn, flip) of the panel from the four taps on the corners of the picture, asked in the order top-left, top-right, bottom-right, bottom-left."""
    if len(raw) != len(CORNER_TARGETS):
        raise SetupError('Four touches are needed.')
    wanted = [apply(invert(output_matrix(transform)), t) for t in CORNER_TARGETS]
    expected = [_quadrant(p) for p in wanted]
    best = []
    for pair, matrix in touch.CALIBRATION.items():
        if [_quadrant(apply(matrix, p)) for p in raw] == expected:
            best.append((pair, matrix))
    # the different pairs that are the same matrix (a mirror turned twice is the other mirror) are one answer
    found = {tuple(round(v, 6) for v in matrix) for _, matrix in best}
    if len(found) != 1:
        raise SetupError('The corners were touched in the wrong order, or not in the corners. Try again, starting from the top-left corner.')
    return orientation_of(best[0][1]) or best[0][0]


def fit_affine(raw: list[Point], targets: list[Point]) -> tuple[Affine, float]:
    """The matrix that carries `raw` closest to `targets` (least squares) and the largest distance left over. Needs three taps that are not on a line."""
    n = len(raw)
    if n < 3 or n != len(targets):
        raise SetupError('Not enough touches.')
    # normal equations of [x y 1] . [a b c] = target x (and the same for y): the same 3x3 system, two right-hand sides
    s = [[sum(p[i] * p[j] for p in [(x, y, 1.0) for x, y in raw]) for j in range(3)] for i in range(3)]
    rhs = [[sum((x, y, 1.0)[i] * t[k] for (x, y), t in zip(raw, targets)) for i in range(3)] for k in range(2)]
    solved = [_solve3(s, r) for r in rhs]
    matrix = (*solved[0], *solved[1])
    residual = max(_distance(apply(matrix, p), t) for p, t in zip(raw, targets))
    return tuple(round(v, 6) for v in matrix), residual


def _solve3(m: list[list[float]], r: list[float]) -> list[float]:
    """x of m . x = r for a 3x3 system (Cramer): the matrices here are tiny and a library would be a dependency for nothing."""
    def det(a):
        return (a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1]) - a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0])
                + a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0]))
    d = det(m)
    if abs(d) < 1e-9:
        raise SetupError('The touches are too close to a line. Try again and touch each target where it is.')
    out = []
    for col in range(3):
        replaced = [[r[i] if j == col else m[i][j] for j in range(3)] for i in range(3)]
        out.append(det(replaced) / d)
    return out


def _distance(a: Point, b: Point) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def points_matrix(raw: list[Point], transform: str = 'normal') -> Affine:
    """The panel matrix from the five taps on the targets, or SetupError when they do not fit one."""
    if len(raw) != len(POINT_TARGETS):
        raise SetupError('Five touches are needed.')
    xs, ys = [p[0] for p in raw], [p[1] for p in raw]
    if max(xs) - min(xs) < MIN_SPREAD or max(ys) - min(ys) < MIN_SPREAD:
        raise SetupError('The touches were all in the same place. Try again and touch each target where it is.')
    inverse = invert(output_matrix(transform))
    matrix, residual = fit_affine(raw, [apply(inverse, t) for t in POINT_TARGETS])
    if residual > MAX_RESIDUAL:
        raise SetupError('The touches do not fit one panel: some target was missed. Try again and touch the middle of each circle.')
    return matrix


# ── the session ───────────────────────────────────────────────────────────────

@dataclass
class Session:
    """One run of the guided setup of one panel: which target is asked now, the raw taps so far, and the result. The caller feeds it taps
    (`tap`) and reads `state()`; nothing here touches a device, a file or a screen."""
    method: str                                     # 'corners' | 'points'
    key: str                                        # identity of the panel
    output: str                                     # the output the picture is on
    transform: str = 'normal'                       # that output's transform now
    taps: list[Point] = field(default_factory=list)
    phase: str = 'running'                          # running | try | error | cancelled | saved
    message: str = ''
    result: Optional[dict] = None                   # {'turn','flip'} or {'matrix': [...]}: what "Yes, keep it" saves
    last_touch: float = field(default_factory=time.monotonic)

    @property
    def targets(self) -> list[Point]:
        return CORNER_TARGETS if self.method == 'corners' else POINT_TARGETS

    @property
    def step(self) -> int:
        return len(self.taps)

    def tap(self, point: Point) -> str:
        """Takes one tap (normalised raw). Returns the phase after it."""
        self.last_touch = time.monotonic()
        if self.phase != 'running':
            return self.phase
        self.taps.append(point)
        if len(self.taps) < len(self.targets):
            return self.phase
        try:
            if self.method == 'corners':
                turn, flip = corners_orientation(self.taps, self.transform)
                self.result = {'turn': turn, 'flip': flip}
            else:
                self.result = {'matrix': list(points_matrix(self.taps, self.transform))}
            self.phase, self.message = 'try', ''
        except SetupError as exc:
            self.phase, self.message, self.result = 'error', str(exc), None
        return self.phase

    def expired(self, now: Optional[float] = None) -> bool:
        return (now if now is not None else time.monotonic()) - self.last_touch > STEP_TIMEOUT

    def matrix(self) -> Optional[Affine]:
        """The panel matrix of the result (what the compositor is given while the visitor tries it)."""
        if not self.result:
            return None
        if 'matrix' in self.result:
            return tuple(self.result['matrix'])
        return touch.CALIBRATION[(self.result['turn'], self.result['flip'])]

    def state(self) -> dict:
        """What the web page and the totem's overlay are told."""
        spot = self.targets[self.step] if self.phase == 'running' and self.step < len(self.targets) else None
        # `spot`, not `target`: that is the name the websocket protocol gives the message's own destination
        return {'phase': self.phase, 'method': self.method, 'key': self.key, 'output': self.output, 'step': self.step,
                'total': len(self.targets), 'spot': {'x': spot[0], 'y': spot[1]} if spot else None, 'message': self.message}
