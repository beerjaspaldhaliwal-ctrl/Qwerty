import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from anime_studio import script_parser as sp  # noqa: E402
from anime_studio.planner import Director, OFFSTAGE_X  # noqa: E402

TEMPLATE = os.path.join(os.path.dirname(__file__), "..", "anime_studio", "templates", "example_script.txt")

BASIC = """
TITLE: Test Episode
FPS: 24
CHARACTER: Kai
  color: #ff8800
  aura: gold
CHARACTER: Ryo | color=blue | height=3.8
CHARACTER: Blaze Falcon
  kind: object
LOCATION: Canyon
  image: locations/canyon.png
  ground: 0.25   # feet line

SCENE: Canyon - NIGHT
KAI (angry): You'll pay for that!
Ryo: Try me.   // inline comment
[POWERUP Kai color=red 2s]
Kai fires an energy blast at Ryo.
[SPIN Blaze Falcon]
[BEAM Kai -> Ryo name="Storm Flash!"]
"""


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.s = sp.parse(BASIC)

    def test_header_and_definitions(self):
        self.assertEqual(self.s.title, "Test Episode")
        self.assertEqual(list(self.s.characters), ["Kai", "Ryo", "Blaze Falcon"])
        self.assertEqual(self.s.characters["Ryo"].height, 3.8)
        self.assertEqual(self.s.characters["Blaze Falcon"].kind, "object")
        self.assertEqual(self.s.locations["Canyon"].ground, 0.25)
        self.assertEqual(self.s.errors, [])

    def test_scene_heading(self):
        sc = self.s.scenes[0]
        self.assertEqual((sc.location, sc.time), ("Canyon", "night"))

    def test_dialogue_and_expression(self):
        beats = self.s.scenes[0].beats
        self.assertEqual((beats[0].speaker, beats[0].expr, beats[0].text), ("Kai", "angry", "You'll pay for that!"))
        self.assertEqual((beats[1].speaker, beats[1].text), ("Ryo", "Try me."))

    def test_commands(self):
        beats = self.s.scenes[0].beats
        self.assertEqual((beats[2].command, beats[2].args), ("POWERUP", ["Kai"]))
        self.assertEqual(beats[2].options, {"color": "red", "secs": "2"})
        self.assertEqual(beats[4].args, ["Blaze Falcon"])
        self.assertEqual((beats[5].command, beats[5].args), ("BEAM", ["Kai", "Ryo"]))
        self.assertEqual(beats[5].options["name"], "Storm Flash!")

    def test_plain_language_action(self):
        b = self.s.scenes[0].beats[3]
        self.assertEqual((b.command, b.args), ("BEAM", ["Kai", "Ryo"]))

    def test_action_interpretation(self):
        s = sp.parse("CHARACTER: Kai\nCHARACTER: Ryo\nCHARACTER: Mira\nSCENE: X\n")
        cases = {
            "Kai powers up.": ("POWERUP", ["Kai"]),
            "Ryo punches Kai in the face.": ("DASH", ["Ryo", "Kai"]),
            "Kai is knocked back.": ("HIT", ["Kai"]),
            "Mira lands from the sky.": ("ENTER", ["Mira"]),
            "Ryo flies away.": ("EXIT", ["Ryo"]),
            "Kai transforms into super form!": ("TRANSFORM", ["Kai"]),
            "Mira smiles.": ("FACE", ["Mira", "happy"]),
        }
        for text, (cmd, args) in cases.items():
            res = sp.interpret_action(s, text)
            self.assertIsNotNone(res, text)
            self.assertEqual((res[0], res[1]), (cmd, args), text)
        self.assertEqual(sp.interpret_action(s, "Kai lands from the sky.")[2]["from"], "sky")
        self.assertEqual(sp.interpret_action(s, "Kai transforms into super form!")[2]["form"], "super")

    def test_screenplay_format(self):
        s = sp.parse("CHARACTER: Kai\n\nINT. DOJO - DAY\n\nKAI\n(shouting)\nI will win!\n\nKai smiles.\n")
        b = s.scenes[0].beats
        self.assertEqual((s.scenes[0].location, s.scenes[0].time), ("DOJO", "day"))
        self.assertEqual((b[0].speaker, b[0].expr, b[0].text), ("Kai", "shout", "I will win!"))
        self.assertEqual(b[1].command, "FACE")

    def test_undefined_speaker_gets_placeholder(self):
        s = sp.parse("SCENE: Somewhere\nGoro: Hello there.\n")
        self.assertIn("Goro", s.characters)
        self.assertTrue(any("Goro" in w for w in s.warnings))
        self.assertIn("Somewhere", s.locations)

    def test_colors(self):
        self.assertEqual(sp.parse_color("#ff0000"), (1.0, 0.0, 0.0))
        self.assertEqual(sp.parse_color("gold"), sp.NAMED_COLORS["gold"])
        self.assertEqual(sp.parse_color("255,128,0")[0], 1.0)

    def test_no_scene_is_error(self):
        self.assertTrue(sp.parse("TITLE: x").errors)


