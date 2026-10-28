"""Build Blender scenes from an EpisodePlan.

One Blender scene per script scene: a painted background plane far
behind, characters as layered image planes (one per expression / mouth /
blink state, switched by keyed visibility), an animated camera that cuts
between shots, and procedural effects. editor.py then strings the scenes
together into the episode edit.
"""

import math
import os
import random
import re

import bpy

from . import compat, materials, placeholder_art
from .planner import LENS, SENSOR, OFFSTAGE_X
from .script_parser import EXPRESSION_ALIASES, parse_color

BG_Y = 14.0
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".tga", ".tif", ".tiff", ".bmp", ".exr"}
MODE_WORDS = {"talk": "talk", "talking": "talk", "mouth": "talk", "mouthopen": "talk", "open": "talk",
              "speak": "talk", "speaking": "talk", "blink": "blink", "closed": "blink",
              "eyesclosed": "blink"}
TAG = "as_generated"


# ---------------------------------------------------------------------------
# Character art
# ---------------------------------------------------------------------------

def _canonical_key(stem):
    """'super_angry_talk' -> ('super', 'angry', 'talk'); 'blink' -> ('', 'neutral', 'blink')."""
    tokens = [t for t in re.split(r"[^a-z0-9]+", stem.lower()) if t]
    mode, expr, rest = "", "", []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        two = t + tokens[i + 1] if i + 1 < len(tokens) else None
        if two and two in MODE_WORDS:
            mode = MODE_WORDS[two]
            i += 2
            continue
        if t in MODE_WORDS and not mode:
            mode = MODE_WORDS[t]
        elif t in EXPRESSION_ALIASES and not expr:
            expr = EXPRESSION_ALIASES[t]
        else:
            rest.append(t)
        i += 1
    return "_".join(rest), expr or "neutral", mode


