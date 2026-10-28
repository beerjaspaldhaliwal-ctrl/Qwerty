"""Assemble the episode: title card, scenes with transitions, subtitles,
attack-name captions, voices, sound effects, music and the end card, all
in one Video Sequencer scene that renders straight to MP4."""

import math

import bpy

from . import compat, materials
from .builder import TAG, make_plane

BASE = 1   # first frame of the edit


def _try_set(obj, **props):
    for k, v in props.items():
        try:
            setattr(obj, k, v)
        except (AttributeError, TypeError, ValueError):
            pass


def _text_object(name, body, size, color, collection, outline=0.0, z=0.0, y=0.0):
    cu = bpy.data.curves.new(name, "FONT")
    cu.body = body
    cu.size = size
    cu.align_x = "CENTER"
    cu.align_y = "CENTER"
    cu[TAG] = True
    if outline:
        cu.offset = outline
    ob = bpy.data.objects.new(name, cu)
    ob[TAG] = True
    ob.rotation_euler = (math.radians(90), 0, 0)
    ob.location = (0, y, z)
    collection.objects.link(ob)
    mat = materials.flat_material(name + "_mat", color)
    cu.materials.append(mat)
    return ob


def _ui_font():
    """Blender's bundled Inter font (the sequencer default is monospaced)."""
    import os
    folder = bpy.utils.system_resource("DATAFILES", path="fonts") or ""
    for name in ("Inter.woff2", "DejaVuSans.woff2", "Inter.ttf"):
        path = os.path.join(folder, name)
        if os.path.exists(path):
            try:
                return bpy.data.fonts.load(path, check_existing=True)
            except RuntimeError:
                pass
    return None


def build_card(name, lines, length, plan, builder, color=(1.0, 0.55, 0.05)):
    """A 3D title/end card: radial burst, big outlined title, pop-in animation."""
    old = bpy.data.scenes.get(name)
    if old:
        bpy.data.scenes.remove(old)
    scene = bpy.data.scenes.new(name)
    scene[TAG] = "card"
    builder.setup_render(scene, length)
    col = scene.collection
    aspect = builder.aspect

    cd = bpy.data.cameras.new(name + "_cam")
    cd.type = "ORTHO"
    cd.ortho_scale = 10.0
    cd.sensor_fit = "HORIZONTAL"
    cam = bpy.data.objects.new(name + "_cam", cd)
    cam[TAG] = True
    cam.location = (0, -10, 0)
    cam.rotation_euler = (math.radians(90), 0, 0)
    col.objects.link(cam)
    scene.camera = cam

    mat = materials.burst_material(name + "_burst", color)
    bg = make_plane(name + "_burst", 12, 12 / aspect * 1.2, origin="center", material=mat, collection=col)
    bg.location = (0, 2, 0)
    compat.set_keys(mat.node_tree, materials.time_path(mat), [(0, 0.0, "LINEAR"), (length, length * 0.04, "LINEAR")])

    title, *rest = [l for l in lines if l] or [""]
    size = min(1.25, 8.6 / max(1, len(title) * 0.62))
    t_front = _text_object(name + "_title", title, size, (1.0, 0.95, 0.35), col, z=0.45 if rest else 0.0, y=-0.2)
    t_back = _text_object(name + "_title_ol", title, size, (0.08, 0.03, 0.12), col, outline=0.045,
                          z=0.45 if rest else 0.0, y=-0.1)
    for ob in (t_front, t_back):
        compat.set_keys(ob, "scale", [(0, 0.05, "BEZIER"), (8, 1.15, "BEZIER"), (13, 1.0, "CONSTANT")], index=0)
        compat.set_keys(ob, "scale", [(0, 0.05, "BEZIER"), (8, 1.15, "BEZIER"), (13, 1.0, "CONSTANT")], index=1)
        compat.set_keys(ob, "scale", [(0, 0.05, "BEZIER"), (8, 1.15, "BEZIER"), (13, 1.0, "CONSTANT")], index=2)
    if rest:
        sub = " ".join(rest)
        ssize = min(0.5, 8.6 / max(1, len(sub) * 0.6))
        _text_object(name + "_sub", sub, ssize, (1, 1, 1), col, z=-0.75, y=-0.2)
        _text_object(name + "_sub_ol", sub, ssize, (0.08, 0.03, 0.12), col, outline=0.03, z=-0.75, y=-0.1)
    flash = make_plane(name + "_flash", 12, 8, origin="center", material=materials.flash_material(name + "_flash"),
                       collection=col)
    flash.location = (0, -1, 0)
    fm = flash.active_material
    compat.set_keys(fm.node_tree, materials.fade_path(fm), [(0, 1.0, "LINEAR"), (6, 0.0, "CONSTANT")])
    return scene


