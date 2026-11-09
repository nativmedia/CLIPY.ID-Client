import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from clipy import projects


class DeleteFailedProjectTests(unittest.TestCase):
    def test_failed_project_can_be_deleted_without_touching_another(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(projects, 'DATA_DIR', Path(folder)), \
             patch.object(projects, '_projects', {}), patch.object(projects, '_active', None):
            failed = projects.Project('failed', '', '', {}, status='error')
            other = projects.Project('other', '', '', {}, status='done')
            failed.dir.mkdir()
            other.dir.mkdir()
            (failed.dir / 'clip.mp4').write_bytes(b'failed')
            (other.dir / 'clip.mp4').write_bytes(b'preserved')
            projects._projects.update(failed=failed, other=other)
            projects.delete_stopped(failed)
            self.assertFalse(failed.dir.exists())
            self.assertEqual((other.dir / 'clip.mp4').read_bytes(), b'preserved')

    def test_running_and_active_workers_cannot_be_deleted(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(projects, 'DATA_DIR', Path(folder)), \
             patch.object(projects, '_projects', {}):
            project = projects.Project('active', '', '', {}, status='error')
            project.dir.mkdir()
            projects._projects[project.id] = project
            with patch.object(projects, '_active', ('active', object())):
                with self.assertRaises(ValueError):
                    projects.delete_stopped(project)
            project.status = 'clipping'
            with patch.object(projects, '_active', None):
                with self.assertRaises(ValueError):
                    projects.delete_stopped(project)
            self.assertTrue(project.dir.exists())