class PlannerTests(unittest.TestCase):
    def plan(self, text):
        return Director(sp.parse(text), "/nonexistent").plan()

    def test_template_plans(self):
        with open(TEMPLATE, encoding="utf-8") as fh:
            script = sp.parse(fh.read())
        self.assertEqual(script.errors, [])
        plan = Director(script, "/nonexistent").plan()
        self.assertEqual(len(plan.scenes), 3)
        for tl in plan.scenes:
            self.assertGreater(tl.length, 0)
            frames = [k.frame for k in tl.cam_keys]
            self.assertEqual(frames, sorted(frames), f"camera keys out of order in {tl.name}")
            for sh in tl.shots:
                self.assertLess(sh.start, sh.end)
            for fx in tl.fx:
                self.assertLessEqual(fx.start, fx.end)
                self.assertLessEqual(fx.end, tl.length + 1)
        ends = [tl.offset + tl.length for tl in plan.scenes]
        self.assertEqual(plan.total, ends[-1] - plan.overlap + plan.end_len)

    def test_dialogue_timing_and_subtitles(self):
        p = self.plan("CHARACTER: Kai\nSCENE: X\nKai: one two three four five six seven eight nine ten\n")
        tl = p.scenes[0]
        sub = tl.subtitles[0]
        self.assertEqual(sub.speaker, "Kai")
        self.assertGreaterEqual((sub.end - sub.start) / p.fps, 3.0)
        self.assertTrue(tl.chars["Kai"].talk)

    def test_enter_starts_offstage(self):
        p = self.plan("CHARACTER: Kai\nCHARACTER: Ryo\nSCENE: X\nKai: Hi.\nRyo enters from the right.\nRyo: Yo.\n")
        tl = p.scenes[0]
        self.assertEqual(tl.chars["Ryo"].x_at(0), OFFSTAGE_X)
        self.assertLess(abs(tl.chars["Ryo"].x_at(tl.length - 1)), OFFSTAGE_X - 1)
        self.assertGreater(tl.chars["Ryo"].x_at(tl.length - 1), 0)

    def test_fight_effects(self):
        p = self.plan("CHARACTER: Kai\nCHARACTER: Ryo\nSCENE: X\n[POWERUP Kai]\n[BEAM Kai Ryo]\n[DASH Ryo Kai]\n")
        kinds = {fx.kind for fx in p.scenes[0].fx}
        for k in ("aura", "speedlines", "shake", "beam", "charge", "impact", "flash", "burst"):
            self.assertIn(k, kinds)
        tl = p.scenes[0]
        self.assertIn("hurt", [e for _f, e in tl.chars["Ryo"].expr_keys])

    def test_transform_sets_form(self):
        p = self.plan("CHARACTER: Kai\nSCENE: X\n[TRANSFORM Kai super]\n")
        self.assertEqual(p.scenes[0].chars["Kai"].form_keys[0][1], "super")

    def test_fighters_take_opposite_sides(self):
        p = self.plan("CHARACTER: Kai\nCHARACTER: Ryo\nCHARACTER: Mira\nSCENE: X\nMira: Go!\n[DASH Kai Ryo]\n")
        tl = p.scenes[0]
        self.assertLess(tl.chars["Kai"].x_at(0) * tl.chars["Ryo"].x_at(0), 0)


if __name__ == "__main__":
    unittest.main()
