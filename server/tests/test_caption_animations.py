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

    def test_font_picker_is_alphabetical_and_scales_small_visible_faces(self):
        labels = list(captions.OPTIONS["fonts"].values())
        self.assertEqual(labels, sorted(labels, key=str.casefold))

        words = [captions.Word("Caption terbaca", 0, 1)]
        montserrat = captions._caption_cues(words, captions.CaptionStyle(font="montserrat", display="sentence"), 360, 640)[0]
        gaegu = captions._caption_cues(words, captions.CaptionStyle(font="gaegu", display="sentence"), 360, 640)[0]
        self.assertGreater(gaegu.size, montserrat.size)

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

    def test_coarse_transcript_segment_becomes_readable_one_line_captions(self):
        text = "Ini satu segmen subtitle otomatis yang sangat panjang sehingga harus dibagi menjadi kata-kata yang terbaca."
        words = [captions.Word(text, 0, 12)]
        style = captions.CaptionStyle(display="sentence", max_lines=1, case="sentence")

        cues = captions._caption_cues(words, style, 360, 640)
        srt_cues = captions.build_srt(words, style, 360, 640).strip().split("\n\n")

        self.assertGreater(len(cues), 1)
        self.assertTrue(all(cue.size > 20 for cue in cues))
        self.assertTrue(all(len(cue.rows) == 1 for cue in cues))
        self.assertGreater(len(srt_cues), 1)
        self.assertTrue(all(len(cue.splitlines()[2:]) == 1 for cue in srt_cues))

    def test_overlapping_segment_windows_become_serialized_visual_cues(self):
        words = [
            captions.Word("Misalnya kita tuning", 32.34, 36.11),
            captions.Word("Iya kan kita", 33.48, 35.12),
            captions.Word("Harus menyesuaikan", 34.97, 37.82),
        ]
        style = captions.CaptionStyle(font="poppins", display="sentence", max_lines=1)
        cues = captions._caption_cues(words, style, 1080, 1920)

        self.assertTrue(all(first.end <= second.start for first, second in zip(cues, cues[1:])))
        self.assertTrue(all(cue.end > cue.start for cue in cues))
        # Poppins' ASS line box is wider than its nominal Pillow size.  The
        # measured line height must follow the same scale as the emitted text.
        self.assertGreater(cues[0].line_height, cues[0].size * 1.5)

