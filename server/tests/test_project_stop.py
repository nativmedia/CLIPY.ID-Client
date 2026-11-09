import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app import stop_project
from clipy import projects


class ProjectStopTests(unittest.TestCase):
    def test_stop_cancels_a_queued_project_and_interrupts_active_work(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(projects, "DATA_DIR", Path(directory)):
            active = projects.create("", "", {})
            queued = projects.create("", "", {})
            ready = Path(directory) / "child-ready"
            child = f"from pathlib import Path; import time; Path({str(ready)!r}).touch(); time.sleep(60)"
            work = f"import subprocess, sys; subprocess.run([sys.executable, '-c', {child!r}])"
            proc = subprocess.Popen([sys.executable, "-c", work], start_new_session=os.name != "nt")
            with patch.object(projects, "_projects", {active.id: active, queued.id: queued}), \
                 patch.object(projects, "_active", (active.id, proc)):
                try:
                    deadline = time.monotonic() + 5
                    while not ready.exists() and time.monotonic() < deadline:
                        time.sleep(0.02)
                    self.assertTrue(ready.exists())
                    self.assertEqual(stop_project(queued.id)["status"], "cancelled")
                    self.assertIsNone(proc.poll())
                    self.assertEqual(stop_project(active.id)["status"], "cancelled")
                    self.assertIsNotNone(proc.poll())
                    self.assertEqual(stop_project(active.id)["status"], "cancelled")
                    self.assertEqual(projects._checkpoint(active.id).status, "cancelled")
                    projects.delete_stopped(projects.get(active.id))
                    self.assertFalse(active.dir.exists())
                finally:
                    projects._terminate(proc)
