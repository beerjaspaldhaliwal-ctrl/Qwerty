"""Optional placeholder voices using the text-to-speech already on the
computer (Windows speech, macOS 'say', or espeak/espeak-ng on Linux).

Real voice recordings always win: put them in <assets>/voices/ named
S01_001.wav, S01_002.wav ... (scene, dialogue line) or point a line at a
file with {voice: path.wav}.
"""

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile


def available_engine():
    if sys.platform.startswith("win"):
        return "sapi"
    if sys.platform == "darwin" and shutil.which("say"):
        return "say"
    for exe in ("espeak-ng", "espeak"):
        if shutil.which(exe):
            return exe
    return None


def _speak(engine, text, out, cdef):
    gender = (cdef.voice if cdef else "").lower()
    pitch = cdef.pitch if cdef else 50
    if engine in ("espeak", "espeak-ng"):
        variant = "f3" if "female" in gender or "girl" in gender else ("m3" if "male" in gender else "m1")
        cmd = [shutil.which(engine), "-v", f"en+{variant}", "-p", str(max(0, min(99, pitch))),
               "-s", "165", "-w", out, text]
        subprocess.run(cmd, check=True, capture_output=True, timeout=60)
    elif engine == "say":
        cmd = ["say", "-o", out, "--data-format=LEI16@22050", text]
        subprocess.run(cmd, check=True, capture_output=True, timeout=60)
    elif engine == "sapi":
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as tf:
            tf.write(text)
            tpath = tf.name
        hint = "Female" if "female" in gender else "Male"
        ps = (
            "Add-Type -AssemblyName System.Speech;"
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
            f"try {{ $s.SelectVoiceByHints('{hint}') }} catch {{}};"
            f"$s.Rate = 1; $s.SetOutputToWaveFile('{out}');"
            f"$s.Speak((Get-Content -Raw -Encoding UTF8 '{tpath}')); $s.Dispose()"
        )
        try:
            subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True,
                           capture_output=True, timeout=120)
        finally:
            os.unlink(tpath)


def generate(script, assets_dir, report=print):
    """Create a WAV for every dialogue line without a recorded voice.
    Sets beat.options['voice'] so the planner times lines to the audio."""
    engine = available_engine()
    if not engine:
        report("No text-to-speech found (install espeak-ng on Linux). Using subtitles only.")
        return 0
    out_dir = os.path.join(assets_dir, "voices_tts")
    os.makedirs(out_dir, exist_ok=True)
    made = 0
    for sc in script.scenes:
        n = 0
        for b in sc.beats:
            if b.kind != "dialogue":
                continue
            n += 1
            if b.options.get("voice"):
                continue
            recorded = [os.path.join(assets_dir, "voices", f"S{sc.index:02d}_{n:03d}{ext}")
                        for ext in (".wav", ".ogg", ".mp3", ".flac")]
            if any(os.path.exists(p) for p in recorded):
                continue
            cdef = script.characters.get(b.speaker)
            digest = hashlib.md5(f"{b.speaker}|{b.text}|{engine}".encode()).hexdigest()[:8]
            out = os.path.join(out_dir, f"S{sc.index:02d}_{n:03d}_{digest}.wav")
            if not os.path.exists(out):
                try:
                    _speak(engine, b.text, out, cdef)
                    made += 1
                except Exception as e:
                    report(f"Voice failed for line {b.lineno}: {e}")
                    continue
            b.options["voice"] = out
    return made
