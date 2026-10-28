"""Turn a parsed Script into a frame-accurate EpisodePlan.

This is the "director": it decides timing, shot framing, camera moves,
character positions, expressions, mouth flaps and effects. Pure Python
(no bpy) so the decisions can be tested outside Blender; builder.py turns
the plan into Blender data.
"""

import math
import os
import random
import wave
from dataclasses import dataclass, field

from .script_parser import TRANSIENT_EXPRESSIONS, parse_color

LENS = 35.0
SENSOR = 36.0
SLOT_X = {"farleft": -5.9, "left": -2.9, "centerleft": -1.45, "center": 0.0,
          "centerright": 1.45, "right": 2.9, "farright": 5.9}
OFFSTAGE_X = 15.0
FLY_Z = 9.0


@dataclass
class CamKey:
    frame: int
    x: float
    z: float
    dist: float
    roll: float = 0.0      # degrees
    tilt: float = 0.0      # degrees, positive looks up
    interp: str = "BEZIER"


@dataclass
class Shot:
    start: int
    end: int
    kind: str
    subjects: list = field(default_factory=list)
    label: str = ""


@dataclass
class Fx:
    kind: str
    start: int
    end: int
    char: str = ""
    target: str = ""
    color: tuple = (1.0, 0.8, 0.2)
    strength: float = 1.0
    extra: dict = field(default_factory=dict)


@dataclass
class CharTrack:
    name: str
    x_keys: list = field(default_factory=list)      # (frame, value, interp)
    z_keys: list = field(default_factory=list)
    spin_keys: list = field(default_factory=list)   # (frame, radians, interp)
    expr_keys: list = field(default_factory=list)   # (frame, expression)
    form_keys: list = field(default_factory=list)   # (frame, form or "")
    facing_keys: list = field(default_factory=list)  # (frame, "left"/"right")
    talk: list = field(default_factory=list)        # (start, end) mouth moving
    y: float = 0.0

    def x_at(self, f):
        return _eval(self.x_keys, f, OFFSTAGE_X)

    def z_at(self, f):
        return _eval(self.z_keys, f, 0.0)

    def expr_at(self, f):
        cur = "neutral"
        for k, e in self.expr_keys:
            if k <= f:
                cur = e
        return cur


def _eval(keys, f, default):
    if not keys:
        return default
    if f <= keys[0][0]:
        return keys[0][1]
    for (f0, v0, i0), (f1, v1, _i1) in zip(keys, keys[1:]):
        if f0 <= f < f1:
            if i0 == "CONSTANT":
                return v0
            t = (f - f0) / float(f1 - f0)
            return v0 + (v1 - v0) * t
    return keys[-1][1]


@dataclass
class Subtitle:
    start: int
    end: int
    speaker: str
    text: str
    color: tuple = (1, 1, 1)


@dataclass
class AudioCue:
    start: int
    path: str
    kind: str = "voice"    # voice, sfx, music
    volume: float = 1.0
    end: int = 0


@dataclass
class SceneTimeline:
    index: int
    name: str
    location: str
    time: str
    length: int = 0
    cam_keys: list = field(default_factory=list)
    shots: list = field(default_factory=list)
    chars: dict = field(default_factory=dict)
    fx: list = field(default_factory=list)
    subtitles: list = field(default_factory=list)
    audio: list = field(default_factory=list)
    captions: list = field(default_factory=list)    # (start, end, text)
    music: str = ""
    transition: str = "wipe"
    offset: int = 0        # first frame of this scene in the episode edit


@dataclass
class EpisodePlan:
    title: str
    episode: str
    fps: int
    resolution: tuple
    scenes: list
    title_len: int
    end_len: int
    next_text: str
    overlap: int
    total: int
    warnings: list = field(default_factory=list)


def audio_duration(path):
    """Seconds of a WAV file, or None if it can't be read here."""
    try:
        with wave.open(path, "rb") as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:
        return None


