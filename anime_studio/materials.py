"""Flat, unlit anime materials and procedural effect shaders.

Every material is emission based so colours come out exactly as drawn
(with the 'Standard' view transform), the way cel animation looks.
Effect materials expose two keyable Value nodes: "Fade" (0..1 opacity)
and "Time" (drives flicker/noise animation).
"""

import bpy

from . import compat


class NB:
    """Tiny node-building helper."""

    def __init__(self, mat):
        mat.use_nodes = True
        self.nt = mat.node_tree
        self.nt.nodes.clear()
        self.n = self.nt.nodes
        self.l = self.nt.links
        self.x = 0

    def node(self, kind, **props):
        nd = self.n.new(kind)
        nd.location = (self.x, 0)
        self.x += 180
        for k, v in props.items():
            setattr(nd, k, v)
        return nd

    def link(self, a, b):
        self.l.new(a, b)

    def val(self, name, v):
        nd = self.node("ShaderNodeValue")
        nd.name = nd.label = name
        nd.outputs[0].default_value = v
        return nd.outputs[0]

    def _in(self, sock, v):
        if isinstance(v, (int, float)):
            sock.default_value = v
        else:
            self.link(v, sock)

    def math(self, op, a, b=0.0, clamp=False):
        nd = self.node("ShaderNodeMath", operation=op, use_clamp=clamp)
        self._in(nd.inputs[0], a)
        self._in(nd.inputs[1], b)
        return nd.outputs[0]

    def maprange(self, v, a, b, c=0.0, d=1.0, smooth=True):
        nd = self.node("ShaderNodeMapRange", clamp=True)
        if smooth:
            nd.interpolation_type = "SMOOTHSTEP"
        self._in(nd.inputs["Value"], v)
        nd.inputs["From Min"].default_value = a
        nd.inputs["From Max"].default_value = b
        nd.inputs["To Min"].default_value = c
        nd.inputs["To Max"].default_value = d
        return nd.outputs[0]

    def mixcol(self, fac, a, b):
        nd = self.node("ShaderNodeMix", data_type="RGBA", clamp_factor=True)
        self._in(nd.inputs[0], fac)
        for sock, v in ((nd.inputs[6], a), (nd.inputs[7], b)):
            if isinstance(v, (tuple, list)):
                sock.default_value = (*v[:3], 1.0)
            else:
                self.link(v, sock)
        return nd.outputs[2]

    def uv(self):
        tc = self.node("ShaderNodeTexCoord")
        sep = self.node("ShaderNodeSeparateXYZ")
        self.link(tc.outputs["UV"], sep.inputs[0])
        return sep.outputs[0], sep.outputs[1]

    def polar(self, cx=0.5, cy=0.5, sx=1.0, sy=1.0):
        u, v = self.uv()
        dx = self.math("MULTIPLY", self.math("SUBTRACT", u, cx), sx * 2)
        dy = self.math("MULTIPLY", self.math("SUBTRACT", v, cy), sy * 2)
        r = self.math("SQRT", self.math("ADD", self.math("MULTIPLY", dx, dx), self.math("MULTIPLY", dy, dy)))
        ang = self.math("ARCTAN2", dy, dx)
        return r, ang, u, v

    def noise(self, x, y, w=None, scale=1.0, detail=2.0):
        comb = self.node("ShaderNodeCombineXYZ")
        self._in(comb.inputs[0], x)
        self._in(comb.inputs[1], y)
        nz = self.node("ShaderNodeTexNoise", noise_dimensions="4D" if w is not None else "3D")
        nz.inputs["Scale"].default_value = scale
        nz.inputs["Detail"].default_value = detail
        self.link(comb.outputs[0], nz.inputs["Vector"])
        if w is not None:
            self._in(nz.inputs["W"], w)
        return nz.outputs["Fac"]

    def output(self, color, alpha, strength=1.0):
        em = self.node("ShaderNodeEmission")
        em.inputs["Strength"].default_value = strength
        if isinstance(color, (tuple, list)):
            em.inputs["Color"].default_value = (*color[:3], 1.0)
        else:
            self.link(color, em.inputs["Color"])
        tr = self.node("ShaderNodeBsdfTransparent")
        mix = self.node("ShaderNodeMixShader")
        self._in(mix.inputs[0], alpha)
        self.link(tr.outputs[0], mix.inputs[1])
        self.link(em.outputs[0], mix.inputs[2])
        out = self.node("ShaderNodeOutputMaterial")
        self.link(mix.outputs[0], out.inputs["Surface"])


