import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
import app


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class SequenceCoverApiTests(unittest.TestCase):
    def test_apply_status_preview_and_download(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "filming-sequence.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=blue:s=320x240:r=30:d=1",
                            "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                            "-c:a", "aac", "-shortest", str(source)], check=True)
            original = source.read_bytes()
            project = SimpleNamespace(id="cover-api", dir=root, settings={"mode": "filming", "thumb": {"title_style": "pop"}})
            endpoint = "/api/projects/cover-api/film-sequence"
            with patch.object(app.projects, "get", return_value=project), patch.object(app.film_sequence, "download", return_value=source):
                client = TestClient(app.app)
                self.assertEqual(client.get(endpoint + "/download").content, original)
                result = client.post(endpoint + "/cover", json={"title": "Judul pilihan"})
                self.assertEqual(result.status_code, 200)
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    state = client.get(endpoint + "/cover").json()
                    if state["status"] != "running":
                        break
                    time.sleep(.1)
                self.assertEqual(state["status"], "done", state)
                self.assertTrue(state["has_applied"])
                self.assertEqual(state["title"], "Judul pilihan")
                download = client.get(endpoint + "/download")
                self.assertEqual(download.status_code, 200)
                self.assertTrue(download.headers["content-disposition"].startswith("attachment"))
                self.assertEqual(client.get(endpoint + "/cover/image").content[:2], b"\xff\xd8")
                self.assertTrue(client.get(endpoint + "/video").headers["content-disposition"].startswith("inline"))
                self.assertEqual(source.read_bytes(), original)
            app.sequence_cover._jobs.pop(project.id, None)


if __name__ == "__main__":
    unittest.main()
