"""Parse Anime Studio story scripts into a structured model.

Pure Python (no bpy) so it can be tested outside Blender.

The format is forgiving. A script has a header (title, characters,
locations) followed by scenes. Inside a scene every line is one of:

    KAI (angry): You'll pay for that!      dialogue, optional expression
    [POWERUP Kai color=gold 3s]            explicit command
    Kai powers up and screams.             plain action, auto-interpreted
    MUSIC: music/battle.mp3                scene music

Screenplay style (a character name alone in capitals, followed by the
lines they speak) and INT./EXT. scene headings are accepted as well.
"""

import re
from dataclasses import dataclass, field

EXPRESSION_ALIASES = {
    "neutral": "neutral", "normal": "neutral", "calm": "neutral", "serious": "neutral",
    "happy": "happy", "smile": "happy", "smiling": "happy", "laugh": "happy",
    "laughing": "happy", "grin": "happy", "excited": "happy", "joy": "happy",
    "angry": "angry", "mad": "angry", "furious": "angry", "rage": "angry",
    "glare": "angry", "determined": "angry",
    "shout": "shout", "shouting": "shout", "yell": "shout", "yelling": "shout",
    "scream": "shout", "screaming": "shout", "battle cry": "shout",
    "sad": "sad", "cry": "sad", "crying": "sad", "tears": "sad", "upset": "sad",
    "surprised": "surprised", "surprise": "surprised", "shock": "surprised",
    "shocked": "surprised", "gasp": "surprised", "scared": "surprised", "afraid": "surprised",
    "hurt": "hurt", "pain": "hurt", "injured": "hurt", "wounded": "hurt",
    "smirk": "smirk", "confident": "smirk", "cocky": "smirk", "evil": "smirk",
}

# Expressions that wear off after the beat they were used in.
TRANSIENT_EXPRESSIONS = {"shout", "surprised", "hurt"}

COMMANDS = {
    "ENTER", "EXIT", "MOVE", "PLACE", "FACE", "POWERUP", "CALM", "TRANSFORM",
    "BEAM", "DASH", "ATTACK", "HIT", "SPIN", "CLASH", "SUMMON", "EXPLOSION",
    "SHAKE", "FLASH", "SPEEDLINES", "ZOOM", "SHOT", "WAIT", "SFX", "CAPTION",
}
COMMAND_ALIASES = {
    "ATTACK": "DASH", "PUNCH": "DASH", "KICK": "DASH", "RUSH": "DASH",
    "BLAST": "BEAM", "FIRE": "BEAM", "KAMEHAMEHA": "BEAM",
    "LAUNCH": "SPIN", "LET IT RIP": "SPIN", "CHARGE": "POWERUP",
    "AURA": "POWERUP", "PAUSE": "WAIT", "HOLD": "WAIT", "EXPRESSION": "FACE",
    "BOOM": "EXPLOSION", "IMPACT": "HIT", "LEAVE": "EXIT", "APPEAR": "ENTER",
    "CAMERA": "SHOT", "TEXT": "CAPTION",
}

SLOT_NAMES = {"farleft", "left", "centerleft", "center", "centerright", "right", "farright"}
DIRECTION_WORDS = {"left", "right", "top", "up", "sky", "above", "down"}

HEADER_KEYS = {"TITLE", "EPISODE", "FPS", "RESOLUTION", "NEXT", "STYLE", "DRAMA",
               "VOICES", "TRANSITION", "ESTABLISHING", "SUBTITLES"}
RESERVED_SPEAKERS = {"NARRATOR", "NARRATION", "VOICE", "ANNOUNCER"}


@dataclass
class CharacterDef:
    name: str
    aliases: list = field(default_factory=list)
    folder: str = ""          # folder of expression images (neutral.png, angry.png, ...)
    sheet: str = ""           # or one sheet image cut into a grid
    grid: tuple = (1, 1)      # columns, rows of the sheet
    cells: list = field(default_factory=list)  # expression name per grid cell
    color: str = "#ff8c00"
    aura: str = ""            # aura colour, defaults to color
    height: float = 3.4       # world units, feet to top of hair
    face: float = 0.0         # eye line as a fraction of height (0 = automatic)
    faces: str = "front"      # direction the artwork looks: front, left, right
    key: str = ""             # colour to key out when art has no transparency
    voice: str = ""           # text-to-speech voice hint, e.g. "male" / "female"
    pitch: int = 50           # 0-99 text-to-speech pitch
    kind: str = "fighter"     # fighter, creature or object (spinning tops etc.)
    lineno: int = 0


