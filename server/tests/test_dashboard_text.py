import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from clipy import dashboard_text, thumbs


def sample_project(root):
    return SimpleNamespace(dir=root, title="Cerita sumber", clips=[
        {"n": 1, "status": "done", "video": "clip_1.mp4", "start": 0, "end": 2, "title": "Laporan hilang", "narration": "Tokoh mencari saudaranya."},
        {"n": 2, "status": "done", "video": "clip_2.mp4", "start": 3, "end": 5, "title": "Petunjuk", "narration": "Ia menemukan petunjuk."},
    ])


def result(ids):
    return {"items": [{"id": item, "title": "Judul utuh", "pendek": "Satu pertanyaan tentang cerita.",
                       "sedang": "Deskripsi sedang tentang cerita.", "panjang": "Deskripsi panjang tentang cerita."}
                      for item in ids]}


class DashboardTextTests(unittest.TestCase):
    def test_clip_and_sequence_generation_preserves_old_posts_and_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            p = sample_project(root)
            legacy = '{"1":{"youtube":{"description":"edit lama"}}}'
            (root / "social.json").write_text(legacy, encoding="utf-8")
            (root / "words.json").write_text(json.dumps([{"text": "Bukti", "start": 0, "end": 1}]), encoding="utf-8")
            with patch.object(dashboard_text.keys, "ask_json", return_value=result(["1", "2"])) as ask:
                first = dashboard_text.ensure(p)
                self.assertEqual(set(first), {"1", "2"})
                self.assertIn("Tokoh mencari saudaranya", ask.call_args.args[1])
            dashboard_text.update(p, "1", "sedang", "Edit sendiri")
            with patch.object(dashboard_text.keys, "ask_json", return_value=result(["sequence"])) as ask:
                combined = dashboard_text.ensure(p, include_sequence=True)
                self.assertEqual(combined["1"]["sedang"], "Edit sendiri")
                self.assertEqual(combined["sequence"]["title"], "Judul utuh")
                self.assertIn("Bukti", ask.call_args.args[1])
                self.assertIn("sudut pandang lain", ask.call_args.args[0])
            with patch.object(dashboard_text.keys, "ask_json", side_effect=AssertionError("must use cache")):
                self.assertEqual(dashboard_text.ensure(p, True), combined)
            self.assertEqual((root / "social.json").read_text(encoding="utf-8"), legacy)

    def test_incomplete_response_is_not_saved_and_invalid_edits_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            p = sample_project(Path(directory))
            with patch.object(dashboard_text.keys, "ask_json", return_value=result(["1"])):
                with self.assertRaisesRegex(ValueError, "belum lengkap"):
                    dashboard_text.ensure(p)
            self.assertFalse((p.dir / "dashboard-text.json").exists())
            with self.assertRaises(ValueError):
                dashboard_text.update(p, "999", "pendek", "x")
            with self.assertRaises(ValueError):
                dashboard_text.update(p, "1", "pendek", "x" * 71)
            self.assertLessEqual(len(dashboard_text._fit("kata " * 40, 70)), 70)

    def test_thumbnail_auto_uses_clip_title_and_respects_manual_or_none(self):
        clip = {"title": "Laporan Hilang di Tengah Hutan", "thumbnail_text": "Judul lama"}
        self.assertEqual(thumbs.thumbnail_text({"text_mode": "auto", "title_style": "pop"}, clip), clip["title"])
        self.assertEqual(thumbs.thumbnail_text({"text_mode": "manual", "text": "Judul pilihan"}, clip), "Judul pilihan")
        self.assertEqual(thumbs.thumbnail_text({"text_mode": "none"}, clip), "")

    def test_invalid_saved_file_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "dashboard-text.json"
            path.write_text("{broken", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "dipertahankan"):
                dashboard_text.update(sample_project(root), "1", "pendek", "Edit")
            self.assertEqual(path.read_text(encoding="utf-8"), "{broken")


if __name__ == "__main__":
    unittest.main()