def assemble(script, plan, scenes, builder, output_path):
    name = f"AS EPISODE - {plan.title}"[:60]
    old = bpy.data.scenes.get(name)
    if old:
        bpy.data.scenes.remove(old)
    master = bpy.data.scenes.new(name)
    master[TAG] = "episode"
    builder.setup_render(master, plan.total)
    master.frame_start = BASE
    master.frame_end = BASE + plan.total - 1
    compat.set_video_output(master, output_path)
    coll = compat.sequence_collection(master)
    W, H = plan.resolution

    title_lines = [plan.title, plan.episode]
    title = build_card("AS Title Card", title_lines, plan.title_len, plan, builder)
    end = build_card("AS End Card", ["TO BE CONTINUED...", plan.next_text], plan.end_len, plan, builder,
                     color=(0.2, 0.45, 1.0))

    clips = [(title, BASE, "wipe")]
    for tl, sc in zip(plan.scenes, scenes):
        clips.append((sc, BASE + tl.offset, tl.transition))
    last = plan.scenes[-1] if plan.scenes else None
    end_start = BASE + (last.offset + last.length - plan.overlap if last else plan.title_len - plan.overlap)
    clips.append((end, end_start, "fade"))

    strips = []
    for i, (sc, start, _tr) in enumerate(clips):
        st = coll.new_scene(name=sc.name[:60], scene=sc, channel=1 + (i % 2), frame_start=start)
        strips.append(st)

    if plan.overlap > 0:
        for i in range(1, len(strips)):
            a, b = strips[i - 1], strips[i]
            kind = clips[i][2]
            if kind == "cut":
                continue
            s0, s1 = int(b.frame_final_start), int(a.frame_final_end)
            if s1 <= s0:
                continue
            etype = {"wipe": "WIPE", "fade": "GAMMA_CROSS", "cross": "CROSS", "dissolve": "GAMMA_CROSS",
                     "iris": "WIPE"}.get(kind, "WIPE")
            fx = compat.new_effect(coll, f"trans{i}", etype, 3, s0, s1, input1=a, input2=b)
            if etype == "WIPE":
                _try_set(fx, transition_type="IRIS" if kind == "iris" else "SINGLE", direction="IN",
                         angle=math.radians(-20), blur_width=0.08)

    def text_strip(chan, start, end, text, size, loc, color=(1, 1, 1), box=False, outline=True):
        st = compat.new_effect(coll, text[:20] or "text", "TEXT", chan, start, max(end, start + 1))
        st.text = text
        if font:
            _try_set(st, font=font)
        _try_set(st, font_size=size, location=loc, align_x="CENTER", align_y="BOTTOM",
                 anchor_x="CENTER", anchor_y="BOTTOM", wrap_width=0.86, use_shadow=True,
                 shadow_color=(0, 0, 0, 1))
        _try_set(st, color=(*color[:3], 1.0))
        if box:
            _try_set(st, use_box=True, box_color=(0, 0, 0, 0.55), box_margin=0.012)
        if outline:
            _try_set(st, use_outline=True, outline_color=(0, 0, 0, 1), outline_width=0.06)
        return st

    font = _ui_font()

    voice_chan = [8, 9]
    vi = 0
    for tl in plan.scenes:
        off = BASE + tl.offset
        for sub in tl.subtitles:
            text_strip(6, off + sub.start, off + sub.end, sub.text, int(H * 0.046), (0.5, 0.05), box=True)
            if sub.speaker:
                text_strip(7, off + sub.start, off + sub.end, sub.speaker.upper(), int(H * 0.04),
                           (0.5, 0.125), color=sub.color)
        for s, e, text in tl.captions:
            text_strip(10, off + s, off + e, text, int(H * 0.085), (0.5, 0.72), color=(1.0, 0.85, 0.15))
        for cue in tl.audio:
            try:
                if cue.kind == "music":
                    chan = 14
                elif cue.kind == "sfx":
                    chan = 12 if vi % 2 else 13
                else:
                    chan = voice_chan[vi % 2]
                vi += 1
                snd = coll.new_sound(name=cue.kind, filepath=cue.path, channel=chan, frame_start=off + cue.start)
                snd.volume = cue.volume
                if cue.end:
                    snd.frame_final_end = min(int(snd.frame_final_end), off + cue.end)
            except Exception as e:
                builder.warnings.append(f"audio skipped ({cue.path}): {e}")
    master.frame_end = BASE + plan.total - 1
    return master