@dataclass
class LocationDef:
    name: str
    image: str = ""
    time: str = "day"         # day, night, sunset
    ground: float = 0.2       # where feet stand, as a fraction of image height
    lineno: int = 0


@dataclass
class Beat:
    kind: str                 # "dialogue", "command" or "note"
    lineno: int = 0
    speaker: str = ""
    expr: str = ""
    text: str = ""
    command: str = ""
    args: list = field(default_factory=list)
    options: dict = field(default_factory=dict)
    source: str = ""


@dataclass
class SceneDef:
    index: int
    location: str
    time: str = ""
    heading: str = ""
    music: str = ""
    transition: str = ""
    beats: list = field(default_factory=list)
    lineno: int = 0


@dataclass
class Script:
    title: str = "Untitled Episode"
    episode: str = ""
    fps: int = 24
    resolution: tuple = (1920, 1080)
    next_text: str = ""
    drama: int = 1            # 0 calm, 1 normal, 2 hype
    voices: bool = False
    transition: str = "wipe"
    establishing: bool = True
    subtitles: bool = True
    characters: dict = field(default_factory=dict)
    locations: dict = field(default_factory=dict)
    scenes: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    def find_character(self, token):
        """Return the canonical character name for a token, or None."""
        if not token:
            return None
        t = _norm(token)
        for name, c in self.characters.items():
            if _norm(name) == t or any(_norm(a) == t for a in c.aliases):
                return name
        return None

    def find_location(self, token):
        t = _norm(token)
        for name in self.locations:
            if _norm(name) == t:
                return name
        for name in self.locations:
            if t and (t in _norm(name) or _norm(name) in t):
                return name
        return None

    def mentions(self, text):
        """Characters named in free text, in order of first appearance."""
        found = []
        low = text.lower()
        for name, c in self.characters.items():
            best = None
            for n in [name] + list(c.aliases):
                m = re.search(r"(?<![\w])" + re.escape(n.lower()) + r"(?![\w])", low)
                if m and (best is None or m.start() < best):
                    best = m.start()
            if best is not None:
                found.append((best, name))
        return [n for _, n in sorted(found)]


def _norm(s):
    return re.sub(r"[^a-z0-9]+", "", str(s).lower())


def normalize_expression(word):
    if not word:
        return ""
    w = word.strip().lower()
    if w in EXPRESSION_ALIASES:
        return EXPRESSION_ALIASES[w]
    for k, v in EXPRESSION_ALIASES.items():
        if k in w:
            return v
    return re.sub(r"[^a-z0-9_]+", "_", w).strip("_")


def parse_color(value, default=(1.0, 0.55, 0.0)):
    """'#ff8800', 'ff8800', 'gold', or '1,0.5,0' -> linear-ish RGB floats 0..1."""
    if isinstance(value, (tuple, list)):
        return tuple(float(v) for v in value[:3])
    v = str(value or "").strip().lower()
    if not v:
        return default
    if v in NAMED_COLORS:
        return NAMED_COLORS[v]
    h = v.lstrip("#")
    if re.fullmatch(r"[0-9a-f]{6}", h):
        return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    if re.fullmatch(r"[0-9a-f]{3}", h):
        return tuple(int(c * 2, 16) / 255.0 for c in h)
    parts = re.split(r"[,\s]+", v)
    try:
        nums = [float(p) for p in parts if p]
        if len(nums) >= 3:
            if max(nums) > 1.0:
                nums = [n / 255.0 for n in nums]
            return tuple(nums[:3])
    except ValueError:
        pass
    return default


