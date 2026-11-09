import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from clipy import film_sequence, sequence_cover


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class SequenceCoverTests(unittest.TestCase):
    def test_apply_embeds_cover_without_reencoding_video_or_audio(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "filming-sequence.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=blue:s=320x240:r=30:d=2",
                            "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                            "-c:a", "aac", "-shortest", str(source)], check=True)
            original = source.read_bytes()
            p = SimpleNamespace(id="test", dir=root, settings={"thumb": {"title_style": "pop"}})
            signature = sequence_cover._stat(source)
            sequence_cover._jobs[p.id] = {"status": "running"}
            with patch.object(film_sequence, "download", return_value=source), patch.object(sequence_cover.projects, "get", return_value=p):
                self.assertEqual(sequence_cover.video(p), source)
                sequence_cover._run(p.id, source, signature, "Judul yang diedit", "pop", None)
                state = sequence_cover.status(p)
                self.assertEqual(state["status"], "done", state)
                self.assertEqual(state["title"], "Judul yang diedit")
                output = sequence_cover.video(p)
                cover = sequence_cover.image(p)
                self.assertTrue(cover.is_file())
                details = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(output)],
                                                     check=True, capture_output=True, text=True).stdout)
                self.assertTrue(any(s.get("disposition", {}).get("attached_pic") == 1 for s in details["streams"]))
                def digest(path, stream):
                    return subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", stream, "-c", "copy", "-f", "md5", "-"],
                                          check=True, capture_output=True).stdout
                self.assertEqual(digest(source, "0:v:0"), digest(output, "0:v:0"))
                self.assertEqual(digest(source, "0:a:0"), digest(output, "0:a:0"))
                self.assertEqual(source.read_bytes(), original)
                with patch.object(sequence_cover.subprocess, "run", side_effect=OSError("ffmpeg gagal")):
                    sequence_cover._run(p.id, source, signature, "Judul lain", "pop", None)
                self.assertEqual(sequence_cover.status(p)["status"], "error")
                self.assertEqual(sequence_cover.video(p), output)
                self.assertEqual(source.read_bytes(), original)
                sequence_cover._jobs.pop(p.id, None)
                self.assertEqual(sequence_cover.status(p)["title"], "Judul yang diedit")
                source.write_bytes(original + b"change")
                self.assertEqual(sequence_cover.video(p), source)
                with self.assertRaises(ValueError): sequence_cover.image(p)

    def test_rejects_empty_title_before_start(self):
        with self.assertRaises(ValueError):
            sequence_cover.start(SimpleNamespace(), "   ")


if __name__ == "__main__":
    unittest.main()
