"""Generate stand-in anime art (spiky-haired fighters, creatures, spinning
tops and painted backgrounds) for anything the script doesn't supply art
for, so every episode builds and renders even before real art exists.

Drawing uses numpy, which ships with Blender.
"""

import math

import bpy
import numpy as np

W, H = 384, 640
OUTLINE = np.array([0.05, 0.04, 0.07])


def _grid(w, h):
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    return x / w, 1.0 - y / h      # u right, v up


def _ellipse(u, v, cx, cy, rx, ry):
    return ((u - cx) / rx) ** 2 + ((v - cy) / ry) ** 2 <= 1.0


def _tri(u, v, p0, p1, p2):
    pts = (u, v)
    d1 = (pts[0] - p1[0]) * (p0[1] - p1[1]) - (p0[0] - p1[0]) * (pts[1] - p1[1])
    d2 = (pts[0] - p2[0]) * (p1[1] - p2[1]) - (p1[0] - p2[0]) * (pts[1] - p2[1])
    d3 = (pts[0] - p0[0]) * (p2[1] - p0[1]) - (p2[0] - p0[0]) * (pts[1] - p0[1])
    neg = (d1 < 0) | (d2 < 0) | (d3 < 0)
    pos = (d1 > 0) | (d2 > 0) | (d3 > 0)
    return ~(neg & pos)


def _rect(u, v, x0, y0, x1, y1):
    return (u >= x0) & (u <= x1) & (v >= y0) & (v <= y1)


def _line(u, v, p0, p1, width):
    px, py = u - p0[0], v - p0[1]
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    L = dx * dx + dy * dy
    t = np.clip((px * dx + py * dy) / L, 0, 1)
    return (px - t * dx) ** 2 + (py - t * dy) ** 2 <= width * width


def _outline(mask, px=4):
    out = mask.copy()
    for dx in range(-px, px + 1, 2):
        for dy in range(-px, px + 1, 2):
            if dx * dx + dy * dy <= px * px:
                out |= np.roll(np.roll(mask, dx, axis=1), dy, axis=0)
    return out


