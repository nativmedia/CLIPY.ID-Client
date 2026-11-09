import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
import app


class DashboardApiTests(unittest.TestCase):
    def test_text_and_video_endpoints_with_isolated_project(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            video = root / "combined.mp4"
            video.write_bytes(b"test-video-data")
            project = SimpleNamespace(id="test", dir=root, title="Cerita", settings={"mode": "filming"},
                                      clips=[{"n": 1, "status": "done", "video": "clip_1.mp4",
                                              "title": "Judul", "start": 0, "end": 1}])
            item = {"title": "Cerita utuh", "pendek": "Pendek", "sedang": "Sedang", "panjang": "Panjang"}
            generated = {"items": [{"id": "1", **item}, {"id": "sequence", **item}]}
            with patch.object(app.projects, "get", return_value=project), \
                 patch.object(app.film_sequence, "status", return_value={"status": "done"}), \
                 patch.object(app.film_sequence, "download", return_value=video), \
                 patch.object(app.dashboard_text.keys, "ask_json", return_value=generated):
                client = TestClient(app.app)
                response = client.get("/api/projects/test/dashboard-text")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["sequence"]["title"], "Cerita utuh")
                response = client.put("/api/projects/test/dashboard-text", json={"item": "1", "field": "sedang", "text": "Edit pengguna"})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(client.get("/api/projects/test/dashboard-text").json()["1"]["sedang"], "Edit pengguna")
                response = client.put("/api/projects/test/dashboard-text", json={"item": "sequence", "field": "title", "text": "Judul pilihan"})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(client.get("/api/projects/test/dashboard-text").json()["sequence"]["title"], "Judul pilihan")
                response = client.get("/api/projects/test/film-sequence/video")
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.headers["content-disposition"].startswith("inline"))
                self.assertEqual(response.content, b"test-video-data")


if __name__ == "__main__":
    unittest.main()
