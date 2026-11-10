import json
import shutil
import subprocess
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app import ClipSettings, FilmSettings
from clipy import film, media


RATIOS = ['16:9', '4:3', '1:1', '4:5', '9:16']


class SelectOptions(HTMLParser):
    def __init__(self):
        super().__init__()
        self.select = None
        self.options = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'select':
            self.select = attrs.get('id')
        elif tag == 'option' and self.select and 'hidden' not in attrs:
            self.options.setdefault(self.select, []).append(attrs.get('value', ''))

    def handle_endtag(self, tag):
        if tag == 'select':
            self.select = None


class CropOptionsTests(unittest.TestCase):
    def test_ui_and_api_accept_requested_ratios(self):
        parser = SelectOptions()
        html = (Path(__file__).resolve().parents[2] / 'web/index.html').read_text(encoding='utf-8')
        parser.feed(html)
        self.assertEqual(parser.options['film-ratio'], RATIOS)
        self.assertNotIn('film-source-ratio', html)
        self.assertNotIn('opt-clip-ratio', html)
        self.assertEqual(parser.options['film-background'], ['black', 'white', 'blur'])
        self.assertEqual(parser.options['opt-background'], ['black', 'white', 'blur'])
        self.assertEqual(list(media.RATIOS), [*RATIOS, '5:4'])
        for ratio in [*RATIOS, '5:4']:
            self.assertEqual(ClipSettings(ratio=ratio, fit='crop', count=1, max_duration=60).ratio, ratio)
        for mode in ('none', 'blackbar', 'auto', '3:4'):
            self.assertEqual(FilmSettings(source_ratio=mode).source_ratio, mode)
            if 'clip_ratio' in ClipSettings.model_fields:
                clip = ClipSettings(ratio='9:16', fit='crop', count=1, max_duration=60, clip_ratio=mode)
                self.assertEqual(clip.clip_ratio, mode)
        self.assertEqual(ClipSettings(ratio='9:16', fit='crop', count=1, max_duration=60).clip_ratio, 'auto')
        self.assertEqual(ClipSettings(ratio='9:16', fit='crop', count=1, max_duration=60).background, 'black')
        self.assertEqual(FilmSettings().background, 'black')

    def test_crop_modes_choose_the_right_detection(self):
        path = Path('example.mp4')
        with patch.object(media, 'letterbox', return_value=20) as legacy, \
                patch.object(media, 'black_bars', return_value=(12, 20)) as detect:
            self.assertEqual(media.crop_insets(path, 'none'), 0)
            legacy.assert_not_called()
            detect.assert_not_called()
            self.assertEqual(media.crop_insets(path, 'blackbar'), (12, 20))
            self.assertEqual(media.crop_insets(path, '4:5'), 20)

    def test_no_crop_keeps_tall_picture_inside_wide_output(self):
        graph = film._filming_graph(9 / 16, 320, 180, 'white', 'none')
        self.assertIn('pad=320:180', graph)
        self.assertNotIn('crop=320:180', graph)
        if hasattr(media, 'composed_reframe'):
            graph = media.composed_reframe(320, 180, 'blackbar', 'white', 9 / 16)
            self.assertIn('pad=320:180', graph)

    def test_inconsistent_bars_do_not_crop_the_picture(self):
        outputs = [SimpleNamespace(stderr=value) for value in (
            'crop=320:300:0:30', 'crop=320:360:0:0', 'crop=320:300:0:30')]
        with patch.object(media, 'probe_duration', return_value=1), \
                patch.object(media.subprocess, 'run', side_effect=outputs):
            self.assertEqual(media._sample_black_bars(Path('changing.mp4'), 1, 1), (0, 0))

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
    def test_black_bars_are_detected_and_removed_on_both_axes(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'bars.mp4'
            cropped = Path(folder) / 'cropped.mp4'
            subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i',
                'color=c=red:s=240x120:r=10:d=1', '-vf', 'pad=320:180:40:30:color=black',
                '-c:v', 'libx264', '-threads', '1', str(source)], check=True, capture_output=True)
            inset = media.black_bars(source)
            self.assertGreaterEqual(inset[0], 36)
            self.assertGreaterEqual(inset[1], 54)
            subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', str(source),
                '-vf', media.blackbar_crop(inset), '-c:v', 'libx264', '-threads', '1',
                str(cropped)], check=True, capture_output=True)
            info = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                '-show_entries', 'stream=width,height', '-of', 'json', str(cropped)],
                check=True, capture_output=True, text=True)
            stream = json.loads(info.stdout)['streams'][0]
            self.assertLessEqual(stream['width'], 248)
            self.assertLessEqual(stream['height'], 128)
            graph = film._filming_graph(320 / 180, 320, 180, 'white', 'blackbar', inset)
            self.assertIn(media.blackbar_crop(inset), graph)


if __name__ == '__main__':
    unittest.main()