class Canvas:
    def __init__(self, w=W, h=H):
        self.w, self.h = w, h
        self.rgb = np.zeros((h, w, 3), np.float32)
        self.a = np.zeros((h, w), np.float32)
        self.u, self.v = _grid(w, h)

    def paint(self, mask, color, outline=True):
        if outline:
            ol = _outline(mask, max(2, self.w // 110))
            self.rgb[ol & ~mask] = OUTLINE
            self.a[ol] = 1.0
        self.rgb[mask] = color
        self.a[mask] = 1.0

    def ink(self, mask, color=OUTLINE):
        self.rgb[mask] = color
        self.a[mask] = 1.0

    def to_image(self, name):
        img = bpy.data.images.get(name)
        if img:
            bpy.data.images.remove(img)
        img = bpy.data.images.new(name, self.w, self.h, alpha=True)
        px = np.concatenate([self.rgb, self.a[..., None]], axis=2)
        px = np.flipud(px)               # Blender stores rows bottom-up
        img.pixels.foreach_set(px.ravel())
        img.pack()
        img["as_generated"] = True
        return img


def _shade(c, k):
    return np.clip(np.array(c) * k, 0, 1)


def fighter(name, color, expr="neutral", talk=False, blink=False, form="", seed=0, style="spiky"):
    """Fighter in a gi: spiky hair, or long hair when style='long'."""
    rng = np.random.default_rng(seed)
    cv = Canvas()
    u, v = cv.u, cv.v
    gi = np.array(color[:3])
    hair = _shade(gi, 0.35) + 0.05 if not form else np.array([1.0, 0.86, 0.2])
    skin = np.array([1.0, 0.84, 0.70])
    long_hair = style == "long"
    if long_hair:
        hair = np.array([0.95, 0.45, 0.6]) * 0.75 if not form else hair
        back = _ellipse(u, v, 0.5, 0.74, 0.215, 0.15) | _rect(u, v, 0.29, 0.5, 0.71, 0.75)
        cv.paint(back, _shade(hair, 0.85))

    # Legs, body, belt, arms.
    legs = _rect(u, v, 0.33, 0.02, 0.47, 0.32) | _rect(u, v, 0.53, 0.02, 0.67, 0.32)
    cv.paint(legs, _shade(gi, 0.8))
    boots = _rect(u, v, 0.31, 0.0, 0.49, 0.07) | _rect(u, v, 0.51, 0.0, 0.69, 0.07)
    cv.paint(boots, np.array([0.15, 0.2, 0.45]))
    torso = _ellipse(u, v, 0.5, 0.47, 0.21, 0.18) | _rect(u, v, 0.30, 0.30, 0.70, 0.5)
    cv.paint(torso, gi)
    cv.paint(_rect(u, v, 0.30, 0.31, 0.70, 0.355), np.array([0.15, 0.2, 0.5]), outline=False)
    collar = _tri(u, v, (0.42, 0.62), (0.58, 0.62), (0.5, 0.5))
    cv.paint(collar, np.array([0.2, 0.3, 0.7]), outline=False)
    arm_up = expr in ("shout", "angry")
    if arm_up:
        arms = _line(u, v, (0.27, 0.58), (0.16, 0.42), 0.055) | _line(u, v, (0.73, 0.58), (0.84, 0.42), 0.055)
        fists = _ellipse(u, v, 0.15, 0.40, 0.055, 0.035) | _ellipse(u, v, 0.85, 0.40, 0.055, 0.035)
    else:
        arms = _line(u, v, (0.27, 0.58), (0.22, 0.36), 0.05) | _line(u, v, (0.73, 0.58), (0.78, 0.36), 0.05)
        fists = _ellipse(u, v, 0.215, 0.34, 0.05, 0.032) | _ellipse(u, v, 0.785, 0.34, 0.05, 0.032)
    cv.paint(arms, gi)
    cv.paint(fists, skin)

    # Head and spiky hair.
    head = _ellipse(u, v, 0.5, 0.74, 0.17, 0.115)
    neck = _rect(u, v, 0.45, 0.6, 0.55, 0.66)
    cv.paint(neck, _shade(skin, 0.9))
    spikes = np.zeros_like(head)
    n = 0 if long_hair else 7
    for i in range(n):
        a = math.pi * (0.05 + 0.9 * i / (n - 1))
        L = 0.17 + 0.05 * rng.random() + (0.06 if form else 0.0)
        tip = (0.5 + math.cos(a) * L * 1.3, 0.82 + math.sin(a) * L)
        base_l = (0.5 + math.cos(a - 0.35) * 0.12, 0.8 + math.sin(a - 0.35) * 0.07)
        base_r = (0.5 + math.cos(a + 0.35) * 0.12, 0.8 + math.sin(a + 0.35) * 0.07)
        spikes |= _tri(u, v, tip, base_l, base_r)
    hair_mask = spikes | _ellipse(u, v, 0.5, 0.81, 0.18, 0.075)
    cv.paint(hair_mask, hair)
    cv.paint(head & ~_ellipse(u, v, 0.5, 0.84, 0.2, 0.05), skin)
    bang = _tri(u, v, (0.36, 0.8), (0.47, 0.8), (0.40, 0.73)) | _tri(u, v, (0.52, 0.8), (0.64, 0.8), (0.6, 0.72))
    if long_hair:
        bang |= _tri(u, v, (0.44, 0.82), (0.56, 0.82), (0.5, 0.75))
        locks = _tri(u, v, (0.31, 0.8), (0.37, 0.8), (0.3, 0.6)) | _tri(u, v, (0.63, 0.8), (0.69, 0.8), (0.7, 0.6))
        cv.paint(locks, hair)
    cv.paint(bang, hair, outline=False)

    # Face: eyes, brows, mouth by expression.
    ey = 0.735
    for ex in (0.44, 0.56):
        if blink or expr == "happy":
            cv.ink(_line(u, v, (ex - 0.035, ey), (ex + 0.035, ey + (0.008 if expr == "happy" else 0)), 0.006))
        else:
            ry = 0.032 if expr in ("surprised", "shout") else 0.026
            cv.ink(_ellipse(u, v, ex, ey, 0.032, ry + 0.004))
            cv.paint(_ellipse(u, v, ex, ey, 0.028, ry), np.array([1, 1, 1]), outline=False)
            pupil = 0.011 if expr == "surprised" else 0.016
            cv.ink(_ellipse(u, v, ex + 0.006, ey - 0.003, pupil, pupil * 1.4), np.array([0.1, 0.12, 0.2]))
            cv.paint(_ellipse(u, v, ex + 0.012, ey + 0.008, 0.006, 0.006), np.array([1, 1, 1]), outline=False)
            if long_hair:
                cv.ink(_line(u, v, (ex - 0.036, ey + ry * 0.6), (ex + 0.036, ey + ry * 0.9), 0.006))
        tilt = {"angry": -0.022, "shout": -0.02, "sad": 0.018, "surprised": 0.0, "smirk": -0.01}.get(expr, -0.006)
        inner = ex + (0.03 if ex < 0.5 else -0.03)
        outer = ex - (0.035 if ex < 0.5 else -0.035)
        lift = 0.012 if expr == "surprised" else 0.0
        cv.ink(_line(u, v, (outer, ey + 0.045 + lift), (inner, ey + 0.045 + tilt + lift), 0.006))
    my = 0.675
    open_mouth = talk or expr in ("shout", "surprised")
    if open_mouth:
        ry = 0.03 if expr == "shout" else (0.022 if talk else 0.015)
        rx = 0.05 if expr == "shout" else 0.03
        cv.ink(_ellipse(u, v, 0.5, my, rx, ry))
        cv.paint(_ellipse(u, v, 0.5, my - ry * 0.4, rx * 0.6, ry * 0.45), np.array([0.85, 0.3, 0.3]), outline=False)
    elif expr in ("happy", "smirk"):
        cv.ink(_line(u, v, (0.46, my + 0.008), (0.5, my - 0.006), 0.005) |
               _line(u, v, (0.5, my - 0.006), (0.54, my + (0.008 if expr == "happy" else 0.012)), 0.005))
    elif expr in ("sad", "hurt"):
        cv.ink(_line(u, v, (0.46, my - 0.008), (0.5, my + 0.002), 0.005) |
               _line(u, v, (0.5, my + 0.002), (0.54, my - 0.008), 0.005))
    else:
        cv.ink(_line(u, v, (0.47, my), (0.53, my), 0.005))
    if expr == "hurt":
        cv.ink(_line(u, v, (0.4, 0.71), (0.36, 0.69), 0.004), np.array([0.8, 0.2, 0.2]))
    if expr == "sad":
        cv.paint(_ellipse(u, v, 0.43, 0.705, 0.008, 0.02), np.array([0.6, 0.85, 1.0]), outline=False)
    return cv.to_image(f"AS_{name}_{form + '_' if form else ''}{expr}{'_talk' if talk else ''}{'_blink' if blink else ''}")


def creature(name, color, expr="neutral", talk=False, blink=False, form="", seed=0, style=""):
    """Round pocket-monster style creature with ears and a tail."""
    cv = Canvas(448, 448)
    u, v = cv.u, cv.v
    col = np.array(color[:3])
    ear = _tri(u, v, (0.26, 0.92), (0.2, 0.6), (0.4, 0.66)) | _tri(u, v, (0.74, 0.92), (0.6, 0.66), (0.8, 0.6))
    cv.paint(ear, col)
    cv.paint(_tri(u, v, (0.26, 0.92), (0.23, 0.8), (0.3, 0.82)) | _tri(u, v, (0.74, 0.92), (0.7, 0.82), (0.77, 0.8)),
             OUTLINE, outline=False)
    tail = _tri(u, v, (0.95, 0.55), (0.72, 0.25), (0.8, 0.2))
    cv.paint(tail, _shade(col, 0.85))
    body = _ellipse(u, v, 0.5, 0.42, 0.3, 0.33)
    cv.paint(body, col)
    cv.paint(_ellipse(u, v, 0.5, 0.33, 0.17, 0.17), np.clip(col * 0.4 + 0.6, 0, 1), outline=False)
    feet = _ellipse(u, v, 0.37, 0.08, 0.08, 0.045) | _ellipse(u, v, 0.63, 0.08, 0.08, 0.045)
    cv.paint(feet, _shade(col, 0.8))
    cv.paint(_ellipse(u, v, 0.31, 0.47, 0.05, 0.035) | _ellipse(u, v, 0.69, 0.47, 0.05, 0.035),
             np.array([1.0, 0.35, 0.35]), outline=False)
    for ex in (0.38, 0.62):
        if blink or expr == "happy":
            cv.ink(_line(u, v, (ex - 0.04, 0.56), (ex + 0.04, 0.57), 0.008))
        else:
            r = 0.05 if expr == "surprised" else 0.042
            cv.ink(_ellipse(u, v, ex, 0.56, r * 0.8, r))
            cv.paint(_ellipse(u, v, ex + 0.012, 0.575, 0.014, 0.014), np.array([1, 1, 1]), outline=False)
        if expr in ("angry", "shout"):
            inner = ex + (0.045 if ex < 0.5 else -0.045)
            cv.ink(_line(u, v, (ex - (0.05 if ex < 0.5 else -0.05), 0.64), (inner, 0.615), 0.008))
    if talk or expr in ("shout", "surprised"):
        cv.ink(_ellipse(u, v, 0.5, 0.45, 0.045, 0.04 if expr == "shout" else 0.03))
        cv.paint(_ellipse(u, v, 0.5, 0.44, 0.028, 0.016), np.array([0.9, 0.35, 0.35]), outline=False)
    else:
        cv.ink(_line(u, v, (0.46, 0.47), (0.5, 0.455), 0.006) | _line(u, v, (0.5, 0.455), (0.54, 0.47), 0.006))
    return cv.to_image(f"AS_{name}_{expr}{'_talk' if talk else ''}{'_blink' if blink else ''}")


def spinning_top(name, color, expr="neutral", talk=False, blink=False, form="", seed=0, style=""):
    """Battle top seen from above: ring, blades and a glowing bit."""
    cv = Canvas(448, 448)
    u, v = cv.u, cv.v
    col = np.array(color[:3])
    r = np.sqrt((u - 0.5) ** 2 + (v - 0.5) ** 2)
    ang = np.arctan2(v - 0.5, u - 0.5)
    blades = r < (0.36 + 0.1 * (np.sin(ang * 4) > 0.3))
    cv.paint(blades, _shade(col, 0.7))
    cv.paint(r < 0.3, np.array([0.75, 0.78, 0.85]))
    cv.paint((r < 0.3) & (np.sin(ang * 8) > 0.6), np.array([0.45, 0.48, 0.55]), outline=False)
    cv.paint(r < 0.17, col)
    cv.paint((r < 0.08), np.array([1.0, 1.0, 0.85]), outline=False)
    return cv.to_image(f"AS_{name}_{expr}")


def background(name, time="day", seed=0):
    """Painted-style landscape: sky gradient, mountains, rocky ground."""
    w, h = 1920, 1080
    rng = np.random.default_rng(seed)
    u, v = _grid(w, h)
    palettes = {
        "day": ((0.35, 0.62, 0.95), (0.80, 0.90, 1.0), (0.45, 0.55, 0.75), (0.80, 0.62, 0.40), (0.62, 0.45, 0.28)),
        "sunset": ((0.30, 0.20, 0.50), (1.0, 0.55, 0.30), (0.40, 0.25, 0.40), (0.65, 0.40, 0.30), (0.45, 0.28, 0.22)),
        "night": ((0.02, 0.03, 0.12), (0.12, 0.15, 0.35), (0.08, 0.1, 0.2), (0.22, 0.2, 0.3), (0.15, 0.13, 0.2)),
    }
    top, horizon, mount, ground_hi, ground_lo = [np.array(c) for c in palettes.get(time, palettes["day"])]
    t = np.clip((v - 0.35) / 0.65, 0, 1)[..., None]
    rgb = horizon * (1 - t) + top * t
    # Sun or moon.
    sun = _ellipse(u * 1.777, v, 1.35, 0.78, 0.06, 0.06)
    rgb[sun] = (1.0, 0.97, 0.85) if time != "sunset" else (1.0, 0.8, 0.5)
    if time == "night":
        stars = rng.random((h, w)) > 0.9993
        rgb[stars & (v > 0.4)] = (1, 1, 1)
    # Clouds.
    if time != "night":
        for _ in range(7):
            cx, cy = rng.random() * 1.0, 0.6 + rng.random() * 0.33
            for k in range(4):
                m = _ellipse(u, v, cx + k * 0.035, cy + (k % 2) * 0.012, 0.05, 0.03)
                rgb[m] = rgb[m] * 0.2 + np.array([1, 1, 1]) * 0.8
    # Two mountain ranges.
    for layer, (base, amp, shade) in enumerate(((0.42, 0.16, 1.0), (0.34, 0.1, 0.8))):
        xs = np.linspace(0, 1, 9)
        ys = base + amp * rng.random(9)
        ridge = np.interp(u[0], xs, ys)
        mask = v < ridge[None, :]
        rgb[mask] = mount * shade * (1.0 + 0.15 * layer)
    # Rocky ground with a horizon line and some rocks/cracks.
    g = v < 0.27
    tg = np.clip(v / 0.27, 0, 1)[..., None]
    rgb[g] = (ground_lo * (1 - tg) + ground_hi * tg)[g]
    rgb[np.abs(v - 0.27) < 0.0025] = ground_lo * 0.6
    for _ in range(14):
        cx, cy = rng.random(), rng.random() * 0.22
        s = 0.01 + rng.random() * 0.03
        m = _ellipse(u * 1.777, v, cx * 1.777, cy, s * 1.6, s)
        rgb[m] = ground_lo * 0.75
    cv = Canvas(w, h)
    cv.rgb = rgb.astype(np.float32)
    cv.a[:] = 1.0
    return cv.to_image(f"AS_BG_{name}_{time}")


DRAWERS = {"fighter": fighter, "creature": creature, "object": spinning_top}
EXPRESSIONS = ["neutral", "happy", "angry", "shout", "sad", "surprised", "hurt", "smirk"]
