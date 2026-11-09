"""The sequence preserves clip order and removes thumbnail intros, including older projects."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from clipy import media, projects


class SequencePartsTests(unittest.TestCase):
    def test_clean_body_old_intro_and_failed_dub(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("clip_1.mp4", "clip_2.mp4", "clip_3.mp4", "body_1.mp4", "dub_2.mp3"):
                (root / name).touch()
            clips = [{"n": n, "video": f"clip_{n}.mp4", "status": "done"} for n in (1, 2, 3)]
            clips[2]["note"] = "no dubbed intro (error)"
            p = SimpleNamespace(dir=root, status="done", settings={"thumb": {"voice": "voice"}}, clips=clips)
            with patch.object(media, "probe_duration", return_value=1.234):
                self.assertEqual(projects.sequence_parts(p), [(root / "body_1.mp4", 0), (root / "clip_2.mp4", 1.63), (root / "clip_3.mp4", 0)])
            (root / "dub_2.mp3").unlink()
            with self.assertRaisesRegex(ValueError, "dubbing tidak tersedia"):
                projects.sequence_parts(p)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class SequenceMediaTests(unittest.TestCase):
    def test_concat_trims_intro_and_handles_missing_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, second, output = (root / name for name in ("first.mp4", "second.mp4", "out.mp4"))
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=red:s=64x64:r=30:d=1", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(first)], check=True)
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=blue:s=64x64:r=30:d=1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(second)], check=True)
            with patch.object(media, "video_encoders", return_value=[["-c:v", "libx264", "-preset", "ultrafast"]]):
                media.concatenate_clips([(first, 0.5), (second, 0)], output)
            self.assertAlmostEqual(media.probe_duration(output), 1.5, delta=0.15)
            def pixel(at):
                return subprocess.run(["ffmpeg", "-v", "error", "-ss", str(at), "-i", str(output), "-frames:v", "1", "-vf", "scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], check=True, capture_output=True).stdout
            self.assertGreater(pixel(0.2)[0], pixel(0.2)[2])
            self.assertGreater(pixel(0.9)[2], pixel(0.9)[0])


if __name__ == "__main__":
    unittest.main()