class CharacterArt:
    """All images of one character, keyed by (form, expression, mode)."""

    def __init__(self, cdef, assets_dir, forms_used, warnings):
        self.cdef = cdef
        self.images = {}          # key -> (image, grid, cell)
        self.key_color = parse_color(cdef.key, None) if cdef.key and cdef.key.lower() not in ("none", "no") else None
        if cdef.key and cdef.key.lower() in ("white", "#fff", "#ffffff"):
            self.key_color = (1.0, 1.0, 1.0)
        self.placeholder = False
        self._mats = {}
        found = self._load(assets_dir, warnings)
        if not found:
            self._make_placeholders(forms_used)

    def _path(self, assets_dir, p):
        return p if os.path.isabs(p) else os.path.normpath(os.path.join(assets_dir, p))

    def _load(self, assets_dir, warnings):
        c = self.cdef
        if c.sheet:
            path = self._path(assets_dir, c.sheet)
            if not os.path.exists(path):
                warnings.append(f"{c.name}: sheet not found: {path}")
            else:
                img = bpy.data.images.load(path, check_existing=True)
                cols, rows = c.grid
                cells = c.cells or ["neutral"]
                for idx, name in enumerate(cells):
                    if idx >= cols * rows:
                        break
                    key = _canonical_key(name)
                    self.images[key] = (img, (cols, rows), (idx % cols, idx // cols))
        if c.folder:
            path = self._path(assets_dir, c.folder)
            if not os.path.isdir(path):
                warnings.append(f"{c.name}: folder not found: {path}")
            else:
                files = sorted(f for f in os.listdir(path) if os.path.splitext(f)[1].lower() in IMAGE_EXT)
                for f in files:
                    key = _canonical_key(os.path.splitext(f)[0])
                    img = bpy.data.images.load(os.path.join(path, f), check_existing=True)
                    self.images.setdefault(key, (img, (1, 1), (0, 0)))
                if files and ("", "neutral", "") not in self.images:
                    first = self.images.get(_canonical_key(os.path.splitext(files[0])[0]))
                    self.images[("", "neutral", "")] = first
        return bool(self.images)

    def _make_placeholders(self, forms_used):
        c = self.cdef
        self.placeholder = True
        draw = placeholder_art.DRAWERS.get(c.kind, placeholder_art.fighter)
        color = parse_color(c.color)
        seed = sum(ord(ch) for ch in c.name)
        style = "long" if any(w in c.voice.lower() for w in ("female", "girl", "woman")) else "spiky"
        for form in [""] + sorted(f for f in forms_used if f):
            exprs = placeholder_art.EXPRESSIONS if c.kind != "object" else ["neutral"]
            for expr in exprs:
                for mode in ("", "talk", "blink") if c.kind != "object" else ("",):
                    img = draw(c.name, color, expr=expr, talk=mode == "talk", blink=mode == "blink",
                               form=form, seed=seed, style=style)
                    self.images[(form, expr, mode)] = (img, (1, 1), (0, 0))

    def base_key(self, form, expr):
        for key in ((form, expr, ""), (form, "neutral", ""), ("", expr, ""), ("", "neutral", "")):
            if key in self.images:
                return key
        return next(iter(self.images))

    def variant_key(self, base, mode):
        key = (base[0], base[1], mode)
        return key if key in self.images else None

    def size(self, key):
        img, (cols, rows), _cell = self.images[key]
        w, h = img.size[0] / cols, img.size[1] / rows
        if w <= 0 or h <= 0:
            w, h = 1, 1
        height = self.cdef.height
        return height * w / h, height

    def material(self, key):
        if key not in self._mats:
            img, grid, cell = self.images[key]
            name = f"AS_{self.cdef.name}_{'_'.join(k for k in key if k)}"
            self._mats[key] = materials.image_material(name, img, grid, cell, self.key_color)
        return self._mats[key]


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def make_plane(name, w, h, origin="bottom", material=None, collection=None):
    x0, x1 = (0.0, w) if origin == "left" else (-w / 2, w / 2)
    z0, z1 = (0.0, h) if origin == "bottom" else (-h / 2, h / 2)
    me = bpy.data.meshes.new(name)
    me.from_pydata([(x0, 0, z0), (x1, 0, z0), (x1, 0, z1), (x0, 0, z1)], [], [(0, 1, 2, 3)])
    uv = me.uv_layers.new(name="UVMap")
    for i, co in enumerate(((0, 0), (1, 0), (1, 1), (0, 1))):
        uv.data[i].uv = co
    if material:
        me.materials.append(material)
    ob = bpy.data.objects.new(name, me)
    ob[TAG] = True
    if collection:
        collection.objects.link(ob)
    return ob


def make_empty(name, collection, parent=None):
    ob = bpy.data.objects.new(name, None)
    ob.empty_display_size = 0.3
    ob[TAG] = True
    collection.objects.link(ob)
    if parent:
        ob.parent = parent
    return ob


def _key_fade(mat, keys):
    compat.set_keys(mat.node_tree, materials.fade_path(mat), keys)


def _key_time(mat, start, end, rate=1.0):
    compat.set_keys(mat.node_tree, materials.time_path(mat),
                    [(start, 0.0, "LINEAR"), (end, (end - start) * rate, "LINEAR")])


def _fade_keys(start, end, peak=1.0, fade_in=2, fade_out=4):
    fade_in = max(1, min(fade_in, (end - start) // 2))
    fade_out = max(1, min(fade_out, (end - start) // 2))
    return [(start - 1, 0.0, "LINEAR"), (start + fade_in, peak, "LINEAR"),
            (end - fade_out, peak, "LINEAR"), (end, 0.0, "LINEAR")]


# ---------------------------------------------------------------------------
# Scene builder
# ---------------------------------------------------------------------------

class SceneBuilder:
    def __init__(self, script, plan, assets_dir, engine="EEVEE", quality=100, warnings=None):
        self.script = script
        self.plan = plan
        self.assets_dir = assets_dir
        self.engine = engine
        self.quality = quality
        self.warnings = warnings if warnings is not None else []
        self.aspect = plan.resolution[0] / float(plan.resolution[1])
        self.art = {}
        self._bg_cache = {}

    def character_art(self, name):
        if name not in self.art:
            cdef = self.script.characters[name]
            forms = set()
            for sc in self.plan.scenes:
                tr = sc.chars.get(name)
                if tr:
                    forms.update(f for _k, f in tr.form_keys)
            self.art[name] = CharacterArt(cdef, self.assets_dir, forms, self.warnings)
        return self.art[name]

    def setup_render(self, scene, length):
        r = scene.render
        r.resolution_x, r.resolution_y = self.plan.resolution
        r.resolution_percentage = self.quality
        r.fps = self.plan.fps
        scene.frame_start, scene.frame_end = 0, max(0, length - 1)
        compat.set_engine(scene, self.engine)
        scene.view_settings.view_transform = "Standard"
        scene.view_settings.look = "None"
        try:
            scene.eevee.taa_render_samples = 16
        except AttributeError:
            pass
        if self.engine == "CYCLES":
            scene.cycles.samples = 8
            scene.cycles.use_denoising = False
            scene.cycles.transparent_max_bounces = 64
            scene.cycles.max_bounces = 0
        world = bpy.data.worlds.get("AS_World") or bpy.data.worlds.new("AS_World")
        world[TAG] = True
        world.color = (0, 0, 0)
        scene.world = world
        scene.render.film_transparent = False

    def build_scene(self, tl):
        name = f"AS {tl.name}"[:60]
        old = bpy.data.scenes.get(name)
        if old:
            bpy.data.scenes.remove(old)
        scene = bpy.data.scenes.new(name)
        scene[TAG] = "scene"
        self.setup_render(scene, tl.length)
        col = scene.collection

        cam = self.build_camera(scene, tl)
        self.build_background(scene, tl, cam)
        roots = {}
        for cname, tr in tl.chars.items():
            roots[cname] = self.build_character(scene, tl, cname, tr)
        for i, fx in enumerate(tl.fx):
            try:
                self.build_fx(scene, tl, fx, i, cam, roots)
            except Exception as e:                       # one bad effect shouldn't stop the episode
                self.warnings.append(f"{tl.name}: effect {fx.kind} skipped ({e})")
        tint = {"night": ((0.08, 0.12, 0.42), 0.25), "sunset": ((1.0, 0.45, 0.15), 0.12)}.get(tl.time)
        if tint:
            self.camera_plane(f"S{tl.index:02d}_tint", cam, 0.4, materials.flat_material(
                f"AS_tint_{tl.time}", tint[0], tint[1]), col)
        for shot in tl.shots:
            m = scene.timeline_markers.new(f"{shot.label} {' '.join(shot.subjects)}".strip()[:60], frame=shot.start)
            m.camera = cam
        return scene

    # -- camera ---------------------------------------------------------------
    def build_camera(self, scene, tl):
        cd = bpy.data.cameras.new(f"S{tl.index:02d}_Camera")
        cd.lens = LENS
        cd.sensor_width = SENSOR
        cd.sensor_fit = "HORIZONTAL"
        cd.clip_start = 0.05
        cd.clip_end = 400
        cam = bpy.data.objects.new(f"S{tl.index:02d}_Camera", cd)
        cam[TAG] = True
        scene.collection.objects.link(cam)
        scene.camera = cam
        keys = tl.cam_keys or []
        if not keys:
            from .planner import CamKey
            keys = [CamKey(0, 0, 3, 14)]
        compat.set_keys(cam, "location", [(k.frame, k.x, k.interp) for k in keys], index=0)
        compat.set_keys(cam, "location", [(k.frame, -k.dist, k.interp) for k in keys], index=1)
        compat.set_keys(cam, "location", [(k.frame, k.z, k.interp) for k in keys], index=2)
        compat.set_keys(cam, "rotation_euler", [(k.frame, math.radians(90 + k.tilt), k.interp) for k in keys], index=0)
        compat.set_keys(cam, "rotation_euler", [(k.frame, math.radians(k.roll), k.interp) for k in keys], index=1)
        compat.set_keys(cam, "rotation_euler", [(0, 0.0, "CONSTANT")], index=2)
        # Camera shake: noise on the rotation, limited to each shake window.
        shakes = [fx for fx in tl.fx if fx.kind == "shake"]
        if shakes:
            fcurves = _fcurves(cam)
            for fc in fcurves:
                if fc.data_path == "rotation_euler" and fc.array_index in (0, 1):
                    for fx in shakes:
                        m = fc.modifiers.new("NOISE")
                        m.scale = 1.6
                        m.strength = math.radians(2.2) * fx.strength
                        m.phase = fx.start * 0.37 + fc.array_index * 11
                        m.use_restricted_range = True
                        m.frame_start = fx.start
                        m.frame_end = fx.end
                        m.blend_in = 1
                        m.blend_out = min(6, max(1, (fx.end - fx.start) // 2))
        return cam

    def camera_plane(self, name, cam, distance, mat, collection, margin=1.45):
        w = SENSOR / LENS * distance * margin
        ob = make_plane(name, w, w / self.aspect, origin="center", material=mat, collection=collection)
        ob.parent = cam
        ob.rotation_euler = (math.radians(-90), 0, 0)   # plane is XZ; camera looks down -Z
        ob.location = (0, 0, -distance)
        return ob

    # -- background -------------------------------------------------------------
    def background_image(self, tl):
        loc = self.script.locations.get(tl.location)
        if loc and loc.image:
            p = loc.image if os.path.isabs(loc.image) else os.path.join(self.assets_dir, loc.image)
            if os.path.exists(p):
                return bpy.data.images.load(p, check_existing=True), loc.ground
            self.warnings.append(f"location '{tl.location}': image not found: {p}")
        key = (tl.location, tl.time)
        if key not in self._bg_cache:
            self._bg_cache[key] = placeholder_art.background(re.sub(r"\W+", "_", tl.location), tl.time,
                                                             seed=sum(map(ord, tl.location)))
        return self._bg_cache[key], 0.24

    def build_background(self, scene, tl, cam):
        img, ground = self.background_image(tl)
        iw, ih = img.size if img.size[0] else (16, 9)
        img_aspect = iw / float(ih)
        per_unit = (SENSOR / self.aspect) / LENS      # visible height per unit of distance
        keys = tl.cam_keys
        wide = next((k for k in keys if k.dist > 8), None) or (keys[0] if keys else None)
        if wide is None:
            from .planner import CamKey
            wide = CamKey(0, 0, 3, 14)
        # Where the characters' feet line lands on the background, seen from the wide shot.
        z_hit = wide.z - wide.z * (wide.dist + BG_Y) / wide.dist
        # Size the painting so the wide shot shows nearly all of it, then grow it
        # only as much as needed to cover every other camera position.
        lo, hi, half_w = z_hit, z_hit, 1.0
        for k in keys:
            span = (k.dist + BG_Y) * per_unit
            zc = k.z + math.tan(math.radians(k.tilt)) * (k.dist + BG_Y)
            roll = 1.0 + abs(math.sin(math.radians(k.roll))) * self.aspect
            lo = min(lo, zc - span * 0.52 * roll)
            hi = max(hi, zc + span * 0.52 * roll)
            half_w = max(half_w, abs(k.x) + span * self.aspect * 0.52 * roll)   # camera never yaws
        g = min(max(ground, 0.05), 0.9)
        wide_span = (wide.dist + BG_Y) * per_unit
        Hb = max(wide_span * 1.08, (z_hit - lo) / g, (hi - z_hit) / (1 - g), 2 * half_w / img_aspect)
        Wb = Hb * img_aspect
        mat = materials.image_material(f"AS_BG_{tl.index:02d}", img, opaque=True)
        ob = make_plane(f"S{tl.index:02d}_Background", Wb, Hb, origin="bottom", material=mat,
                        collection=scene.collection)
        ob.location = (0, BG_Y, z_hit - g * Hb)
        return ob

    # -- characters -------------------------------------------------------------
    def build_character(self, scene, tl, name, tr):
        art = self.character_art(name)
        c = art.cdef
        col = scene.collection
        pre = f"S{tl.index:02d}_{name}"
        root = make_empty(pre, col)
        root.location = (tr.x_at(0), tr.y, 0)
        compat.set_keys(root, "location", tr.x_keys, index=0)
        compat.set_keys(root, "location", tr.z_keys, index=2)
        body = make_empty(pre + "_body", col, parent=root)
        h = c.height
        centered = c.kind == "object"
        if centered:
            body.location.z = h / 2

        # Facing: mirror the artwork when it looks the wrong way.
        art_dir = c.faces if c.faces in ("left", "right") else "front"
        flips = []
        for f, want in sorted(tr.facing_keys):
            flip = art_dir != "front" and art_dir != want
            flips.append((f, -1.0 if flip else 1.0, "CONSTANT"))
        if flips:
            compat.set_keys(root, "scale", flips, index=0)

        if tr.spin_keys:
            axis = 1 if centered else 2
            compat.set_keys(root if not centered else body, "rotation_euler", tr.spin_keys, index=axis)

        # Breathing: tiny vertical squash, offset per character.
        compat.set_keys(body, "scale", [(0, 1.0, "CONSTANT")], index=2)
        fc = next((f for f in _fcurves(body) if f.data_path == "scale" and f.array_index == 2), None)
        if fc and c.kind != "object":
            m = fc.modifiers.new("FNGENERATOR")
            m.function_type = "SIN"
            m.amplitude = 0.006
            m.phase_multiplier = 2 * math.pi / (self.plan.fps * 2.6)
            m.phase_offset = len(name)
            m.use_additive = True

        # Which image is visible on each frame.
        rng = random.Random(sum(map(ord, name)) + tl.index)
        length = tl.length
        expr_keys = sorted(tr.expr_keys, key=lambda k: k[0])
        form_keys = sorted(tr.form_keys, key=lambda k: k[0])
        talk_frames = set()
        for s, e in tr.talk:
            # Limited animation mouth flaps: open/closed in small irregular steps.
            f = s
            open_ = True
            while f < e:
                step = rng.choice((2, 3, 3, 4)) if open_ else rng.choice((2, 2, 3))
                if open_:
                    talk_frames.update(range(f, min(e, f + step)))
                f += step
                open_ = not open_
        blink_frames = set()
        f = rng.randint(self.plan.fps, self.plan.fps * 3)
        while f < length:
            blink_frames.update((f, f + 1, f + 2))
            f += rng.randint(int(self.plan.fps * 2.2), int(self.plan.fps * 4.5))

        states = []
        ei = fi = 0
        expr, form = "neutral", ""
        bob = []
        for fr in range(length):
            while ei < len(expr_keys) and expr_keys[ei][0] <= fr:
                expr = expr_keys[ei][1]
                ei += 1
            while fi < len(form_keys) and form_keys[fi][0] <= fr:
                form = form_keys[fi][1]
                fi += 1
            base = art.base_key(form, expr)
            key = base
            if fr in blink_frames and art.variant_key(base, "blink"):
                key = art.variant_key(base, "blink")
            elif fr in talk_frames:
                v = art.variant_key(base, "talk")
                if v:
                    key = v
                else:
                    bob.append(fr)
            states.append(key)

        intervals = {}
        start = 0
        for fr in range(1, length + 1):
            if fr == length or states[fr] != states[start]:
                intervals.setdefault(states[start], []).append((start, fr))
                start = fr
        for i, (key, ivs) in enumerate(intervals.items()):
            w, hh = art.size(key)
            ob = make_plane(f"{pre}_{'_'.join(k for k in key if k)}", w, hh,
                            origin="center" if centered else "bottom", material=art.material(key), collection=col)
            ob.parent = body
            compat.set_visibility(ob, ivs)

        if bob:
            # No open-mouth art: bob the head a touch while talking instead.
            bob_set = set(bob)
            keys = []
            prev = None
            for fr in range(length):
                v = 0.012 * h if fr in bob_set else 0.0
                if v != prev:
                    keys.append((fr, (h / 2 if centered else 0.0) + v, "CONSTANT"))
                    prev = v
            compat.set_keys(body, "location", keys, index=2)

        # Contact shadow on the ground, following the character left/right.
        sh = make_plane(pre + "_shadow", h * 0.55, h * 0.12, origin="center",
                        material=self._shadow_mat(), collection=col)
        sh.location = (0, tr.y + 0.02, 0.01)
        con = sh.constraints.new("COPY_LOCATION")
        con.target = root
        con.use_y = con.use_z = False
        return root

    def _shadow_mat(self):
        mat = bpy.data.materials.get("AS_shadow")
        if mat is None:
            mat = materials.glow_material("AS_shadow", (0.0, 0.0, 0.0), hard=0.6, noisy=False)
            mat.node_tree.nodes["Fade"].outputs[0].default_value = 0.35
        return mat

    # -- effects -----------------------------------------------------------------
    def char_point(self, tl, name, f, height_frac=0.55):
        tr = tl.chars.get(name)
        if not tr:
            return 0.0, 2.0, 0.0
        h = self.script.characters[name].height
        return tr.x_at(f), tr.z_at(f) + h * height_frac, h

    def facing(self, tl, name, f):
        tr = tl.chars.get(name)
        d = "right"
        if tr:
            for k, v in sorted(tr.facing_keys):
                if k <= f:
                    d = v
        return 1 if d == "right" else -1

    def front_y(self, tl):
        return min([t.y for t in tl.chars.values()] + [0.0]) - 0.3

    def build_fx(self, scene, tl, fx, i, cam, roots):
        col = scene.collection
        pre = f"S{tl.index:02d}_fx{i:03d}_{fx.kind}"
        s, e = fx.start, max(fx.end, fx.start + 2)

        if fx.kind == "shake":
            return  # handled on the camera
        if fx.kind == "aura":
            root = roots.get(fx.char)
            if not root:
                return
            art = self.character_art(fx.char)
            w, h = art.size(art.base_key("", "neutral"))
            mat = materials.aura_material(pre, fx.color)
            ob = make_plane(pre, max(w, h * 0.6) * 1.6, h * 1.35, origin="bottom", material=mat, collection=col)
            ob.parent = root
            ob.location = (0, 0.07, -0.12 * h)
            ramp = fx.extra.get("ramp", 6)
            peak = fx.strength
            keys = [(s - 1, 0.0, "LINEAR"), (s + max(2, ramp), peak, "LINEAR")]
            calm = fx.extra.get("beat_end")
            if calm and calm + 12 < e - 6:
                # After the power-up the aura keeps burning, but quieter.
                keys += [(calm, peak, "LINEAR"), (calm + 12, peak * 0.55, "LINEAR"), (e - 6, peak * 0.55, "LINEAR")]
            else:
                keys.append((e - 6, peak, "LINEAR"))
            keys.append((e, 0.0, "LINEAR"))
            _key_fade(mat, keys)
            _key_time(mat, s, e, rate=0.08)
            compat.set_visibility(ob, [(s, e + 1)])
            return
        if fx.kind == "speedlines":
            mat = materials.speedlines_material(pre, fx.color, self.aspect)
            ob = self.camera_plane(pre, cam, 1.0, mat, col)
            _key_fade(mat, _fade_keys(s, e, 1.0, 2, 3))
            _key_time(mat, s, e, rate=1.0)
            compat.set_visibility(ob, [(s - 1, e + 1)])
            return
        if fx.kind == "flash":
            mat = materials.flash_material(pre)
            ob = self.camera_plane(pre, cam, 0.5, mat, col)
            _key_fade(mat, [(s, fx.strength, "LINEAR"), (e, 0.0, "LINEAR")])
            compat.set_visibility(ob, [(s, e + 1)])
            return
        if fx.kind == "burst":
            x, z, h = self.char_point(tl, fx.char, s, 0.6)
            mat = materials.burst_material(pre, fx.color)
            ob = make_plane(pre, 60, 34, origin="center", material=mat, collection=col)
            ob.location = (x, 1.5, z)
            _key_fade(mat, _fade_keys(s, e, 1.0, 2, 2))
            _key_time(mat, s, e, rate=0.25)
            compat.set_visibility(ob, [(s - 1, e + 1)])
            return
        if fx.kind == "impact":
            if fx.extra.get("between") and fx.target:
                xa, za, h = self.char_point(tl, fx.char, s, 0.55)
                xb, zb, _ = self.char_point(tl, fx.target, s, 0.55)
                x, z = (xa + xb) / 2, (za + zb) / 2
            else:
                x, z, h = self.char_point(tl, fx.char, s, 0.08 if fx.extra.get("ground") else 0.55)
                if fx.target in tl.chars:
                    # Put the star on the side the blow came from.
                    x += h * 0.3 * (1 if tl.chars[fx.target].x_at(s) > x else -1)
            mat = materials.impact_material(pre, fx.color, seed=i * 1.7)
            size = h * (0.38 if not fx.extra.get("ground") else 0.9)
            ob = make_plane(pre, size, size * (1 if not fx.extra.get("ground") else 0.45),
                            origin="center", material=mat, collection=col)
            ob.location = (x, self.front_y(tl) - 0.05, z)
            sc = [(s, 0.3, "LINEAR"), (s + 3, 1.0, "BEZIER"), (e, 1.2, "LINEAR")]
            for ax in (0, 2):
                compat.set_keys(ob, "scale", sc, index=ax)
            _key_fade(mat, [(s, 1.0, "CONSTANT"), (s + max(2, (e - s) // 2), 1.0, "LINEAR"), (e, 0.0, "LINEAR")])
            compat.set_visibility(ob, [(s, e + 1)])
            return
        if fx.kind == "charge":
            x, z, h = self.char_point(tl, fx.char, s, 0.52)
            x += self.facing(tl, fx.char, s) * h * 0.3
            mat = materials.glow_material(pre, fx.color)
            ob = make_plane(pre, 1, 1, origin="center", material=mat, collection=col)
            ob.location = (x, self.front_y(tl) - 0.1, z)
            g = (e - s) // 2
            sc = [(s, h * 0.15, "BEZIER"), (s + g, h * 0.55, "LINEAR"), (e, h * 0.6, "LINEAR")]
            for ax in (0, 2):
                compat.set_keys(ob, "scale", sc, index=ax)
            _key_fade(mat, _fade_keys(s, e, 1.0, 4, 4))
            _key_time(mat, s, e, rate=0.3)
            compat.set_visibility(ob, [(s - 1, e + 1)])
            return
        if fx.kind == "beam":
            xa, za, h = self.char_point(tl, fx.char, s, 0.52)
            xa += self.facing(tl, fx.char, s) * h * 0.3
            if fx.target:
                xb, zb, _ = self.char_point(tl, fx.target, s, 0.5)
            else:
                xb, zb = xa + self.facing(tl, fx.char, s) * 14, za
            dx, dz = xb - xa, zb - za
            length = max(0.5, math.hypot(dx, dz) + 0.3)
            mat = materials.beam_material(pre, fx.color)
            ob = make_plane(pre, 1.0, h * 0.32, origin="left", material=mat, collection=col)
            ob.location = (xa, self.front_y(tl) - 0.12, za)
            ob.rotation_euler = (0, -math.atan2(dz, dx), 0)
            grow = fx.extra.get("grow", 6)
            compat.set_keys(ob, "scale", [(s, 0.02, "LINEAR"), (s + grow, length, "CONSTANT")], index=0)
            _key_fade(mat, _fade_keys(s, e, 1.0, 1, 5))
            _key_time(mat, s, e, rate=0.5)
            compat.set_visibility(ob, [(s - 1, e + 1)])
            return
        if fx.kind == "debris":
            x, _z, h = self.char_point(tl, fx.char, s, 0.0)
            rng = random.Random(i)
            for k in range(7):
                mat = materials.rock_material(f"{pre}_{k}", seed=rng.random() * 10)
                size = h * rng.uniform(0.05, 0.12)
                ob = make_plane(f"{pre}_{k}", size, size, origin="center", material=mat, collection=col)
                ox = x + rng.uniform(-0.75, 0.75) * h
                y = rng.choice([0.25, -0.5]) + (self.front_y(tl) if rng.random() < 0.5 else 0.0)
                ob.location = (ox, y, 0.05)
                delay = rng.randint(0, max(1, (e - s) // 3))
                compat.set_keys(ob, "location", [(s + delay, 0.05, "LINEAR"),
                                                 (e, rng.uniform(0.4, 1.3) * h, "LINEAR")], index=2)
                compat.set_keys(ob, "rotation_euler", [(s, 0.0, "LINEAR"), (e, rng.uniform(-3, 3), "LINEAR")],
                                index=1)
                _key_fade(mat, _fade_keys(s + delay, e, 1.0, 3, 5))
                compat.set_visibility(ob, [(s + delay - 1, e + 1)])
            return
        if fx.kind in ("summon", "explosion"):
            f0 = s + (int(self.plan.fps * 0.2) if fx.kind == "summon" else 0)
            if fx.char:
                x, z, h = self.char_point(tl, fx.char, f0, 0.45)
            else:
                x, z, h = 0.0, 1.6, 3.4
            mat = materials.glow_material(pre, fx.color, noisy=fx.kind == "explosion")
            ob = make_plane(pre, 1, 1, origin="center", material=mat, collection=col)
            ob.location = (x, self.front_y(tl) - 0.2, z)
            big = h * (1.7 if fx.kind == "summon" else 3.2)
            sc = [(s, h * 0.15, "BEZIER"), (s + max(3, (e - s) // 2), big, "LINEAR"), (e, big * 1.1, "LINEAR")]
            for ax in (0, 2):
                compat.set_keys(ob, "scale", sc, index=ax)
            _key_fade(mat, [(s - 1, 0.0, "LINEAR"), (s + 2, 1.0, "LINEAR"),
                            (s + (e - s) // 2, 1.0, "LINEAR"), (e, 0.0, "LINEAR")])
            _key_time(mat, s, e, rate=0.2)
            compat.set_visibility(ob, [(s - 1, e + 1)])
            if fx.kind == "explosion":
                self.build_fx(scene, tl, type(fx)("impact", s, s + (e - s) // 2, char=fx.char or "",
                                                  color=(1.0, 0.9, 0.5)), i + 500, cam, roots)
            return
        # Unknown effect kinds (e.g. 'hitflash') are simply not drawn.


def _fcurves(idblock):
    ad = idblock.animation_data
    if not ad or not ad.action:
        return []
    act = ad.action
    if hasattr(act, "layers") and len(act.layers):
        try:
            from bpy_extras.anim_utils import action_get_channelbag_for_slot
            cb = action_get_channelbag_for_slot(act, ad.action_slot)
            if cb:
                return list(cb.fcurves)
        except ImportError:
            pass
    return list(getattr(act, "fcurves", []))


def remove_generated():
    """Delete scenes and data from a previous build (only things we made)."""
    keep = [s for s in bpy.data.scenes if not s.get(TAG)]
    if not keep:
        keep = [bpy.data.scenes.new("Scene")]
    wm = bpy.context.window_manager
    for win in (wm.windows if wm else []):
        if win.scene.get(TAG):
            win.scene = keep[0]
    for s in [s for s in bpy.data.scenes if s.get(TAG)]:
        bpy.data.scenes.remove(s)
    for ob in [o for o in bpy.data.objects if o.get(TAG) and not o.users_scene]:
        bpy.data.objects.remove(ob)
    for coll in (bpy.data.meshes, bpy.data.cameras, bpy.data.materials, bpy.data.curves,
                 bpy.data.actions, bpy.data.images, bpy.data.worlds):
        for item in [x for x in coll if x.get(TAG) and x.users == 0]:
            coll.remove(item)
    return keep[0]