def _new(name, blended=True):
    mat = bpy.data.materials.new(name)
    compat.set_blend_mode(mat, blended)
    mat["as_generated"] = True
    return mat


def image_material(name, image, grid=(1, 1), cell=(0, 0), key_color=None, opaque=False):
    """Unlit image. grid/cell pick one panel of a character sheet."""
    mat = _new(name, blended=not opaque)
    b = NB(mat)
    tex = b.node("ShaderNodeTexImage", image=image, extension="CLIP", interpolation="Cubic")
    cols, rows = grid
    if (cols, rows) != (1, 1):
        tc = b.node("ShaderNodeTexCoord")
        mp = b.node("ShaderNodeMapping", vector_type="POINT")
        mp.inputs["Scale"].default_value = (1.0 / cols, 1.0 / rows, 1.0)
        c, r = cell
        mp.inputs["Location"].default_value = (c / cols, (rows - 1 - r) / rows, 0.0)
        b.link(tc.outputs["UV"], mp.inputs["Vector"])
        b.link(mp.outputs[0], tex.inputs["Vector"])
    alpha = 1.0 if opaque else tex.outputs["Alpha"]
    if key_color is not None and not opaque:
        vm = b.node("ShaderNodeVectorMath", operation="DISTANCE")
        b.link(tex.outputs["Color"], vm.inputs[0])
        vm.inputs[1].default_value = tuple(key_color[:3])
        keyed = b.maprange(vm.outputs["Value"], 0.10, 0.25)
        alpha = b.math("MULTIPLY", tex.outputs["Alpha"], keyed)
    b.output(tex.outputs["Color"], alpha)
    return mat


def flat_material(name, color, alpha=1.0):
    mat = _new(name, blended=alpha < 1.0)
    b = NB(mat)
    b.output(color, alpha)
    return mat


def flash_material(name, color=(1.0, 1.0, 1.0)):
    mat = _new(name)
    b = NB(mat)
    b.output(color, b.val("Fade", 1.0))
    return mat


def _fade_time(b, fade=1.0):
    return b.val("Fade", fade), b.val("Time", 0.0)


def aura_material(name, color):
    mat = _new(name)
    b = NB(mat)
    fade, time = _fade_time(b)
    u, v = b.uv()
    dx = b.math("MULTIPLY", b.math("SUBTRACT", u, 0.5), 2.0)
    dy = b.math("MULTIPLY", b.math("SUBTRACT", v, 0.42), 1.7)
    r = b.math("SQRT", b.math("ADD", b.math("MULTIPLY", dx, dx), b.math("MULTIPLY", dy, dy)))
    flame = b.noise(b.math("MULTIPLY", u, 4.0), b.math("SUBTRACT", b.math("MULTIPLY", v, 2.0),
                                                       b.math("MULTIPLY", time, 1.6)), time, scale=1.6, detail=3.0)
    shape = b.math("ADD", b.math("SUBTRACT", 1.0, r), b.math("MULTIPLY", b.math("SUBTRACT", flame, 0.5), 1.4))
    a = b.maprange(shape, 0.05, 0.45)
    core = b.maprange(shape, 0.45, 0.9)
    col = b.mixcol(core, color, (1.0, 1.0, 0.95))
    b.output(col, b.math("MULTIPLY", a, b.math("MULTIPLY", fade, 0.85)), strength=1.0)
    return mat


def speedlines_material(name, color, aspect=16 / 9):
    mat = _new(name)
    b = NB(mat)
    fade, time = _fade_time(b)
    r, ang, u, v = b.polar(sx=aspect / 1.0, sy=1.0)
    stripes = b.noise(b.math("MULTIPLY", ang, 30.0), b.math("MULTIPLY", time, 3.0), None, scale=1.0, detail=0.0)
    lines = b.maprange(stripes, 0.56, 0.62)
    mask = b.maprange(r, 0.55, 1.25)
    b.output(color, b.math("MULTIPLY", b.math("MULTIPLY", lines, mask), fade))
    return mat