NAMED_COLORS = {
    "gold": (1.0, 0.82, 0.15), "yellow": (1.0, 0.92, 0.2), "orange": (1.0, 0.5, 0.05),
    "red": (1.0, 0.12, 0.08), "crimson": (0.85, 0.05, 0.15), "pink": (1.0, 0.4, 0.75),
    "purple": (0.6, 0.2, 1.0), "violet": (0.55, 0.25, 0.95), "blue": (0.15, 0.5, 1.0),
    "cyan": (0.2, 0.95, 1.0), "teal": (0.1, 0.8, 0.7), "green": (0.2, 0.95, 0.3),
    "white": (1.0, 1.0, 1.0), "black": (0.0, 0.0, 0.0), "silver": (0.8, 0.85, 0.9),
    "dark": (0.25, 0.05, 0.35),
}


_KV_RE = re.compile(r"^\s*([A-Za-z_][\w ]*?)\s*[:=]\s*(.*?)\s*$")
_SCENE_RE = re.compile(r"^\s*SCENE\b\s*\d*\s*[:.\-]?\s*(.*)$", re.I)
_SLUG_RE = re.compile(r"^\s*(INT|EXT|INT\./EXT|I/E)\.?\s+(.*)$", re.I)
_DIALOGUE_RE = re.compile(r"^\s*([^\[\]:(){}]{1,40}?)\s*(?:\(([^)]*)\))?\s*:\s*(.+)$")
_PAREN_ONLY_RE = re.compile(r"^\s*\(([^)]*)\)\s*$")
_BRACKET_RE = re.compile(r"\[([^\]]+)\]")
_CURLY_RE = re.compile(r"\{\s*(\w+)\s*[:=]\s*([^}]*)\}")


