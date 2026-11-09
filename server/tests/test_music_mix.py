import subprocess
import tempfile
import unittest
from pathlib import Path
import numpy as np
from clipy import media

class MusicMixTests(unittest.TestCase):
    def test_selected_segment_is_mixed_with_original_audio(self):
        with tempfile.TemporaryDirectory(prefix="clipy-mix-test-") as tmp:
            d = Path(tmp)
            video, music, output = d / "video.mp4", d / "music.wav", d / "out.mp4"
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=blue:s=160x120:r=30:d=3", "-f", "lavfi", "-i", "sine=frequency=220:duration=3", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(video)], check=True)
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=400:duration=1", "-f", "lavfi", "-i", "sine=frequency=900:duration=2", "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1[a]", "-map", "[a]", str(music)], check=True)
            media.mix_music(video, music, output, start=1.0, length=1.0)
            raw = subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(output), "-vn", "-ac", "1", "-ar", "8000", "-f", "f32le", "-"])
            samples = np.frombuffer(raw, dtype="<f4")[:8000]
            time = np.arange(len(samples)) / 8000
            def magnitude(frequency):
                return abs(np.dot(samples, np.exp(-2j * np.pi * frequency * time)))
            self.assertGreater(media.probe_duration(output), 2.9)
            self.assertGreater(magnitude(220), 1)
            self.assertGreater(magnitude(900), magnitude(400) * 5)
