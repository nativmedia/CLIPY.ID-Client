import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from clipy import media


class FfmpegRunnerTests(unittest.TestCase):
    def test_large_stderr_does_not_block_progress(self):
        # A child writes more than an OS pipe can hold before reporting progress.
        real_popen = subprocess.Popen
        script = "import sys; sys.stderr.write('x'*262144); sys.stderr.flush(); print('out_time_us=500000', flush=True)"
        def spawn(*args, **kwargs):
            return real_popen([sys.executable, '-c', script], **kwargs)
        progress = []
        with tempfile.TemporaryDirectory() as folder, patch.object(media, 'video_encoders', return_value=[[]]), patch.object(media.subprocess, 'Popen', side_effect=spawn):
            media._run_ffmpeg(['output.mp4'], Path(folder), 1, progress.append)
        self.assertEqual(progress, [0.5])

    def test_failure_reports_end_of_error(self):
        real_popen = subprocess.Popen
        script = "import sys; sys.stderr.write('x'*5000 + ' useful failure'); sys.exit(1)"
        def spawn(*args, **kwargs):
            return real_popen([sys.executable, '-c', script], **kwargs)
        with tempfile.TemporaryDirectory() as folder, patch.object(media, 'video_encoders', return_value=[[]]), patch.object(media.subprocess, 'Popen', side_effect=spawn):
            with self.assertRaisesRegex(RuntimeError, 'useful failure'):
                media._run_ffmpeg(['output.mp4'], Path(folder), 1)


if __name__ == '__main__':
    unittest.main()