def parse(text):
    """Parse script text into a Script. Never raises on bad input; problems
    are collected in script.warnings / script.errors with line numbers."""
    script = Script()
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    block = None          # current CharacterDef / LocationDef being filled
    scene = None
    screenplay_speaker = None
    screenplay_expr = ""

    for lineno, raw in enumerate(lines, 1):
        line = _strip_comment(raw)
        if not line.strip():
            screenplay_speaker = None
            screenplay_expr = ""
            if block is not None and scene is None:
                block = None
            continue

        indented = raw[:1] in (" ", "\t")
        stripped = line.strip()
        upper = stripped.upper()

        # Definition blocks -------------------------------------------------
        m = re.match(r"^(CHARACTER|LOCATION|SET|PLACE)\s*:\s*(.+)$", stripped, re.I)
        if m and not (scene is not None and m.group(1).upper() == "PLACE"):
            kind = m.group(1).upper()
            parts = [p.strip() for p in m.group(2).split("|")]
            name = parts[0]
            if kind == "CHARACTER":
                block = CharacterDef(name=name, lineno=lineno)
                if script.find_character(name):
                    script.warnings.append(f"line {lineno}: character '{name}' defined twice")
                script.characters[name] = block
            else:
                block = LocationDef(name=name, lineno=lineno)
                script.locations[name] = block
            for p in parts[1:]:
                kv = _KV_RE.match(p)
                if kv:
                    _set_prop(script, block, kv.group(1), kv.group(2), lineno)
            screenplay_speaker = None
            continue

        if block is not None and indented:
            kv = _KV_RE.match(stripped)
            if kv:
                _set_prop(script, block, kv.group(1), kv.group(2), lineno)
                continue
        if block is not None and not indented:
            kv = _KV_RE.match(stripped)
            if kv and _norm(kv.group(1)) in _PROP_KEYS and scene is None:
                _set_prop(script, block, kv.group(1), kv.group(2), lineno)
                continue
            block = None

        # Scene headings ----------------------------------------------------
        sm = _SCENE_RE.match(stripped)
        slug = _SLUG_RE.match(stripped)
        if sm or slug:
            heading = (sm.group(1) if sm else slug.group(2)).strip()
            loc, time = _split_heading(heading)
            scene = SceneDef(index=len(script.scenes) + 1, location=loc, time=time,
                             heading=stripped, lineno=lineno)
            script.scenes.append(scene)
            screenplay_speaker = None
            continue

        # Header keys (before first scene; TRANSITION also allowed in scenes)
        kv = _KV_RE.match(stripped)
        if kv and kv.group(1).strip().upper() in HEADER_KEYS and (scene is None or kv.group(1).strip().upper() == "TRANSITION"):
            key, val = kv.group(1).strip().upper(), kv.group(2).strip()
            if scene is not None:
                scene.transition = val.lower()
            else:
                _set_header(script, key, val, lineno)
            continue

        if scene is None:
            if stripped:
                script.warnings.append(f"line {lineno}: ignored before first SCENE: {stripped[:60]}")
            continue

        if kv and kv.group(1).strip().upper() == "MUSIC":
            scene.music = kv.group(2).strip()
            continue

        # Screenplay parenthetical / continuation --------------------------
        if screenplay_speaker:
            pm = _PAREN_ONLY_RE.match(stripped)
            if pm:
                screenplay_expr = normalize_expression(pm.group(1))
                continue
            if not _BRACKET_RE.match(stripped) and not _DIALOGUE_RE.match(stripped):
                _add_dialogue(script, scene, screenplay_speaker, screenplay_expr, stripped, lineno, raw)
                screenplay_expr = ""
                continue
            screenplay_speaker = None

        # Bracket commands --------------------------------------------------
        if stripped.startswith("["):
            rest = stripped
            for bm in _BRACKET_RE.finditer(stripped):
                beat = _parse_command(script, bm.group(1), lineno, raw)
                if beat:
                    scene.beats.append(beat)
            rest = _BRACKET_RE.sub("", stripped).strip()
            if rest:
                _add_action(script, scene, rest, lineno, raw)
            continue

        # Screenplay name line: a known name alone in capitals --------------
        name_only = re.match(r"^([A-Z0-9][A-Z0-9 .'\-]{0,30})(?:\s*\(([^)]*)\))?$", stripped)
        if name_only and stripped == stripped.upper():
            who = script.find_character(name_only.group(1)) or (
                "NARRATOR" if name_only.group(1).strip() in RESERVED_SPEAKERS else None)
            if who:
                screenplay_speaker = who
                screenplay_expr = normalize_expression(name_only.group(2) or "")
                continue

        # Dialogue ----------------------------------------------------------
        dm = _DIALOGUE_RE.match(stripped)
        if dm:
            who_raw = dm.group(1).strip()
            who = script.find_character(who_raw)
            if not who and who_raw.upper() in RESERVED_SPEAKERS:
                who = "NARRATOR"
            if not who and _looks_like_name(who_raw):
                who = who_raw.strip().title() if who_raw.isupper() else who_raw.strip()
                script.characters[who] = CharacterDef(name=who, lineno=lineno)
                script.warnings.append(
                    f"line {lineno}: '{who_raw}' was not defined as a CHARACTER; using placeholder art")
            if who:
                _add_dialogue(script, scene, who, normalize_expression(dm.group(2) or ""),
                              dm.group(3), lineno, raw)
                continue

        _add_action(script, scene, stripped, lineno, raw)

    _validate(script)
    return script


def _strip_comment(line):
    s = line
    if s.lstrip().startswith("#") or s.lstrip().startswith("//"):
        return ""
    idx = s.find(" //")
    if idx >= 0:
        s = s[:idx]
    # Trailing "  # comment" (a hash followed by a space, so colours like #ff8800 survive).
    s = re.sub(r"\s+#\s.*$", "", s)
    return s.rstrip()


_PROP_KEYS = {"folder", "images", "sheet", "grid", "cells", "expressions", "color", "colour",
              "aura", "height", "face", "faces", "facing", "key", "transparent", "voice",
              "pitch", "kind", "type", "alias", "aliases", "image", "background",
              "time", "ground"}


