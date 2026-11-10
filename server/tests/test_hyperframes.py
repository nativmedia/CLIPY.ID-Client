import json
import re
import tempfile
import unittest
from pathlib import Path

from clipy import captions, hyperframes


class HyperFramesTests(unittest.TestCase):
    def test_untrusted_caption_text_is_escaped_and_keeps_word_timing(self):
        words = [captions.Word("</script>", .1, .4), captions.Word("Paul", .4, .8)]
        with tempfile.TemporaryDirectory() as folder:
            path = hyperframes.caption_composition(Path(folder), words,
                captions.CaptionStyle(animation="glow", display="sentence"), 360, 640, 2)
            source = path.read_text(encoding="utf-8")
            self.assertIn("&lt;/SCRIPT&gt;", source)
            payload = json.loads(re.search(r'id="overlay-config">(.*?)</script>', source, re.S)[1])
            row = payload["cues"][0]
            self.assertEqual([word["start"] for word in row["words"]], [.1, .4])
            self.assertEqual(row["words"][0]["end"], .4)
            self.assertIn("visibility:hidden", source)
            self.assertEqual(source.count('id="word-'), 2)

    def test_document_serializes_script_terminator_safely(self):
        with tempfile.TemporaryDirectory() as folder:
            source = hyperframes.document(Path(folder), 360, 640, 1, "", "",
                {"label": "</script><script>alert(1)</script>"}).read_text(encoding="utf-8")
            payload = re.search(r'id="overlay-config">(.*?)</script>', source, re.S)[1]
            self.assertNotIn("<", payload)
            self.assertEqual(json.loads(payload)["label"], "</script><script>alert(1)</script>")

    def test_font_weight_is_written_into_the_html_caption_layer(self):
        words = [captions.Word("Short", 0.1, 0.4)]
        with tempfile.TemporaryDirectory() as folder:
            source = hyperframes.caption_composition(Path(folder), words,
                captions.CaptionStyle(font="jetbrains-mono", animation="highlight", display="sentence", font_weight="extra-bold"),
                360, 640, 2).read_text(encoding="utf-8")
            payload = json.loads(re.search(r'id="overlay-config">(.*?)</script>', source, re.S)[1])
            self.assertIn("font-weight:800", source)
            self.assertEqual(payload["cues"][0]["words"][0]["outline"], 0)

    def test_google_font_weight_faces_are_available_to_the_html_renderer(self):
        words = [captions.Word("Short", 0.1, 0.4)]
        with tempfile.TemporaryDirectory() as folder:
            source = hyperframes.caption_composition(Path(folder), words,
                captions.CaptionStyle(font="poppins", animation="highlight", display="sentence", font_weight="extra-bold"),
                360, 640, 2).read_text(encoding="utf-8")
            self.assertIn("Poppins-ExtraBold.ttf", source)
            self.assertIn("font-weight:800", source)
            source = hyperframes.caption_composition(Path(folder), words,
                captions.CaptionStyle(font="montserrat", animation="highlight", display="sentence", font_weight="thin"),
                360, 640, 2).read_text(encoding="utf-8")
            self.assertIn("font-weight:100 900", source)

