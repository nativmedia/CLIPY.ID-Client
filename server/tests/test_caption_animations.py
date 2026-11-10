import re
import unittest

from clipy import captions


class CaptionAnimationTests(unittest.TestCase):
    def test_added_caption_fonts_have_assets_and_ass_families(self):
        expected = {
            "just-me": ("Just Me Again Down Here", "Just Me Again Down Here"),
            "instrument-serif": ("Instrument Serif", "Instrument Serif"),
            "jetbrains-mono": ("JetBrains Mono", "JetBrains Mono"),
            "gaegu": ("Gaegu", "Gaegu"),
        }
        words = [captions.Word("Halo", 0, .4)]
        for name, (label, family) in expected.items():
            with self.subTest(font=name):
                self.assertEqual(captions.OPTIONS["fonts"][name], label)
                self.assertTrue((captions.FONT_DIR / captions.FONT_FILES[name]).is_file())
                ass = captions.build_ass(words, captions.CaptionStyle(font=name), 360, 640)
                self.assertIn(f"Style: Cap,{family},", ass)

    def test_word_glow_keeps_sharp_glyphs_above_the_translucent_halo_for_each_weight(self):
        words = [captions.Word("Paul", 0, .4)]
        for display in ("word", "sentence"):
            for font_name in captions.FONT_WEIGHT_FONTS:
                for font_weight, numeric_weight in captions.FONT_WEIGHTS.items():
                    with self.subTest(display=display, font=font_name, weight=font_weight):
                        style = captions.CaptionStyle(font=font_name, display=display, animation="glow", font_weight=font_weight, color="white")
                        lines = captions.build_ass(words, style, 360, 640).splitlines()
                        halo = next(line for line in lines if r"\3a&H78&" in line)
                        sharp = next(line for line in lines if r"\blur0" in line)
                        self.assertIn(rf"\b{numeric_weight}", halo)
                        self.assertIn(r"\1a&HFF&", halo)
                        self.assertNotIn(r"\1a&HFF&", sharp)
                        self.assertGreater(int(sharp[10]), int(halo[10]))
                        self.assertEqual(re.search(r"\\pos\([^)]*\)", halo)[0], re.search(r"\\pos\([^)]*\)", sharp)[0])

    def test_font_weight_is_written_to_ass_style_and_word_tags(self):
        words = [captions.Word("Halo", 0, .4)]
        for font_name in captions.FONT_WEIGHT_FONTS:
            for name, numeric_weight in captions.FONT_WEIGHTS.items():
                with self.subTest(font=font_name, weight=name):
                    ass = captions.build_ass(words, captions.CaptionStyle(font=font_name, font_weight=name), 360, 640)
                    self.assertIn(f",{numeric_weight},0,0,0,100,100,", ass)
                    self.assertIn(rf"\b{numeric_weight}", ass)

    def test_static_fonts_always_render_regular_weight(self):
        words = [captions.Word("Halo", 0, .4)]
        ass = captions.build_ass(words, captions.CaptionStyle(font="gaegu", font_weight="extra-bold"), 360, 640)
        self.assertIn(",400,0,0,0,100,100,", ass)
        self.assertIn(r"\b400", ass)
        self.assertEqual(captions.OPTIONS["font_weight_fonts"], ["jetbrains-mono", "montserrat", "poppins"])
        self.assertNotIn("cambria", captions.OPTIONS["fonts"])
        self.assertNotIn("ink-free", captions.OPTIONS["fonts"])

    def test_weight_ready_google_fonts_ship_real_files_for_each_choice(self):
        for font_name in captions.FONT_WEIGHT_FONTS:
            for weight in captions.FONT_WEIGHTS:
                with self.subTest(font=font_name, weight=weight):
                    self.assertTrue((captions.FONT_DIR / captions.font_file(font_name, weight)).is_file())

    def test_word_box_moves_between_words_while_glyph_anchors_stay_fixed(self):
        words = [captions.Word("Paul", 0, .4), captions.Word("berlari.", .4, .8)]
        style = captions.CaptionStyle(display="sentence", animation="box", position=4)
        ass = captions.build_ass(words, style, 360, 640)
        lines = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
        boxes = [line for line in lines if ",Shape," in line]
        self.assertEqual(len(boxes), 2)
        self.assertNotEqual(*(re.search(r"\\pos\((\d+),", box)[1] for box in boxes))
        for word in ("PAUL", "BERLARI"):
            variants = [line for line in lines if line.endswith("}" + word)]
            self.assertEqual(len(variants), 2)
            self.assertEqual(len({re.search(r"\\pos\([^)]*\)", line)[0] for line in variants}), 1)
        self.assertEqual(captions.for_stacked_panels(ass, 640), ass)

    def test_sentence_line_limit_is_shared_by_srt(self):
        words = [captions.Word(word, i * .3, i * .3 + .27) for i, word in enumerate(
            "Paul melihat pasukan mundur tetapi Kat masih bertahan di tengah serangan.".split()
        )]
        for limit in (1, 2, 3):
            style = captions.CaptionStyle(display="sentence", max_lines=limit)
            cues = captions.build_srt(words, style, 360, 640).strip().split("\n\n")
            self.assertEqual(max(len(cue.splitlines()[2:]) for cue in cues), limit)
            self.assertEqual(" ".join(" ".join(cue.splitlines()[2:]) for cue in cues), " ".join(w.text for w in words))

