"""Small wrappers over Blender APIs that changed between 4.2 and 5.x."""

import bpy

INTERP = {"CONSTANT", "LINEAR", "BEZIER"}


def ensure_action(idblock, name):
    ad = idblock.animation_data or idblock.animation_data_create()
    if ad.action is None:
        ad.action = bpy.data.actions.new(name)
        ad.action["as_generated"] = True
    return ad.action


def new_fcurve(idblock, data_path, index=0, name=None):
    act = ensure_action(idblock, name or f"{idblock.name}_anim")
    if hasattr(act, "fcurve_ensure_for_datablock"):          # 4.4+ slotted actions
        return act.fcurve_ensure_for_datablock(idblock, data_path, index=index)
    fc = act.fcurves.find(data_path, index=index)
    return fc or act.fcurves.new(data_path, index=index)


def set_keys(idblock, data_path, keys, index=0, default_interp="BEZIER"):
    """Write keyframes fast. keys: [(frame, value)] or [(frame, value, interp)].
    Later keys on the same frame replace earlier ones."""
    by_frame = {}
    for k in keys:
        f, v = int(k[0]), float(k[1])
        by_frame[f] = (v, k[2] if len(k) > 2 else default_interp)
    if not by_frame:
        return None
    fc = new_fcurve(idblock, data_path, index)
    frames = sorted(by_frame)
    pts = fc.keyframe_points
    if len(pts):
        pts.clear()
    pts.add(len(frames))
    co = []
    for f in frames:
        co += [f, by_frame[f][0]]
    pts.foreach_set("co", co)
    for kp, f in zip(pts, frames):
        interp = by_frame[f][1]
        kp.interpolation = interp if interp in INTERP else "BEZIER"
        kp.handle_left_type = kp.handle_right_type = "AUTO_CLAMPED"
    fc.update()
    return fc


def set_visibility(obj, intervals, total_end=None):
    """Show an object only during [(start, end)] frame intervals."""
    keys = [(0, 1.0, "CONSTANT")]
    for s, e in sorted(intervals):
        keys.append((max(0, s), 0.0, "CONSTANT"))
        keys.append((max(1, e), 1.0, "CONSTANT"))
    if not intervals:
        keys = [(0, 1.0, "CONSTANT")]
    set_keys(obj, "hide_render", keys)
    set_keys(obj, "hide_viewport", keys)
    obj.hide_render = True


def set_engine(scene, engine):
    """engine: EEVEE, CYCLES or WORKBENCH."""
    names = {"EEVEE": ["BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"], "CYCLES": ["CYCLES"],
             "WORKBENCH": ["BLENDER_WORKBENCH"]}.get(engine, [engine])
    for n in names:
        try:
            scene.render.engine = n
            return n
        except TypeError:
            continue
    return scene.render.engine


def sequence_collection(scene):
    sed = scene.sequence_editor or scene.sequence_editor_create()
    return getattr(sed, "strips", None) or sed.sequences


def new_effect(coll, name, type, channel, frame_start, frame_end, input1=None, input2=None):
    attempts = [
        dict(frame_end=frame_end, seq1=input1, seq2=input2),
        dict(length=frame_end - frame_start, input1=input1, input2=input2),
        dict(frame_end=frame_end, input1=input1, input2=input2),
    ]
    last = None
    for kw in attempts:
        kw = {k: v for k, v in kw.items() if v is not None}
        try:
            return coll.new_effect(name=name, type=type, channel=channel, frame_start=frame_start, **kw)
        except TypeError as e:
            last = e
    raise last


def set_video_output(scene, filepath):
    r = scene.render
    r.filepath = filepath
    try:
        r.image_settings.media_type = "VIDEO"            # 5.x
    except (AttributeError, TypeError):
        pass
    try:
        r.image_settings.file_format = "FFMPEG"
        r.ffmpeg.format = "MPEG4"
        r.ffmpeg.codec = "H264"
        r.ffmpeg.constant_rate_factor = "HIGH"
        r.ffmpeg.ffmpeg_preset = "GOOD"
        r.ffmpeg.audio_codec = "AAC"
        r.ffmpeg.audio_bitrate = 192
    except (AttributeError, TypeError) as e:
        print("Anime Studio: could not set video output:", e)


def set_blend_mode(mat, blended=True):
    if hasattr(mat, "surface_render_method"):
        try:
            mat.surface_render_method = "BLENDED" if blended else "DITHERED"
        except TypeError:
            pass
    try:
        mat.blend_method = "BLEND" if blended else "HASHED"
    except (AttributeError, TypeError):
        pass
    if hasattr(mat, "show_transparent_back"):
        mat.show_transparent_back = False
