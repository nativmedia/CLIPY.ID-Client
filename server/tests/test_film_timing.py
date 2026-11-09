import unittest
import tempfile
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from clipy import film, media
from clipy.captions import CaptionStyle, Word


class FilmTimingTests(unittest.TestCase):
    def test_planned_footage_is_not_shortened_to_voice_duration(self):
        frames = film._episode_frames(58.0, 45.0, [Word("selesai", 44.0, 44.7)], 60, 3)
        self.assertEqual(frames, 58 * media.FPS)

    def test_output_extends_past_last_word_to_a_complete_frame(self):
        frames = film._episode_frames(55.0, 55.1, [Word("selesai", 55.0, 55.24)], 60, 2)
        self.assertGreaterEqual(frames / media.FPS, 55.24)

    def test_refuses_to_truncate_narration_at_limit(self):
        with self.assertRaisesRegex(ValueError, "tanpa memotong kata"):
            film._episode_frames(59.0, 59.9, [Word("selesai", 59.8, 60.1)], 60, 2)

    def test_serial_story_is_not_rewritten_when_voice_exceeds_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            project = SimpleNamespace(dir=Path(directory))
            clip = {"n": 1, "narration": "Cerita yang harus tetap utuh", "shots": [{"start": 0, "end": 5}]}
            config = {"max_duration": 10, "voice": "voice", "story_mode": "serial"}
            with patch.object(film.tts, "speak_aligned", return_value=(Path(directory) / "voice.mp3", [Word("utuh", 11.0, 11.8)])) as speak, \
                 patch.object(media, "probe_duration", return_value=12.0), \
                 patch.object(film.keys, "ask_json") as rewrite:
                with self.assertRaisesRegex(ValueError, "tanpa memotong kata"):
                    film.render(project, clip, {"width": 64, "height": 64}, config)
            speak.assert_called_once()
            rewrite.assert_not_called()

    def test_serial_duration_cap_limits_longer_parts(self):
        project = SimpleNamespace(clips=[{"shots": [{"start": 0, "end": 51.01}]},
                                         {"shots": [{"start": 100, "end": 153.0}]}])
        cap = film._serial_duration_limit(project, {"max_duration": 60})
        self.assertAlmostEqual(cap, 51 + 1 / media.FPS + 2.9)
        with self.assertRaisesRegex(ValueError, "tanpa memotong kata"):
            film._episode_frames(53.0, cap + 0.1, [], cap, 1)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_serial_render_keeps_planned_duration_with_short_voice(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, voice = root / "source.mp4", root / "voice.mp3"
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=blue:s=80x60:r=30:d=1.5", "-c:v", "libx264", str(source)], check=True)
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=duration=0.5", "-c:a", "libmp3lame", str(voice)], check=True)
            clip = {"n": 1, "narration": "Selesai", "title": "Selesai", "shots": [{"start": 0, "end": 1.4}]}
            project = SimpleNamespace(dir=root, upload_id="id", local_video=source, upload=None, clips=[clip])
            opts = {"width": 180, "height": 320, "style": CaptionStyle(), "brand": None, "brand_position": "bc", "thumb_cfg": {}}
            with patch.object(film.tts, "speak_aligned", return_value=(voice, [Word("Selesai", 0.1, 0.4)])), \
                 patch.object(media, "video_encoders", return_value=[["-c:v", "libx264", "-preset", "ultrafast"]]):
                film.render(project, clip, opts, {"max_duration": 10, "voice": "voice", "story_mode": "serial", "background": "black"})
            self.assertAlmostEqual(media.probe_duration(root / clip["video"]), 1.4, delta=0.1)


if __name__ == "__main__":
    unittest.main()
