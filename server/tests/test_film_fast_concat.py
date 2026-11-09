"""Exercise safe stream-copy concat with real synthetic video and voice."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from clipy import film


class FilmFastConcatTests(unittest.TestCase):
    def make_part(self, path, duration, width=320, rate=30):
        subprocess.run([
            "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
            f"testsrc2=size={width}x180:rate={rate}:duration={duration}",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-an", str(path),
        ], check=True, capture_output=True)

    def test_compatible_shots_are_joined_without_reencoding(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            parts = [root / "one.mp4", root / "two.mp4"]
            for part in parts:
                self.make_part(part, 1)
            voice, listing, output = root / "voice.mp3", root / "list.txt", root / "draft.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                            "sine=frequency=440:duration=2", str(voice)], check=True, capture_output=True)
            listing.write_text("file 'one.mp4'\nfile 'two.mp4'\n", encoding="utf-8")
            self.assertTrue(film._copy_episode(parts, [30, 30], listing, voice, output, 60))
            self.assertTrue(output.is_file())
            stream = film._video_stream(output)
            self.assertEqual(int(stream["nb_frames"]), 60)
            self.assertAlmostEqual(float(stream["duration"]), 2, places=2)
            self.assertEqual(film._video_stream(parts[0])["extradata_hash"], stream["extradata_hash"])

    def test_mixed_or_wrong_frame_count_falls_back_without_touching_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first, second, out = root / "one.mp4", root / "two.mp4", root / "draft.mp4"
            self.make_part(first, 1)
            self.make_part(second, 1, width=640)
            out.write_bytes(b"keep")
            self.assertFalse(film._matching_shots([first, second], [30, 30]))
            self.assertFalse(film._matching_shots([first], [29]))
            self.assertFalse(film._copy_episode([first, second], [30, 30], root / "list.txt", root / "voice.mp3", out, 60))
            self.assertEqual(out.read_bytes(), b"keep")


if __name__ == "__main__":
    unittest.main()
