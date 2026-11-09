"""Filming sequence keeps all finished episodes, in order, without re-encoding."""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from clipy import film_sequence


def project(root, count=2):
    return SimpleNamespace(id="film-sequence-test", dir=root, status="done",
                           settings={"mode": "filming", "film": {"episodes": count}},
                           clips=[{"n": n, "status": "done", "video": f"clip_{n}.mp4"} for n in range(1, count + 1)])


class SequenceValidationTests(unittest.TestCase):
    def test_rejects_missing_failed_and_unfinished_episodes(self):
        with tempfile.TemporaryDirectory() as folder:
            p = project(Path(folder))
            for clip in p.clips:
                (p.dir / clip["video"]).touch()
            self.assertEqual([p.name for p in film_sequence._sources(p)], ["clip_1.mp4", "clip_2.mp4"])
            p.clips[1]["status"] = "error"
            with self.assertRaisesRegex(ValueError, "gagal|selesai"):
                film_sequence._sources(p)
            p.clips[1]["status"] = "done"
            p.clips.pop()
            with self.assertRaisesRegex(ValueError, "belum lengkap"):
                film_sequence._sources(p)
            p.status = "clipping"
            with self.assertRaisesRegex(ValueError, "Selesaikan"):
                film_sequence._sources(p)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class SequenceMediaTests(unittest.TestCase):
    def _clip(self, path, color, size="64x64"):
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color={color}:s={size}:r=30:d=1",
                        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=1",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)], check=True)

    def test_stream_copy_keeps_order_audio_and_sources(self):
        with tempfile.TemporaryDirectory() as folder:
            p = project(Path(folder))
            self._clip(p.dir / "clip_1.mp4", "red")
            self._clip(p.dir / "clip_2.mp4", "blue")
            paths = film_sequence._sources(p)
            film_sequence._jobs[p.id] = {"status": "running", "progress": 0.0, "message": "", "fingerprint": film_sequence._fingerprint(paths)}
            with patch.object(film_sequence.projects, "get", return_value=p):
                film_sequence._run(p.id, paths, film_sequence._fingerprint(paths))
            state = film_sequence.status(p)
            self.assertEqual(state["status"], "done", state)
            result = film_sequence.download(p)
            spec, duration = film_sequence._probe(result)
            self.assertAlmostEqual(duration, 2, delta=.15)
            self.assertEqual([s["codec_type"] for s in spec], ["video", "audio"])
            streams = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration", "-of", "json", str(result)], check=True, capture_output=True, text=True).stdout)["streams"]
            for stream in streams:
                self.assertAlmostEqual(float(stream["duration"]), 2, delta=.15)
            def pixel(at):
                return subprocess.run(["ffmpeg", "-v", "error", "-ss", str(at), "-i", str(result), "-frames:v", "1", "-vf", "scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], check=True, capture_output=True).stdout
            self.assertGreater(pixel(.2)[0], pixel(.2)[2])
            self.assertGreater(pixel(1.4)[2], pixel(1.4)[0])
            self.assertTrue(all(path.is_file() for path in paths))
            original_output = result.read_bytes()
            result.write_bytes(original_output + b"modified")
            self.assertEqual(film_sequence.status(p)["status"], "idle")
            with patch.object(film_sequence.projects, "get", return_value=p):
                film_sequence._run(p.id, paths, film_sequence._fingerprint(paths))
            self.assertEqual(film_sequence.status(p)["status"], "done")
            film_sequence._jobs.pop(p.id)
            self.assertEqual(film_sequence.status(p)["status"], "done")
            (p.dir / "clip_2.mp4").touch()
            self.assertEqual(film_sequence.status(p)["status"], "idle")

    def test_incompatible_clips_report_error_without_output(self):
        with tempfile.TemporaryDirectory() as folder:
            p = project(Path(folder))
            self._clip(p.dir / "clip_1.mp4", "red")
            self._clip(p.dir / "clip_2.mp4", "blue", "96x64")
            paths = film_sequence._sources(p)
            film_sequence._jobs[p.id] = {"status": "running", "progress": 0.0, "message": "", "fingerprint": film_sequence._fingerprint(paths)}
            with patch.object(film_sequence.projects, "get", return_value=p):
                film_sequence._run(p.id, paths, film_sequence._fingerprint(paths))
            state = film_sequence.status(p)
            self.assertEqual(state["status"], "error")
            self.assertIn("berbeda", state["message"])
            self.assertFalse((p.dir / "filming-sequence.mp4").exists())


if __name__ == "__main__":
    unittest.main()