class Director:
    def __init__(self, script, assets_dir="", duration_lookup=None, seed=7):
        self.s = script
        self.fps = script.fps
        self.aspect = script.resolution[0] / float(script.resolution[1])
        self.assets_dir = assets_dir
        self.duration_lookup = duration_lookup or audio_duration
        self.seed = seed
        self.warnings = []

    # -- helpers -----------------------------------------------------------
    def sec(self, s):
        return max(1, int(round(float(s) * self.fps)))

    def char_def(self, name):
        return self.s.characters.get(name)

    def height(self, name):
        c = self.char_def(name)
        return c.height if c else 3.4

    def color(self, name, aura=False):
        c = self.char_def(name)
        if not c:
            return (1.0, 0.8, 0.2)
        return parse_color(c.aura if (aura and c.aura) else c.color)

    def face(self, name):
        """Eye-line height as a fraction of the character's height."""
        c = self.char_def(name)
        if not c:
            return 0.8
        if c.face:
            return c.face
        has_art = (c.folder and os.path.isdir(self.resolve_path(c.folder))) or (
            c.sheet and os.path.exists(self.resolve_path(c.sheet)))
        if has_art:
            return 0.88
        return {"creature": 0.55, "object": 0.5}.get(c.kind, 0.73)

    def resolve_path(self, p):
        if not p:
            return ""
        if os.path.isabs(p):
            return p
        return os.path.normpath(os.path.join(self.assets_dir, p))

    # -- framing -----------------------------------------------------------
    def dist_for_height(self, h):
        return h * LENS / (SENSOR / self.aspect)

    def frame_pose(self, tl, kind, subjects, f):
        onstage = [n for n, t in tl.chars.items() if abs(t.x_at(f)) < OFFSTAGE_X - 1]
        subs = [s for s in subjects if s in tl.chars] or onstage
        if not subs:
            # Empty stage: show the location.
            H = 7.0
            return dict(x=0.0, z=H * 0.3, dist=self.dist_for_height(H))
        h = max(self.height(n) for n in subs)
        xs = [tl.chars[n].x_at(f) for n in subs]
        zs = [tl.chars[n].z_at(f) for n in subs]
        x = (min(xs) + max(xs)) / 2.0
        z0 = sum(zs) / len(zs)
        if kind in ("wide", "establish"):
            pool = onstage or subs
            pxs = [tl.chars[n].x_at(f) for n in pool]
            ph = max(self.height(n) for n in pool)
            width = (max(pxs) - min(pxs)) + ph * 1.5
            H = max(ph * 1.75, width / self.aspect)
            x = (min(pxs) + max(pxs)) / 2.0
            return dict(x=x, z=H / 2 - 0.1 * ph + z0, dist=self.dist_for_height(H))
        if kind == "two":
            width = (max(xs) - min(xs)) + h * 0.95
            H = max(width / self.aspect, h * 0.85)
            return dict(x=x, z=max(H / 2 - 0.05 * h, 1.04 * h - H / 2) + z0, dist=self.dist_for_height(H))
        face = self.face(subs[0]) * h
        lead = 0.0
        tr = tl.chars[subs[0]]
        if tr.facing_keys:
            facing = [d for k, d in tr.facing_keys if k <= f]
            if facing:
                lead = 0.06 * h * (1 if facing[-1] == "right" else -1)
        if kind == "full":
            H = h * 1.25
            return dict(x=x + lead, z=H / 2 - 0.06 * h + z0, dist=self.dist_for_height(H))
        if kind == "medium":
            H = h * 0.62
            return dict(x=x + lead, z=1.04 * h - H / 2 + z0, dist=self.dist_for_height(H))
        if kind == "closeup":
            H = h * 0.34
            return dict(x=x + lead * 0.5, z=face + 0.01 * h + z0, dist=self.dist_for_height(H))
        if kind == "extreme":
            H = h * 0.15
            return dict(x=x, z=face + 0.035 * h + z0, dist=self.dist_for_height(H))
        return self.frame_pose(tl, "medium", subjects, f)

    def add_shot(self, tl, start, end, kind, subjects=(), move="static", low=False, dutch=0.0,
                 follow=False, label=""):
        """Record a shot and the camera keys that realise it."""
        end = max(end, start + 2)
        subjects = [s for s in subjects if s]
        # A shot that starts inside the previous one cuts it short.
        while tl.cam_keys and tl.cam_keys[-1].frame >= start:
            tl.cam_keys.pop()
        if tl.cam_keys:
            k = tl.cam_keys[-1]
            tl.cam_keys[-1] = CamKey(k.frame, k.x, k.z, k.dist, k.roll, k.tilt, "CONSTANT")
        if tl.shots and tl.shots[-1].end > start:
            tl.shots[-1].end = start
        p0 = self.frame_pose(tl, kind, subjects, start)
        p1 = self.frame_pose(tl, kind, subjects, end - 1) if follow else dict(p0)
        tilt = 0.0
        if low:
            # Drop the camera and look up at the subject.
            tilt = 9.0
            for p in (p0, p1):
                p["z"] -= p["dist"] * math.tan(math.radians(tilt)) * 0.9
        roll0 = roll1 = dutch
        if move == "push":
            p1["dist"] *= 0.86
        elif move == "slowpush":
            p1["dist"] *= 0.93
        elif move == "pull":
            p1["dist"] *= 1.15
        elif move == "drift":
            p1["x"] += p0["dist"] * 0.025
        elif move == "pan":
            p0["x"] -= p0["dist"] * 0.18
        elif move == "zoom":
            p1["dist"] *= 0.55
        interp = "LINEAR" if move in ("zoom", "pan") else "BEZIER"
        if move == "zoom":
            snap = min(end - 1, start + 5)
            tl.cam_keys.append(CamKey(start, p0["x"], p0["z"], p0["dist"], roll0, tilt, "LINEAR"))
            tl.cam_keys.append(CamKey(snap, p1["x"], p1["z"], p1["dist"], roll1, tilt, "CONSTANT"))
        else:
            tl.cam_keys.append(CamKey(start, p0["x"], p0["z"], p0["dist"], roll0, tilt, interp))
            tl.cam_keys.append(CamKey(end - 1, p1["x"], p1["z"], p1["dist"], roll1, tilt, "CONSTANT"))
        tl.shots.append(Shot(start, end, kind, list(subjects), label or kind))

    # -- character state ----------------------------------------------------
    def set_x(self, tl, name, frame, x, interp="BEZIER", dur=0):
        tr = tl.chars[name]
        if dur:
            tr.x_keys.append((frame, tr.x_at(frame), interp))
            tr.x_keys.append((frame + dur, x, "CONSTANT"))
        else:
            tr.x_keys.append((frame, x, "CONSTANT"))
        tr.x_keys.sort(key=lambda k: k[0])

    def set_z(self, tl, name, frame, z, interp="BEZIER", dur=0):
        tr = tl.chars[name]
        if dur:
            tr.z_keys.append((frame, tr.z_at(frame), interp))
            tr.z_keys.append((frame + dur, z, "CONSTANT"))
        else:
            tr.z_keys.append((frame, z, "CONSTANT"))
        tr.z_keys.sort(key=lambda k: k[0])

    def set_expr(self, tl, name, frame, expr):
        if name in tl.chars and expr:
            tl.chars[name].expr_keys.append((frame, expr))

    def update_facing(self, tl, frame):
        for name, tr in tl.chars.items():
            x = tr.x_at(frame)
            others = [t.x_at(frame) for n, t in tl.chars.items()
                      if n != name and abs(t.x_at(frame)) < OFFSTAGE_X - 1]
            if others:
                nearest = min(others, key=lambda o: abs(o - x))
                want = "right" if nearest > x else "left"
            else:
                want = "right" if x < 0 else "left"
            if not tr.facing_keys or tr.facing_keys[-1][1] != want:
                tr.facing_keys.append((frame, want))

    def onstage(self, tl, f):
        return [n for n, t in tl.chars.items() if abs(t.x_at(f)) < OFFSTAGE_X - 1]

    def opponent(self, tl, name, f):
        others = [n for n in self.onstage(tl, f) if n != name]
        if not others:
            return None
        x = tl.chars[name].x_at(f) if name in tl.chars else 0
        return min(others, key=lambda o: abs(tl.chars[o].x_at(f) - x))

    def side(self, tl, name, f):
        """+1 if the character is on the right half of the stage."""
        return 1 if tl.chars[name].x_at(f) > 0 else -1

    def _slot_free(self, tl, f, x, who):
        return all(abs(t.x_at(f) - x) > 1.2 for n, t in tl.chars.items()
                   if n != who and abs(t.x_at(f)) < OFFSTAGE_X - 1)

    def free_slot(self, tl, f, prefer=None):
        taken = [t.x_at(f) for t in tl.chars.values() if abs(t.x_at(f)) < OFFSTAGE_X - 1]
        order = ["left", "right", "center", "farleft", "farright", "centerleft", "centerright"]
        if prefer == "left":
            order = ["left", "farleft", "centerleft", "center", "right", "farright", "centerright"]
        elif prefer == "right":
            order = ["right", "farright", "centerright", "center", "left", "farleft", "centerleft"]
        for s in order:
            if all(abs(SLOT_X[s] - t) > 1.2 for t in taken):
                return SLOT_X[s]
        return 0.0

    # -- main ----------------------------------------------------------------
    def plan(self):
        scenes = []
        for sd in self.s.scenes:
            scenes.append(self.plan_scene(sd))
        overlap = self.sec(0.4) if self.s.transition != "cut" else 0
        title_len = self.sec(3.0)
        end_len = self.sec(3.5)
        t = title_len - overlap
        for tl in scenes:
            tl.offset = t
            t += tl.length - overlap
        total = t + end_len
        return EpisodePlan(self.s.title, self.s.episode, self.fps, self.s.resolution, scenes,
                           title_len, end_len, self.s.next_text, overlap, total,
                           list(self.s.warnings) + self.warnings)

    def plan_scene(self, sd):
        rng = random.Random(self.seed * 1000 + sd.index)
        drama = self.s.drama
        tl = SceneTimeline(sd.index, f"S{sd.index:02d} {sd.location}", sd.location, sd.time or "day",
                           music=self.resolve_path(sd.music), transition=sd.transition or self.s.transition)

        # Cast: everyone who speaks or is named in a command, in order of appearance.
        cast, enters = [], set()
        for b in sd.beats:
            names = []
            if b.kind == "dialogue" and b.speaker in self.s.characters:
                names = [b.speaker]
            elif b.kind == "command":
                names = [a for a in b.args if a in self.s.characters]
                if b.command in ("ENTER",) and names and names[0] not in cast:
                    enters.add(names[0])
                if b.command == "SUMMON" and len(names) > 1 and names[1] not in cast:
                    enters.add(names[1])
            for n in names:
                if n not in cast:
                    cast.append(n)
        placed = {}
        for b in sd.beats:
            if b.kind == "command" and b.command == "PLACE" and b.args and b.args[0] in cast:
                slot = (b.args[1] if len(b.args) > 1 else "center").lower()
                placed[b.args[0]] = SLOT_X.get(slot, 0.0)
        # Fighters take the main left/right marks, everyone else stands to the side.
        fighters = []
        for b in sd.beats:
            if b.kind == "command" and b.command in ("DASH", "BEAM", "CLASH", "HIT"):
                for n in b.args:
                    if n in cast and n not in fighters and n not in placed:
                        fighters.append(n)
        others = [n for n in cast if n not in fighters and n not in placed]
        if fighters:
            fslots = {1: ["center"], 2: ["left", "right"], 3: ["left", "center", "right"]}.get(
                len(fighters), ["farleft", "left", "right", "farright"])
            oslots = ["farright", "farleft", "centerright", "centerleft"]
        else:
            fslots = []
            oslots = {1: ["center"], 2: ["left", "right"], 3: ["left", "center", "right"],
                      4: ["farleft", "left", "right", "farright"]}.get(len(others), [])
        # Which side each character arrives from, if the script says.
        pref = {}
        for b in sd.beats:
            if b.kind == "command" and b.command == "ENTER" and b.args and b.args[0] not in pref:
                frm = (b.options.get("from") or "").lower()
                slot = next((x.lower() for x in b.args[1:] if x.lower() in SLOT_X), None)
                if slot:
                    pref[b.args[0]] = "right" if SLOT_X[slot] > 0 else "left"
                elif frm in ("left", "right"):
                    pref[b.args[0]] = frm
        reserved = {}
        for group, slots in ((fighters, fslots), (others, oslots)):
            free = list(slots)
            for n in sorted(group, key=lambda n: n not in pref):
                if not free:
                    break
                side = pref.get(n)
                pick = next((sl for sl in free if side and (SLOT_X[sl] > 0) == (side == "right")
                             and SLOT_X[sl] != 0), free[0])
                free.remove(pick)
                reserved[n] = SLOT_X[pick]
        spread = [n for n in fighters + others if n not in reserved]
        for i, n in enumerate(spread):
            reserved[n] = -5.2 + 10.4 * i / max(1, len(spread) - 1)
        self._reserved = reserved
        for n in cast:
            if n not in enters and n not in placed:
                placed[n] = reserved[n]
        for i, n in enumerate(cast):
            tr = CharTrack(n, y=-0.12 * i)
            start_x = placed.get(n, -OFFSTAGE_X if i % 2 == 0 else OFFSTAGE_X)
            tr.x_keys.append((0, start_x, "CONSTANT"))
            tr.z_keys.append((0, 0.0, "CONSTANT"))
            tr.expr_keys.append((0, "neutral"))
            tl.chars[n] = tr
        self.update_facing(tl, 0)

        f = 0
        auras = {}          # name -> Fx currently on
        override = None     # framing forced by [SHOT ...] for the next beat
        last = {"speaker": None, "kind": None}
        spoken = set()
        dialogue_n = 0

        if self.s.establishing:
            d = self.sec(2.2)
            self.add_shot(tl, f, f + d, "establish", [], move="pan", label="establishing")
            f += d

        for b in sd.beats:
            if b.kind == "dialogue":
                dialogue_n += 1
                f = self.beat_dialogue(tl, sd, b, f, dialogue_n, rng, drama, override, last, spoken)
                override = None
            elif b.kind == "command":
                if b.command == "SHOT":
                    override = b
                    continue
                f = self.beat_command(tl, b, f, rng, drama, auras, override)
                override = None
                last = {"speaker": None, "kind": None}
            else:
                # Stage direction the director can't act out: a short hold.
                d = self.sec(min(2.5, 0.9 + len(b.text.split()) * 0.08))
                subs = self.s.mentions(b.text)
                self.add_shot(tl, f, f + d, "wide" if not subs else "full", subs, move="slowpush")
                f += d
                last = {"speaker": None, "kind": None}

        f += self.sec(0.6)
        if tl.shots:
            tl.shots[-1].end = f
            k = tl.cam_keys[-1]
            tl.cam_keys[-1] = CamKey(f - 1, k.x, k.z, k.dist, k.roll, k.tilt, "CONSTANT")
        tl.length = max(f, self.sec(1))
        for name, fx in auras.items():
            fx.end = tl.length
        if tl.music and os.path.exists(tl.music):
            tl.audio.append(AudioCue(0, tl.music, "music", 0.45, tl.length))
        elif tl.music:
            self.warnings.append(f"scene {sd.index}: music file not found: {tl.music}")
        return tl

    # -- dialogue ------------------------------------------------------------
    def beat_dialogue(self, tl, sd, b, f, n, rng, drama, override, last, spoken):
        speaker = b.speaker
        text = b.text
        voice = self.resolve_path(b.options.get("voice", ""))
        if not voice:
            for ext in (".wav", ".ogg", ".mp3", ".flac"):
                cand = self.resolve_path(os.path.join("voices", f"S{sd.index:02d}_{n:03d}{ext}"))
                if os.path.exists(cand):
                    voice = cand
                    break
        dur_s = None
        if voice and os.path.exists(voice):
            dur_s = self.duration_lookup(voice)
        elif voice:
            self.warnings.append(f"line {b.lineno}: voice file not found: {voice}")
            voice = ""
        words = len(text.split())
        speech_s = dur_s if dur_s else max(1.1, 0.35 + words / 2.7 + (0.25 if "!" in text else 0))
        speech = self.sec(speech_s)
        hold = self.sec(0.35)
        start, end = f, f + speech + hold

        is_char = speaker in tl.chars
        if is_char:
            tr = tl.chars[speaker]
            prev = tr.expr_at(f)
            expr = b.expr or ("neutral" if prev in TRANSIENT_EXPRESSIONS else prev)
            if not b.expr and drama >= 1 and text.rstrip().endswith("!") and words <= 6 and rng.random() < 0.5:
                expr = "shout"
            self.set_expr(tl, speaker, f, expr)
            tr.talk.append((f + 2, f + speech))
        else:
            expr = b.expr

        # Choose the framing like an anime storyboard would.
        low = False
        dutch = 0.0
        move = "slowpush"
        if override is not None:
            kind = (override.args[0].lower() if override.args else "medium")
            subj = [a for a in override.args[1:] if a in tl.chars] or ([speaker] if is_char else [])
            kind, low, dutch, move = self._shot_words(kind, override)
            self.add_shot(tl, start, end, kind, subj, move=move, low=low, dutch=dutch)
            last.update(speaker=speaker, kind=kind)
            spoken.add(speaker)
        elif not is_char:
            self.add_shot(tl, start, end, "wide", [], move="drift")
            last.update(speaker=None, kind="wide")
        else:
            intense = expr in ("shout", "angry") or (text.count("!") >= 2)
            if speaker not in spoken:
                kind = "medium" if len(tl.chars) > 1 else "full"
            elif intense:
                kind = "closeup"
                move = "push" if drama >= 1 else "slowpush"
                if drama >= 2 and expr == "shout":
                    low = rng.random() < 0.5
                    dutch = 0.0 if low else rng.choice([-7.0, 7.0])
            elif last["speaker"] != speaker and len(self.onstage(tl, f)) >= 2 and rng.random() < 0.22:
                kind = "two"
            else:
                kind = rng.choice(["medium", "closeup", "medium", "closeup", "full"])
            if speech_s > 4.0 and move == "slowpush":
                move = "push"
            subj = [speaker]
            if kind == "two":
                other = self.opponent(tl, speaker, f)
                subj = [speaker, other] if other else [speaker]
            # Same speaker, same framing: continue the shot instead of cutting.
            if last["speaker"] == speaker and last["kind"] == kind and tl.shots:
                tl.shots[-1].end = end
                k = tl.cam_keys[-1]
                tl.cam_keys[-1] = CamKey(end - 1, k.x, k.z, k.dist, k.roll, k.tilt, "CONSTANT")
            else:
                self.add_shot(tl, start, end, kind, subj, move=move, low=low, dutch=dutch)
            last.update(speaker=speaker, kind=kind)
            spoken.add(speaker)

        if self.s.subtitles:
            label = "" if speaker == "NARRATOR" else speaker
            tl.subtitles.append(Subtitle(start, f + speech + hold // 2, label, text,
                                         self.color(speaker) if is_char else (0.9, 0.9, 0.9)))
        if voice:
            tl.audio.append(AudioCue(start + 1, voice, "voice", 1.0))

        f = end
        # Reaction shot after an outburst.
        if is_char and drama >= 1 and (expr in ("shout", "angry") or text.endswith("?!")):
            other = self.opponent(tl, speaker, f)
            if other and rng.random() < (0.3 if drama == 1 else 0.55):
                d = self.sec(0.8)
                if drama >= 2 and expr == "shout":
                    self.set_expr(tl, other, f, "surprised")
                self.add_shot(tl, f, f + d, "closeup", [other], move="push")
                f += d
                last.update(speaker=None, kind=None)
        return f

    def _shot_words(self, kind, beat):
        low = "low" in kind or beat.options.get("angle") == "low" or "low" in [a.lower() for a in beat.args]
        dutch = 8.0 if ("dutch" in kind or "dutch" in [a.lower() for a in beat.args]) else 0.0
        move = beat.options.get("move", "slowpush")
        names = {"wide": "wide", "establish": "establish", "full": "full", "medium": "medium",
                 "closeup": "closeup", "close": "closeup", "cu": "closeup", "extreme": "extreme",
                 "eyes": "extreme", "ecu": "extreme", "two": "two", "twoshot": "two", "low": "full",
                 "dutch": "medium"}
        k = names.get(kind.replace("-", "").replace("_", ""), "medium")
        return k, low, dutch, move

    # -- commands ------------------------------------------------------------
    def beat_command(self, tl, b, f, rng, drama, auras, override):
        cmd = b.command
        args = [a for a in b.args]
        chars = [a for a in args if a in tl.chars]
        a = chars[0] if chars else None
        tgt = chars[1] if len(chars) > 1 else None
        secs = b.options.get("secs")
        color_opt = b.options.get("color")

        def dur(default):
            return self.sec(secs) if secs else self.sec(default)

        def sfx(kind, frame):
            for ext in (".wav", ".ogg", ".mp3", ".flac"):
                p = self.resolve_path(os.path.join("sfx", kind + ext))
                if os.path.exists(p):
                    tl.audio.append(AudioCue(frame, p, "sfx", 0.9))
                    return

        if cmd == "PLACE":
            if a:
                slot = (args[1].lower() if len(args) > 1 else "center")
                self.set_x(tl, a, f, SLOT_X.get(slot, 0.0))
                self.update_facing(tl, f)
            return f

        if cmd == "ENTER":
            if not a:
                return f
            frm = (b.options.get("from") or "").lower()
            slot_word = next((x.lower() for x in args[1:] if x.lower() in SLOT_X), None)
            if slot_word:
                x_to = SLOT_X[slot_word]
            elif a in getattr(self, "_reserved", {}):
                x_to = self._reserved[a]
                if not self._slot_free(tl, f, x_to, a):
                    x_to = self.free_slot(tl, f, prefer=frm if frm in ("left", "right") else None)
            else:
                x_to = self.free_slot(tl, f, prefer=frm if frm in ("left", "right") else None)
            d = dur(1.0)
            if frm in ("top", "sky", "above", "up"):
                self.set_x(tl, a, f, x_to)
                self.set_z(tl, a, f, FLY_Z)
                self.set_z(tl, a, f, 0.0, "LINEAR", dur=int(d * 0.7))
                tl.fx.append(Fx("shake", f + int(d * 0.7), f + int(d * 0.7) + self.sec(0.4), strength=0.6))
                tl.fx.append(Fx("impact", f + int(d * 0.7), f + int(d * 0.7) + self.sec(0.35), char=a,
                                color=(1, 1, 1), extra={"ground": True}))
                sfx("land", f + int(d * 0.7))
            else:
                side = -1 if (frm == "left" or (not frm and x_to <= 0)) else 1
                self.set_x(tl, a, f, side * OFFSTAGE_X)
                self.set_x(tl, a, f, x_to, "BEZIER", dur=int(d * 0.75))
                sfx("whoosh", f)
            self.update_facing(tl, f + d)
            self.set_expr(tl, a, f, "neutral" if tl.chars[a].expr_at(f) in TRANSIENT_EXPRESSIONS else "")
            self.add_shot(tl, f, f + d, "wide", [], follow=True, move="static")
            return f + d

        if cmd == "EXIT":
            if not a:
                return f
            to = (b.options.get("to") or b.options.get("from") or
                  next((x.lower() for x in args[1:] if x.lower() in DIRECTIONS), "")).lower()
            d = dur(0.9)
            if to in ("top", "up", "sky"):
                self.set_z(tl, a, f, FLY_Z * 1.5, "LINEAR", dur=int(d * 0.6))
                self.set_x(tl, a, f + int(d * 0.6) + 1, OFFSTAGE_X)
                self.set_z(tl, a, f + int(d * 0.6) + 2, 0.0)
                tl.fx.append(Fx("speedlines", f, f + d, color=(1, 1, 1)))
            else:
                side = (1 if to == "right" else -1) if to in ("left", "right") else self.side(tl, a, f)
                self.set_x(tl, a, f, side * OFFSTAGE_X, "BEZIER", dur=int(d * 0.8))
            sfx("whoosh", f)
            self.add_shot(tl, f, f + d, "wide", [], move="static")
            self.update_facing(tl, f + d)
            return f + d

        if cmd == "MOVE":
            if not a:
                return f
            slot = next((x.lower() for x in args[1:] if x.lower() in SLOT_X), None)
            if slot:
                x_to = SLOT_X[slot]
            elif tgt:
                x_to = tl.chars[tgt].x_at(f) - self.side(tl, tgt, f) * 1.6
            else:
                x_to = 0.0
            d = dur(0.8)
            self.set_x(tl, a, f, x_to, "BEZIER", dur=int(d * 0.8))
            self.update_facing(tl, f + d)
            self.add_shot(tl, f, f + d, "wide", [], follow=True)
            return f + d

        if cmd == "FACE":
            if not a:
                return f
            from .script_parser import normalize_expression
            expr = normalize_expression(args[1] if len(args) > 1 else "neutral")
            self.set_expr(tl, a, f, expr)
            d = dur(1.0)
            self.add_shot(tl, f, f + d, "closeup", [a], move="slowpush")
            return f + d

        if cmd in ("POWERUP", "TRANSFORM"):
            if not a:
                return f
            col = parse_color(color_opt) if color_opt else self.color(a, aura=True)
            d = dur(3.2 if cmd == "POWERUP" else 4.0)
            self.set_expr(tl, a, f, "shout")
            tl.chars[a].talk.append((f + self.sec(0.3), f + d - self.sec(0.4)))
            if cmd == "TRANSFORM":
                form = b.options.get("form") or (args[1].lower() if len(args) > 1 and args[1] not in tl.chars else "super")
                mid = f + int(d * 0.55)
                tl.chars[a].form_keys.append((mid, form))
                tl.fx.append(Fx("flash", mid - 2, mid + self.sec(0.5), strength=1.0))
                if not color_opt and form.startswith("super"):
                    col = parse_color("gold")
            # Low angle, aura erupting, rocks of camera shake, speed lines.
            half = int(d * 0.5)
            self.add_shot(tl, f, f + half, "full", [a], move="push", low=True)
            self.add_shot(tl, f + half, f + d, "closeup", [a], move="push",
                          dutch=rng.choice([-6.0, 6.0]) if drama >= 1 else 0.0)
            tl.fx.append(Fx("burst", f + half, f + d, char=a, color=col))
            tl.fx.append(Fx("speedlines", f + half, f + d, color=(1, 1, 1)))
            tl.fx.append(Fx("shake", f + int(d * 0.2), f + d, strength=0.5 + 0.3 * drama))
            tl.fx.append(Fx("debris", f, f + d, char=a, color=col))
            if a in auras:
                auras[a].end = f
            aura = Fx("aura", f, f + d, char=a, color=col, extra={"ramp": int(d * 0.4), "beat_end": f + d})
            tl.fx.append(aura)
            auras[a] = aura
            sfx("powerup", f)
            self.set_expr(tl, a, f + d, "angry")
            return f + d

        if cmd == "CALM":
            if a and a in auras:
                auras.pop(a).end = f + self.sec(0.4)
            return f

        if cmd == "BEAM":
            if not a:
                return f
            col = parse_color(color_opt) if color_opt else self.color(a, aura=True)
            name = b.options.get("name", "")
            if not tgt:
                # No target named: blast away from the nearest friend, into the distance.
                near = self.opponent(tl, a, f)
                away = "left" if near and tl.chars[near].x_at(f) > tl.chars[a].x_at(f) else "right"
                if not near:
                    away = "left" if tl.chars[a].x_at(f) > 0 else "right"
                tl.chars[a].facing_keys.append((f, away))
            charge, fire, hold = self.sec(1.5), self.sec(0.35), self.sec(1.0)
            if secs:
                charge = max(self.sec(0.6), self.sec(secs) - fire - hold)
            self.set_expr(tl, a, f, "shout")
            tl.chars[a].talk.append((f + 3, f + charge + fire))
            self.add_shot(tl, f, f + charge, "medium", [a], move="push", low=drama >= 1)
            tl.fx.append(Fx("charge", f, f + charge + fire + hold, char=a, color=col,
                            extra={"target": tgt or ""}))
            tl.fx.append(Fx("speedlines", f + charge // 2, f + charge, color=col))
            sfx("charge", f)
            if name:
                tl.captions.append((f + charge // 3, f + charge, name.upper()))
            g = f + charge
            subj = [a, tgt] if tgt else [a]
            self.add_shot(tl, g, g + fire + hold, "two" if tgt else "full", subj, move="pull")
            tl.fx.append(Fx("beam", g, g + fire + hold, char=a, target=tgt or "", color=col,
                            extra={"grow": fire}))
            tl.fx.append(Fx("shake", g, g + fire + hold, strength=0.4))
            sfx("beam", g)
            f = g + fire + hold
            if tgt:
                f = self.hit(tl, tgt, a, g + fire + self.sec(0.3), f, col, rng, drama, sfx)
            return f

        if cmd in ("DASH",):
            if not a:
                return f
            tgt = tgt or self.opponent(tl, a, f)
            d_run = self.sec(0.3)
            self.add_shot(tl, f, f + d_run + self.sec(0.15), "two" if tgt else "full",
                          [a, tgt] if tgt else [a], move="static")
            tl.fx.append(Fx("speedlines", f, f + d_run + self.sec(0.15), color=(1, 1, 1)))
            sfx("whoosh", f)
            self.set_expr(tl, a, f, "shout")
            if tgt:
                side = 1 if tl.chars[tgt].x_at(f) > tl.chars[a].x_at(f) else -1
                contact = tl.chars[tgt].x_at(f) - side * self.height(tgt) * 0.42
                self.set_x(tl, a, f, contact, "LINEAR", dur=d_run)
                f2 = f + d_run
                end = f2 + self.sec(1.0)
                home = tl.chars[a].x_at(f)
                f = self.hit(tl, tgt, a, f2, end, (1, 1, 1), rng, drama, sfx)
                # Leap back to their mark so the staging stays readable.
                self.set_x(tl, a, f - self.sec(0.45), home, "BEZIER", dur=self.sec(0.3))
                self.update_facing(tl, f)
                self.set_expr(tl, a, f, "angry")
                return f
            x = tl.chars[a].x_at(f) + (1.5 if self.side(tl, a, f) < 0 else -1.5)
            self.set_x(tl, a, f, x, "LINEAR", dur=d_run)
            return f + d_run + self.sec(0.4)

        if cmd == "HIT":
            target = a or (self.onstage(tl, f) or [None])[0]
            if not target:
                return f
            attacker = tgt or self.opponent(tl, target, f)
            return self.hit(tl, target, attacker, f, f + dur(1.0), (1, 1, 1), rng, drama, sfx)

        if cmd == "SPIN":
            if not a:
                return f
            d = dur(2.5)
            col = parse_color(color_opt) if color_opt else self.color(a, aura=True)
            c = self.char_def(a)
            obj = c.kind == "object" if c else False
            turns = (3.0 if obj else 1.5) * d / float(self.fps)
            tr = tl.chars[a]
            tr.spin_keys.append((f, 0.0, "LINEAR"))
            tr.spin_keys.append((f + d, turns * 2 * math.pi, "CONSTANT"))
            tr.spin_keys.append((f + d + 1, 0.0, "CONSTANT"))
            self.add_shot(tl, f, f + d // 2, "full", [a], move="push", low=True)
            self.add_shot(tl, f + d // 2, f + d, "full" if obj else "closeup", [a], move="push")
            tl.fx.append(Fx("speedlines", f, f + d, color=(1, 1, 1)))
            tl.fx.append(Fx("aura", f, f + d, char=a, color=col, strength=0.8, extra={"ramp": self.sec(0.3)}))
            sfx("spin", f)
            return f + d

        if cmd == "CLASH":
            if not a:
                return f
            tgt = tgt or self.opponent(tl, a, f)
            if not tgt:
                return f
            d_run = self.sec(0.35)
            xa, xb = tl.chars[a].x_at(f), tl.chars[tgt].x_at(f)
            mid = (xa + xb) / 2.0
            gap = 0.42 * (self.height(a) + self.height(tgt)) / 2.0
            sa = 1 if xa < xb else -1
            self.set_x(tl, a, f, mid - sa * gap, "LINEAR", dur=d_run)
            self.set_x(tl, tgt, f, mid + sa * gap, "LINEAR", dur=d_run)
            self.set_expr(tl, a, f, "shout")
            self.set_expr(tl, tgt, f, "shout")
            hit = f + d_run
            end = hit + dur(1.6)
            self.add_shot(tl, f, hit, "two", [a, tgt], move="push")
            self.add_shot(tl, hit, end, "two", [a, tgt], move="pull", dutch=6.0 if drama else 0.0)
            tl.fx.append(Fx("speedlines", f, hit, color=(1, 1, 1)))
            for i in range(3):
                h = hit + i * self.sec(0.4)
                if h < end:
                    tl.fx.append(Fx("impact", h, h + self.sec(0.3), char=a, target=tgt,
                                    color=self.color(a, True) if i % 2 == 0 else self.color(tgt, True),
                                    extra={"between": True}))
            tl.fx.append(Fx("flash", hit, hit + self.sec(0.3)))
            tl.fx.append(Fx("shake", hit, end, strength=0.8))
            sfx("clash", hit)
            # Both recoil.
            self.set_x(tl, a, end - self.sec(0.4), mid - sa * (gap + 1.2), "BEZIER", dur=self.sec(0.3))
            self.set_x(tl, tgt, end - self.sec(0.4), mid + sa * (gap + 1.2), "BEZIER", dur=self.sec(0.3))
            self.set_expr(tl, a, end, "angry")
            self.set_expr(tl, tgt, end, "angry")
            return end

        if cmd == "SUMMON":
            if not a:
                return f
            creature = tgt
            d = dur(2.2)
            self.set_expr(tl, a, f, "shout")
            tl.chars[a].talk.append((f + 2, f + self.sec(0.8)))
            self.add_shot(tl, f, f + self.sec(0.9), "medium", [a], move="push")
            g = f + self.sec(0.9)
            if creature:
                side = 1 if tl.chars[a].x_at(f) < 0 else -1
                x_to = tl.chars[a].x_at(f) + side * 2.6
                if abs(x_to) > 5.5:
                    x_to = self.free_slot(tl, g)
                self.set_x(tl, creature, g + self.sec(0.2), x_to)
                tl.fx.append(Fx("summon", g, g + self.sec(0.9), char=creature, color=(1, 1, 1)))
                tl.fx.append(Fx("flash", g + self.sec(0.15), g + self.sec(0.5)))
                self.update_facing(tl, g + self.sec(0.2))
                self.add_shot(tl, g, f + d, "wide", [], move="push")
                self.set_expr(tl, creature, g + self.sec(0.4), "shout")
                tl.chars[creature].talk.append((g + self.sec(0.4), g + self.sec(1.0)))
                sfx("summon", g)
            self.set_expr(tl, a, f + d, "smirk")
            return f + d

        if cmd == "EXPLOSION":
            d = dur(1.6)
            where = a
            self.add_shot(tl, f, f + d, "wide", [], move="pull", dutch=5.0 if drama else 0.0)
            tl.fx.append(Fx("flash", f, f + self.sec(0.45)))
            tl.fx.append(Fx("explosion", f, f + d, char=where or "", color=parse_color(color_opt or "orange")))
            tl.fx.append(Fx("shake", f, f + d, strength=1.0))
            sfx("explosion", f)
            for n in self.onstage(tl, f):
                self.set_expr(tl, n, f + 2, "surprised")
            return f + d

        if cmd == "SHAKE":
            d = dur(1.0)
            tl.fx.append(Fx("shake", f, f + d, strength=float(b.options.get("strength", 0.7))))
            self.add_shot(tl, f, f + d, "wide", chars, move="static")
            return f + d

        if cmd == "FLASH":
            d = dur(0.4)
            tl.fx.append(Fx("flash", f, f + d))
            return f

        if cmd == "SPEEDLINES":
            d = dur(1.2)
            tl.fx.append(Fx("speedlines", f, f + d, color=parse_color(color_opt or "white")))
            self.add_shot(tl, f, f + d, "closeup" if a else "wide", chars, move="push")
            return f + d

        if cmd == "ZOOM":
            if not a:
                return f
            d = dur(1.0)
            self.add_shot(tl, f, f + d, "extreme", [a], move="zoom")
            tl.fx.append(Fx("speedlines", f, f + d, color=(1, 1, 1)))
            sfx("zoom", f)
            return f + d

        if cmd == "WAIT":
            d = dur(1.0)
            self.add_shot(tl, f, f + d, override.args[0].lower() if override and override.args else "wide",
                          chars, move="drift")
            return f + d

        if cmd == "SFX":
            if args:
                p = self.resolve_path(args[0])
                if os.path.exists(p):
                    tl.audio.append(AudioCue(f, p, "sfx", float(b.options.get("volume", 1.0))))
                else:
                    self.warnings.append(f"line {b.lineno}: sound not found: {p}")
            return f

        if cmd == "CAPTION":
            d = dur(2.0)
            text = " ".join(args) or b.text
            tl.captions.append((f, f + d, text))
            return f

        return f

    def hit(self, tl, target, attacker, f, end, col, rng, drama, sfx):
        """Impact on target at frame f; returns the frame the beat ends."""
        end = max(end, f + self.sec(1.2))
        side = 1
        if attacker and attacker in tl.chars:
            side = 1 if tl.chars[target].x_at(f) >= tl.chars[attacker].x_at(f) else -1
        self.add_shot(tl, f, f + self.sec(0.45), "medium", [target], move="static", follow=True,
                      dutch=rng.choice([-9.0, 9.0]) if drama >= 1 else 0.0)
        self.add_shot(tl, f + self.sec(0.45), end, "wide", [], move="static", follow=True)
        tl.fx.append(Fx("impact", f, f + self.sec(0.4), char=target, target=attacker or "",
                        color=(1.0, 0.72, 0.15)))
        tl.fx.append(Fx("flash", f, f + self.sec(0.2), strength=0.8))
        tl.fx.append(Fx("shake", f, f + self.sec(0.6), strength=0.9))
        tl.fx.append(Fx("hitflash", f, f + self.sec(0.25), char=target))
        sfx("hit", f)
        self.set_expr(tl, target, f, "hurt")
        x = tl.chars[target].x_at(f)
        knock = max(-6.0, min(6.0, x + side * 1.4))
        self.set_x(tl, target, f, knock, "BEZIER", dur=self.sec(0.35))
        self.update_facing(tl, f + self.sec(0.4))
        return end


DIRECTIONS = {"left", "right", "top", "up", "sky"}


def plan_script(script, assets_dir="", duration_lookup=None):
    return Director(script, assets_dir, duration_lookup).plan()