def _set_prop(script, block, key, value, lineno):
    k = _norm(key)
    v = value.strip()
    try:
        if isinstance(block, CharacterDef):
            if k in ("folder", "images"):
                block.folder = v
            elif k == "sheet":
                block.sheet = v
            elif k == "grid":
                m = re.match(r"(\d+)\s*[x,*]\s*(\d+)", v)
                block.grid = (int(m.group(1)), int(m.group(2))) if m else (1, 1)
            elif k in ("cells", "expressions"):
                block.cells = [normalize_expression(c) for c in re.split(r"[,\s]+", v) if c]
            elif k in ("color", "colour"):
                block.color = v
            elif k == "aura":
                block.aura = v
            elif k == "height":
                block.height = max(0.3, float(v))
            elif k == "face":
                block.face = min(1.0, max(0.1, float(v)))
            elif k in ("faces", "facing"):
                block.faces = v.lower()
            elif k in ("key", "transparent"):
                block.key = v
            elif k == "voice":
                block.voice = v
            elif k == "pitch":
                block.pitch = int(float(v))
            elif k in ("kind", "type"):
                block.kind = v.lower()
            elif k in ("alias", "aliases"):
                block.aliases = [a.strip() for a in v.split(",") if a.strip()]
            else:
                script.warnings.append(f"line {lineno}: unknown character property '{key}'")
        else:
            if k in ("image", "background"):
                block.image = v
            elif k == "time":
                block.time = v.lower()
            elif k == "ground":
                block.ground = min(0.9, max(0.0, float(v)))
            else:
                script.warnings.append(f"line {lineno}: unknown location property '{key}'")
    except ValueError:
        script.warnings.append(f"line {lineno}: bad value '{v}' for '{key}'")


def _set_header(script, key, val, lineno):
    try:
        if key == "TITLE":
            script.title = val
        elif key == "EPISODE":
            script.episode = val
        elif key == "FPS":
            script.fps = max(6, min(120, int(float(val))))
        elif key == "RESOLUTION":
            m = re.match(r"(\d+)\s*[x*,]\s*(\d+)", val)
            if m:
                script.resolution = (int(m.group(1)), int(m.group(2)))
            elif val.lower() in ("4k", "2160p"):
                script.resolution = (3840, 2160)
            elif val.lower() in ("720p", "hd"):
                script.resolution = (1280, 720)
            else:
                script.resolution = (1920, 1080)
        elif key == "NEXT":
            script.next_text = val
        elif key in ("STYLE", "DRAMA"):
            levels = {"calm": 0, "normal": 1, "hype": 2, "epic": 2, "low": 0, "high": 2}
            lv = levels.get(val.lower())
            script.drama = lv if lv is not None else max(0, min(2, int(float(val))))
        elif key == "VOICES":
            script.voices = val.lower() in ("on", "yes", "true", "1", "tts")
        elif key == "TRANSITION":
            script.transition = val.lower()
        elif key == "ESTABLISHING":
            script.establishing = val.lower() not in ("off", "no", "false", "0")
        elif key == "SUBTITLES":
            script.subtitles = val.lower() not in ("off", "no", "false", "0")
    except ValueError:
        script.warnings.append(f"line {lineno}: bad value '{val}' for {key}")


def _split_heading(heading):
    time = ""
    m = re.match(r"^(.*?)\s*[-–—,]\s*(DAY|NIGHT|SUNSET|DUSK|DAWN|EVENING|MORNING|NOON|CONTINUOUS)\s*$",
                 heading, re.I)
    if m:
        heading, time = m.group(1), m.group(2).lower()
        time = {"dusk": "sunset", "evening": "sunset", "dawn": "sunset",
                "morning": "day", "noon": "day", "continuous": ""}.get(time, time)
    return heading.strip(" .-:"), time


def _looks_like_name(s):
    words = s.split()
    if not 1 <= len(words) <= 3:
        return False
    if s.upper() in ("NOTE", "NOTES", "SFX", "FX", "TODO", "MUSIC", "CUT TO", "FADE IN", "FADE OUT"):
        return False
    return all(w[:1].isalpha() and (w[:1].isupper()) for w in words)


def _extract_options(text):
    opts = {}
    for m in _CURLY_RE.finditer(text):
        opts[m.group(1).lower()] = m.group(2).strip()
    return _CURLY_RE.sub("", text).strip(), opts


def _add_dialogue(script, scene, speaker, expr, text, lineno, raw):
    text, opts = _extract_options(text)
    if not text:
        return
    scene.beats.append(Beat(kind="dialogue", lineno=lineno, speaker=speaker, expr=expr,
                            text=text, options=opts, source=raw.strip()))


