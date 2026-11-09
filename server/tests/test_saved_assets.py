"""Saved settings only expose referenced image uploads, never arbitrary project files."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from app import saved_project_asset


class SavedAssetTests(unittest.TestCase):
    def test_original_logo_and_thumbnail_are_available(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'logo.png').write_bytes(b'logo')
            (root / 'upload.webp').write_bytes(b'image')
            project = SimpleNamespace(dir=root, logo='logo.png', upload='upload.webp', music=None)
            with patch('app.projects.get', return_value=project):
                self.assertEqual(saved_project_asset('id', 'logo').media_type, 'image/png')
                self.assertEqual(saved_project_asset('id', 'image').media_type, 'image/webp')

    def test_other_kinds_and_paths_are_rejected(self):
        project = SimpleNamespace(dir=Path('.'), logo='../settings.json', upload=None, music=None)
        with patch('app.projects.get', return_value=project):
            for kind in ('logo', 'image', 'settings', 'words.json'):
                with self.assertRaises(HTTPException) as error:
                    saved_project_asset('id', kind)
                self.assertEqual(error.exception.status_code, 404)

    def test_missing_project_is_not_found(self):
        with patch('app.projects.get', return_value=None):
            with self.assertRaises(HTTPException) as error:
                saved_project_asset('missing', 'logo')
            self.assertEqual(error.exception.status_code, 404)
