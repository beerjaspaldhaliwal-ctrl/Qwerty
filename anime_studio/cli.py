"""Run Anime Studio without the UI.

    blender -b -P anime_studio/cli.py -- --script episode_01.txt [options]

Options:
    --script PATH      story script (required)
    --assets DIR       assets folder (default: the script's folder)
    --out PATH         video file to render (default: episode.mp4 next to the script)
    --engine NAME      EEVEE, CYCLES or WORKBENCH (default EEVEE)
    --quality N        resolution percentage (default 100)
    --hype N           0 calm, 1 normal, 2 max hype (default: from script)
    --tts              generate placeholder voices
    --save PATH        save the built .blend
    --stills DIR       render a still of the first frame of every shot into DIR
    --render           render the full episode video
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bpy  # noqa: E402

from anime_studio import pipeline  # noqa: E402


def main(argv):
    p = argparse.ArgumentParser(prog="anime_studio")
    p.add_argument("--script", required=True)
    p.add_argument("--assets", default="")
    p.add_argument("--out", default="")
    p.add_argument("--engine", default="EEVEE")
    p.add_argument("--quality", type=int, default=100)
    p.add_argument("--hype", type=int, default=None)
    p.add_argument("--tts", action="store_true")
    p.add_argument("--save", default="")
    p.add_argument("--stills", default="")
    p.add_argument("--max-stills", type=int, default=400)
    p.add_argument("--render", action="store_true")
    a = p.parse_args(argv)

    script = os.path.abspath(a.script)
    out = os.path.abspath(a.out) if a.out else os.path.splitext(script)[0] + ".mp4"
    master, lines = pipeline.build(path=script, assets_dir=a.assets, output_path=out, engine=a.engine.upper(),
                                   quality=a.quality, drama=a.hype, tts=a.tts)
    print("\n".join(lines))
    if a.save:
        bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(a.save))
    if a.stills:
        render_stills(master, os.path.abspath(a.stills), a.max_stills)
    if a.render:
        bpy.ops.render.render(animation=True, scene=master.name)
        print("Rendered", out)


def render_stills(master, folder, limit):
    """Render one PNG from the middle of every shot for quick review."""
    from anime_studio import builder
    from anime_studio.editor import BASE
    os.makedirs(folder, exist_ok=True)
    out = master.render.filepath
    try:
        master.render.image_settings.media_type = "IMAGE"
    except (AttributeError, TypeError):
        pass
    master.render.image_settings.file_format = "PNG"
    cuts = []
    for strip in _strips(master):
        if strip.type == "SCENE" and strip.scene.get(builder.TAG) == "scene":
            for m in strip.scene.timeline_markers:
                cuts.append((int(strip.frame_start) + m.frame, m.name))
    cuts.sort()
    picks = [(BASE + 20, "title")]
    for i, (f, name) in enumerate(cuts):
        nxt = cuts[i + 1][0] if i + 1 < len(cuts) else f + 24
        picks.append(((f + nxt) // 2, name))
    picks.append((master.frame_end - 30, "end"))
    for i, (f, name) in enumerate(picks[:limit]):
        master.frame_set(f)
        safe = "".join(c if c.isalnum() else "_" for c in name)[:40]
        master.render.filepath = os.path.join(folder, f"{i:03d}_f{f:05d}_{safe}.png")
        bpy.ops.render.render(write_still=True, scene=master.name)
    compat_out(master, out)


def compat_out(master, out):
    from anime_studio import compat
    compat.set_video_output(master, out)


def _strips(scene):
    sed = scene.sequence_editor
    return list(getattr(sed, "strips_all", None) or sed.sequences_all)


if __name__ == "__main__":
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    main(argv)