def _parse_command(script, body, lineno, raw):
    tokens = re.findall(r'\w+="[^"]*"|"[^"]*"|\S+', body.strip())
    if not tokens:
        return None
    word = tokens[0].upper()
    rest = tokens[1:]
    if len(tokens) >= 3 and " ".join(t.upper() for t in tokens[:3]) == "LET IT RIP":
        word, rest = "SPIN", tokens[3:]
    word = COMMAND_ALIASES.get(word, word)
    if word not in COMMANDS:
        script.warnings.append(f"line {lineno}: unknown command [{body.strip()}]")
        return Beat(kind="note", lineno=lineno, text=body.strip(), source=raw.strip())
    args, opts = [], {}
    rest = _join_names(script, rest)
    for t in rest:
        if t in ("->", "=>", "→"):
            continue
        if t.startswith('"') and t.endswith('"') and len(t) >= 2:
            args.append(t[1:-1])
            continue
        kv = re.match(r"^(\w+)=(.+)$", t)
        if kv:
            opts[kv.group(1).lower()] = kv.group(2).strip('"')
            continue
        sec = re.match(r"^(\d+(?:\.\d+)?)s(?:ec(?:onds?)?)?$", t, re.I)
        if sec:
            opts["secs"] = sec.group(1)
            continue
        low = t.lower()
        if low in ("at", "to", "from", "on", "into", "and", "vs", "with"):
            if low == "from":
                opts["_from_next"] = True
            continue
        if opts.pop("_from_next", False):
            opts["from"] = low
            continue
        who = script.find_character(t)
        args.append(who if who else t)
    opts.pop("_from_next", None)
    return Beat(kind="command", lineno=lineno, command=word, args=args, options=opts,
                source=raw.strip())


def _join_names(script, tokens):
    """Merge tokens that together spell a multi-word character name."""
    out, i = [], 0
    while i < len(tokens):
        for n in (3, 2):
            if i + n <= len(tokens):
                who = script.find_character(" ".join(tokens[i:i + n]))
                if who:
                    out.append(who)
                    i += n
                    break
        else:
            out.append(tokens[i])
            i += 1
    return out


# Plain-language action lines ---------------------------------------------

ACTION_PATTERNS = [
    (r"\btransform(s|ed|ing)?\b|\bgoes super\b|\bturns super\b|\bascends?\b", "TRANSFORM"),
    (r"\bpowers? up\b|\bpowering up\b|\bcharges? (up|energy|ki|power)\b|\baura (flares|explodes|bursts|erupts)\b|\bpower level\b",
     "POWERUP"),
    (r"\b(fires?|blasts?|shoots?|unleash(es)?|launches? (an? )?(energy|ki|beam|blast))\b|\bbeam\b|\b(energy|ki) (wave|blast|ball)\b|\bkamehameha\b",
     "BEAM"),
    (r"\bclash(es)?\b|\bcollide\b|\bcollision\b", "CLASH"),
    (r"\b(launch(es)?|rips?)\b.*\b(beyblade|blade|top)\b|\blet it rip\b|\bspins?\b|\bspinning\b", "SPIN"),
    (r"\b(summons?|throws? (an? |the |his |her |their )?(poke ?ball|capsule|ball))\b|\bi choose you\b|\bcalls? (out|forth)\b",
     "SUMMON"),
    (r"\b(is|gets|was) (hit|struck|knocked|blasted)\b|\bknocked back\b|\btakes the hit\b|\bcrashes into\b|\bfalls? (down|back)\b",
     "HIT"),
    (r"\b(punch(es)?|kicks?|hits?|strikes?|attacks?|rush(es)? (at|toward)|charges? (at|toward)|dash(es)? (at|toward)|tackles?|slams?|smashes)\b",
     "DASH"),
    (r"\bexplo(des|sion)\b|\bground shakes\b|\bearthquake\b|\bshockwave\b|\bboom\b", "EXPLOSION"),
    (r"\b(enters?|appears?|arrives?|lands?|walks in|steps in|flies in|jumps in|runs in|teleports in|shows up)\b", "ENTER"),
    (r"\b(leaves?|exits?|flies (off|away)|runs (off|away)|walks (off|away)|disappears?|vanishes|teleports away)\b", "EXIT"),
    (r"\b(calms? down|powers? down|aura fades)\b", "CALM"),
    (r"\b(smiles?|grins?|laughs?|cheers?)\b", "FACE:happy"),
    (r"\b(smirks?)\b", "FACE:smirk"),
    (r"\b(glares?|frowns?|growls?|clenches|scowls?)\b", "FACE:angry"),
    (r"\b(cries|crying|sobs?|tears up)\b", "FACE:sad"),
    (r"\b(gasps?|shocked|stunned|surprised|eyes widen|jaw drops)\b", "FACE:surprised"),
    (r"\b(screams?|yells?|shouts?|roars?)\b", "FACE:shout"),
]


