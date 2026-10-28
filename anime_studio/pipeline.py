"""Script file -> finished, render-ready episode. Shared by the UI and the
command line."""

import os
import time

import bpy

from . import builder, editor, script_parser, voices
from .planner import Director


def sound_duration(path):
    from .planner import audio_duration
    d = audio_duration(path)
    if d:
        return d
    try:
        import aud
        snd = aud.Sound(path)
        rate = snd.specs[0]
        return snd.length / float(rate) if rate else None
    except Exception:
        return None


def load_script(path=None, text=None):
    if text is None:
        with open(path, encoding="utf-8-sig") as fh:
            text = fh.read()
    return script_parser.parse(text)


def check(path=None, text=None, assets_dir=""):
    """Parse and plan without building. Returns (script, plan, report_lines)."""
    script = load_script(path, text)
    plan = Director(script, assets_dir, sound_duration).plan() if not script.errors else None
    return script, plan, report(script, plan)


def report(script, plan, extra_warnings=()):
    lines = [f"Title: {script.title}  {script.episode}".rstrip(),
             f"Characters: {', '.join(script.characters) or '-'}",
             f"Locations: {', '.join(script.locations) or '-'}",
             f"Scenes: {len(script.scenes)}"]
    if plan:
        secs = plan.total / float(plan.fps)
        shots = sum(len(s.shots) for s in plan.scenes)
        fx = sum(len(s.fx) for s in plan.scenes)
        lines.append(f"Episode length: {int(secs // 60)}m {secs % 60:04.1f}s ({plan.total} frames @ {plan.fps} fps)")
        lines.append(f"Shots: {shots}   Effects: {fx}")
        for s in plan.scenes:
            lines.append(f"  {s.name}: {s.length / plan.fps:.1f}s, {len(s.shots)} shots, cast: {', '.join(s.chars)}")
    for e in script.errors:
        lines.append("ERROR: " + e)
    warnings = list(script.warnings) + (list(plan.warnings[len(script.warnings):]) if plan else [])
    warnings += list(extra_warnings)
    for w in dict.fromkeys(warnings):
        lines.append("Warning: " + w)
    return lines


def build(path=None, text=None, assets_dir="", output_path="//anime_episode.mp4", engine="EEVEE",
          quality=100, drama=None, tts=False, log=print):
    """Build the whole episode. Returns (master_scene, report_lines)."""
    t0 = time.time()
    if not assets_dir:
        assets_dir = os.path.dirname(os.path.abspath(path)) if path else bpy.path.abspath("//")
    assets_dir = bpy.path.abspath(assets_dir)
    script = load_script(path, text)
    if script.errors:
        raise ValueError("; ".join(script.errors))
    if drama is not None:
        script.drama = drama
    if tts or script.voices:
        n = voices.generate(script, assets_dir, report=log)
        log(f"Generated {n} voice lines")

    log("Planning shots...")
    plan = Director(script, assets_dir, sound_duration).plan()
    warnings = []
    builder.remove_generated()
    sb = builder.SceneBuilder(script, plan, assets_dir, engine, quality, warnings)
    scenes = []
    for tl in plan.scenes:
        log(f"Building {tl.name} ({tl.length} frames)...")
        scenes.append(sb.build_scene(tl))
    log("Assembling episode edit...")
    master = editor.assemble(script, plan, scenes, sb, output_path)
    wm = bpy.context.window_manager
    if wm and wm.windows:
        for win in wm.windows:
            win.scene = master
    lines = report(script, plan, warnings)
    lines.append(f"Built in {time.time() - t0:.1f}s. Output: {bpy.path.abspath(output_path)}")
    return master, lines