def burst_material(name, color):
    mat = _new(name)
    b = NB(mat)
    fade, time = _fade_time(b)
    r, ang, u, v = b.polar(sy=0.6)
    rays = b.math("SINE", b.math("ADD", b.math("MULTIPLY", ang, 14.0), b.math("MULTIPLY", time, 0.6)))
    bands = b.maprange(rays, -0.1, 0.1)
    dark = tuple(c * 0.35 for c in color)
    col = b.mixcol(bands, dark, tuple(min(1.0, c * 0.9 + 0.1) for c in color))
    glow = b.maprange(r, 0.6, 0.0)
    col = b.mixcol(glow, col, (1.0, 1.0, 0.92))
    b.output(col, fade)
    return mat


def impact_material(name, color, seed=0.0):
    """Sharp anime impact star: two overlapping rings of thin spikes."""
    mat = _new(name)
    b = NB(mat)
    fade, time = _fade_time(b)
    r, ang, u, v = b.polar()

    def spikes(n, phase, power):
        return b.math("POWER", b.math("ABSOLUTE", b.math("COSINE", b.math("ADD", b.math("MULTIPLY", ang, n), phase))), power)

    limit = b.math("ADD", 0.16, b.math("ADD", b.math("MULTIPLY", spikes(4.0, seed, 14.0), 0.8),
                                           b.math("MULTIPLY", spikes(5.0, seed + 0.9, 10.0), 0.45)))
    inside = b.maprange(b.math("SUBTRACT", limit, r), 0.0, 0.015, smooth=False)
    core = b.maprange(r, 0.38, 0.14)
    col = b.mixcol(core, color, (1.0, 1.0, 1.0))
    b.output(col, b.math("MULTIPLY", inside, fade))
    return mat


def glow_material(name, color, hard=0.0, noisy=True):
    """Round glow: energy balls, summon light, explosions."""
    mat = _new(name)
    b = NB(mat)
    fade, time = _fade_time(b)
    r, ang, u, v = b.polar()
    if noisy:
        n = b.noise(b.math("MULTIPLY", u, 3.0), b.math("MULTIPLY", v, 3.0), time, scale=2.0, detail=2.0)
        r = b.math("ADD", r, b.math("MULTIPLY", b.math("SUBTRACT", n, 0.5), 0.35))
    a = b.maprange(r, 1.0, 0.55 + hard * 0.4)
    core = b.maprange(r, 0.6, 0.15)
    col = b.mixcol(core, color, (1.0, 1.0, 1.0))
    b.output(col, b.math("MULTIPLY", a, fade))
    return mat


def beam_material(name, color):
    mat = _new(name)
    b = NB(mat)
    fade, time = _fade_time(b)
    u, v = b.uv()
    c = b.math("SUBTRACT", 1.0, b.math("MULTIPLY", b.math("ABSOLUTE", b.math("SUBTRACT", v, 0.5)), 2.0))
    flick = b.noise(b.math("SUBTRACT", b.math("MULTIPLY", u, 6.0), b.math("MULTIPLY", time, 4.0)),
                    b.math("MULTIPLY", v, 2.0), time, scale=1.5, detail=1.0)
    c = b.math("ADD", c, b.math("MULTIPLY", b.math("SUBTRACT", flick, 0.5), 0.5))
    a = b.maprange(c, 0.05, 0.35)
    core = b.maprange(c, 0.45, 0.8)
    col = b.mixcol(core, color, (1.0, 1.0, 1.0))
    b.output(col, b.math("MULTIPLY", a, fade))
    return mat


def rock_material(name, seed=0.0):
    mat = _new(name)
    b = NB(mat)
    fade, time = _fade_time(b)
    r, ang, u, v = b.polar()
    n = b.noise(b.math("MULTIPLY", ang, 0.8), seed, None, scale=1.0, detail=1.0)
    edge = b.math("ADD", 0.55, b.math("MULTIPLY", n, 0.4))
    a = b.maprange(b.math("SUBTRACT", edge, r), 0.0, 0.03)
    shade = b.maprange(v, 0.2, 0.9)
    col = b.mixcol(shade, (0.18, 0.13, 0.1), (0.45, 0.36, 0.28))
    b.output(col, b.math("MULTIPLY", a, fade))
    return mat


def fade_path(mat):
    return 'nodes["Fade"].outputs[0].default_value'


def time_path(mat):
    return 'nodes["Time"].outputs[0].default_value'