def interpret_action(script, sentence):
    """Turn a plain-language sentence into (command, args, options) or None."""
    low = sentence.lower()
    chars = script.mentions(sentence)
    for pattern, cmd in ACTION_PATTERNS:
        if not re.search(pattern, low):
            continue
        opts = {}
        if cmd.startswith("FACE:"):
            if not chars:
                continue
            return "FACE", [chars[0], cmd.split(":")[1]], opts
        if cmd == "HIT":
            # Passive voice: the first named character is the one hit.
            return ("HIT", chars[:1], opts) if chars else ("HIT", [], opts)
        if cmd in ("ENTER", "EXIT"):
            if not chars:
                continue
            d = re.search(r"\bfrom (the )?(left|right|sky|above|top)\b", low)
            if d:
                opts["from"] = d.group(2)
            elif re.search(r"\b(flies|lands|drops|descends)\b", low):
                opts["from"] = "top"
            if cmd == "EXIT":
                d = re.search(r"\b(to the |towards? the )?(left|right)\b", low)
                if d:
                    opts["to"] = d.group(2)
                elif re.search(r"\bflies\b|\bup\b", low):
                    opts["to"] = "top"
            return cmd, chars[:1], opts
        if cmd == "TRANSFORM":
            f = re.search(r"\binto (an? |the |his |her |their )?([\w\- ]+?)(?: form| mode)?(?:[.!,]|$)", low)
            if f:
                opts["form"] = re.sub(r"[^a-z0-9]+", "_", f.group(2).strip())
            return cmd, chars[:1], opts
        if not chars and cmd not in ("EXPLOSION",):
            continue
        return cmd, chars[:2], opts
    return None


def _add_action(script, scene, text, lineno, raw):
    text, _opts = _extract_options(text)
    sentences = [s.strip() for s in re.split(r"(?<=[.!?;])\s+", text) if s.strip()]
    added = False
    for s in sentences:
        res = interpret_action(script, s)
        if res:
            cmd, args, opts = res
            scene.beats.append(Beat(kind="command", lineno=lineno, command=cmd, args=list(args),
                                    options=dict(opts, auto="1"), text=s, source=raw.strip()))
            added = True
    if not added:
        scene.beats.append(Beat(kind="note", lineno=lineno, text=text, source=raw.strip()))


def _validate(script):
    if not script.scenes:
        script.errors.append("no SCENE found - add a line like 'SCENE: Rocky Canyon - DAY'")
    for sc in script.scenes:
        if sc.location and not script.find_location(sc.location):
            script.locations[sc.location] = LocationDef(name=sc.location, lineno=sc.lineno)
            script.warnings.append(
                f"line {sc.lineno}: location '{sc.location}' has no LOCATION definition; using a painted placeholder")
        elif sc.location:
            sc.location = script.find_location(sc.location)
        if not sc.time:
            loc = script.locations.get(sc.location)
            sc.time = loc.time if loc else "day"
        if not sc.beats:
            script.warnings.append(f"line {sc.lineno}: scene {sc.index} is empty")
        for b in sc.beats:
            if b.kind == "command":
                for a in b.args:
                    if (b.command in ("ENTER", "EXIT", "POWERUP", "CALM", "TRANSFORM", "HIT", "SPIN",
                                      "ZOOM") and a == b.args[0] and not script.find_character(a)):
                        script.warnings.append(f"line {b.lineno}: unknown character '{a}' in [{b.command}]")
