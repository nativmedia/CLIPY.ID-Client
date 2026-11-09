import json
import os
import queue
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from clipy import projects


class ResumeProjectTests(unittest.TestCase):
    def test_restart_preserves_inputs_and_requeues_once(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(projects, 'DATA_DIR', Path(folder)), \
                patch.object(projects, '_projects', {}), \
                patch.object(projects, '_queue', queue.Queue()), \
                patch.object(projects, '_active', None):
            project = projects.create('https://example.test/video', 'test', {'clip': {'count': 4}})
            project.status, project.stage = 'cancelled', 'dihentikan'
            project.save()
            (project.dir / 'source.srt').write_text('original input', encoding='utf-8')
            projects._projects[project.id] = project

            restarted = projects.restart_unstarted(project)
            self.assertEqual(restarted.id, project.id)
            self.assertEqual(restarted.settings, project.settings)
            self.assertEqual(restarted.status, 'queued')
            self.assertEqual(projects._queue.get_nowait(), project.id)
            self.assertEqual(json.loads((project.dir / 'project.json').read_text(encoding='utf-8'))['status'], 'queued')
            self.assertEqual((project.dir / 'source.srt').read_text(encoding='utf-8'), 'original input')
            with self.assertRaises(ValueError):
                projects.restart_unstarted(restarted)
            self.assertTrue(projects._queue.empty())

    def test_restart_rejects_started_or_active_jobs(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(projects, 'DATA_DIR', Path(folder)), \
                patch.object(projects, '_projects', {}), \
                patch.object(projects, '_queue', queue.Queue()), \
                patch.object(projects, '_active', None):
            project = projects.create('', '', {})
            projects._projects[project.id] = project
            project.status = 'error'
            project.progress = 0.2
            with self.assertRaises(ValueError):
                projects.restart_unstarted(project)
            project.progress = 0
            project.clips = [{'n': 1, 'status': 'done'}]
            with self.assertRaises(ValueError):
                projects.restart_unstarted(project)
            project.clips = []
            with patch.object(projects, '_active', (project.id, object())):
                with self.assertRaises(ValueError):
                    projects.restart_unstarted(project)
            self.assertTrue(projects._queue.empty())

    @unittest.skipUnless(os.name == 'nt', 'Windows stdin inheritance regression')
    def test_worker_starts_with_parent_stdin_in_use(self):
        with tempfile.TemporaryDirectory() as folder:
            project_dir = Path(folder) / 'sample'
            project_dir.mkdir()
            (project_dir / 'project.json').write_text(json.dumps({
                'id': 'sample', 'url': '', 'video_id': '', 'settings': {}, 'status': 'queued'
            }), encoding='utf-8')
            parent_code = r'''
import os, sys, threading, time
from pathlib import Path
from clipy import projects
projects.DATA_DIR = Path(sys.argv[1])
threading.Thread(target=lambda: sys.stdin.buffer.read(1), daemon=True).start()
time.sleep(0.5)
worker = projects._start_project("sample", [])
try:
    code = worker.wait(timeout=30)
    print("worker-returncode=" + str(code), flush=True)
except Exception:
    worker.kill()
    worker.wait()
    print("worker-blocked", flush=True)
os._exit(0)
'''
            parent = subprocess.Popen([sys.executable, '-c', parent_code, folder], stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      cwd=Path(__file__).resolve().parents[1])
            try:
                parent.wait(timeout=45)  # Keep the input pipe open until the worker finishes.
                stdout, stderr = parent.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                parent.kill()
                parent.communicate()
                self.fail('The project worker did not finish')
            self.assertIn(b'worker-returncode=0', stdout, stderr.decode(errors='replace'))
            checkpoint = json.loads((project_dir / 'project.json').read_text(encoding='utf-8'))
            # Invalid synthetic settings intentionally produce an error after worker startup.
            self.assertEqual(checkpoint['status'], 'error')
            self.assertIn('clip', checkpoint['error'])


if __name__ == '__main__':
    unittest.main()
