import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from clipy import captions
from app import ClipSettings
from clipy import film, media
from clipy.framing import Plan, Segment

class ClipLayoutTests(unittest.TestCase):
    def test_clip_shape_and_background_are_rendered(self):
        for background in ('black', 'white', 'blur'):
            with self.subTest(background=background):
                graph = media.composed_reframe(180, 320, '1:1', background, 16 / 9)
                raw = subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=red:s=320x180:d=0.1', '-filter_complex', graph, '-map', '[r]', '-frames:v', '1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'], capture_output=True, check=True).stdout
                def pixel(x, y):
                    i = (y * 180 + x) * 3
                    return raw[i:i + 3]
                center, top, bottom = pixel(90, 160), pixel(90, 20), pixel(90, 300)
                self.assertGreater(center[0], 200)
                self.assertLess(center[1], 20)
                if background == 'black':
                    self.assertLess(max(top), 10)
                    self.assertLess(max(bottom), 10)
                elif background == 'white':
                    self.assertGreater(min(top), 240)
                    self.assertGreater(min(bottom), 240)
                else:
                    self.assertGreater(top[0], 100)
                    self.assertLess(top[0], center[0])

    def test_same_ratio_inset_and_no_background_in_both_renderers(self):
        for graph in (
            media.composed_reframe(180, 320, '9:16', 'white', 16 / 9, inset=True),
            film._filming_graph(16 / 9, 180, 320, 'white', '9:16', inset=True),
        ):
            self.assertIn('scale=162:288', graph)
            self.assertIn('pad=180:320', graph)
        for graph in (
            media.composed_reframe(240, 180, '4:3', 'none', 16 / 9),
            film._filming_graph(16 / 9, 240, 180, 'none', '4:3'),
        ):
            self.assertIn('crop=240:180', graph)
            self.assertNotIn('pad=', graph)
            self.assertNotIn('boxblur=', graph)

    def test_video_and_thumbnail_use_same_clip_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = folder / 'source.mp4'
            subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i',
                            'color=red:s=320x180:r=30:d=1', '-f', 'lavfi', '-i',
                            'sine=frequency=440:duration=1', '-c:v', 'libx264',
                            '-c:a', 'aac', '-shortest', str(source)], check=True)
            ass = folder / 'caption.ass'
            ass.write_text(captions.build_ass([], captions.CaptionStyle(), 180, 320), encoding='utf-8')
            output = folder / 'output.mp4'
            with patch.object(media, 'video_encoders', return_value=[['-c:v', 'libx264', '-preset', 'ultrafast']]):
                media.render_clip(source, ass, output, 180, 320, 'crop', clip_ratio='1:1', background='white', source_ratio=16/9)
            frame = folder / 'thumb.jpg'
            media.grab_frame(source, frame, 180, 320, 'crop', clip_ratio='1:1', background='white', source_ratio=16/9)
            self.assertGreater(media.probe_duration(output), 0.8)
            audio = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'a:0',
                                    '-show_entries', 'stream=codec_type', '-of', 'csv=p=0', str(output)],
                                   capture_output=True, text=True, check=True).stdout.strip()
            self.assertEqual(audio, 'audio')
            rendered = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(output), '-frames:v', '1',
                                       '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'],
                                      capture_output=True, check=True).stdout
            top = (20 * 180 + 90) * 3
            center = (160 * 180 + 90) * 3
            self.assertGreater(min(rendered[top:top + 3]), 235)
            self.assertGreater(rendered[center], 200)
            with Image.open(frame) as image:
                self.assertEqual(image.size, (180, 320))
                self.assertGreater(min(image.getpixel((90, 20))), 235)
                self.assertGreater(image.getpixel((90, 160))[0], 200)

    def test_source_ratio_and_output_width_behavior(self):
        self.assertEqual(media.clip_frame_size(180, 'auto', 16 / 9, output_height=320), (180, 320))
        self.assertEqual(media.clip_frame_size(180, '1:1', 16 / 9), (180, 180))
        self.assertIn('crop=320:180', media.composed_reframe(320, 180, '3:4', 'black', 16 / 9))

    def test_speaker_plan_is_composed_in_crop_area(self):
        plan = Plan([Segment(0, 70, 0, 180, 180)])
        graph = media.composed_reframe(180, 320, '1:1', 'white', 16 / 9, plan)
        raw = subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                              'color=red:s=320x180:d=0.1', '-filter_complex', graph,
                              '-map', '[r]', '-frames:v', '1', '-pix_fmt', 'rgb24',
                              '-f', 'rawvideo', '-'], capture_output=True, check=True).stdout
        self.assertGreater(min(raw[(20 * 180 + 90) * 3:(20 * 180 + 90) * 3 + 3]), 240)
        self.assertGreater(raw[(160 * 180 + 90) * 3], 200)

    def test_no_crop_preserves_portrait_source_on_landscape_canvas(self):
        graph = media.composed_reframe(320, 180, 'none', 'white', 9 / 16)
        raw = subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                              'color=red:s=180x320:d=0.1', '-filter_complex', graph,
                              '-map', '[r]', '-frames:v', '1', '-pix_fmt', 'rgb24',
                              '-f', 'rawvideo', '-'], capture_output=True, check=True).stdout
        def pixel(x, y):
            i = (y * 320 + x) * 3
            return raw[i:i + 3]
        self.assertGreater(min(pixel(20, 90)), 240)
        self.assertGreater(min(pixel(300, 90)), 240)
        self.assertGreater(pixel(160, 90)[0], 200)
        self.assertLess(pixel(160, 90)[1], 20)
        # Auto now fills the selected output shape, while no-crop keeps the source.
        self.assertIn('crop=320:180', media.composed_reframe(320, 180, 'auto', 'white', 9 / 16))

    def test_five_four_settings_and_render(self):
        settings = ClipSettings(ratio='5:4', fit='crop', count=1, max_duration=60,
                                clip_ratio='5:4', background='black')
        self.assertEqual(settings.dubbing_speed, 1.0)
        self.assertEqual(media.RATIOS['5:4'], (1350, 1080))
        graph = media.composed_reframe(200, 160, '5:4', 'black', 16 / 9)
        raw = subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                              'color=red:s=320x180:d=0.1', '-filter_complex', graph,
                              '-map', '[r]', '-frames:v', '1', '-pix_fmt', 'rgb24',
                              '-f', 'rawvideo', '-'], capture_output=True, check=True).stdout
        self.assertEqual(len(raw), 200 * 160 * 3)
        self.assertGreater(raw[(80 * 200 + 100) * 3], 200)

    def test_old_settings_and_new_settings_validate(self):
        common = {'ratio': '9:16', 'fit': 'crop', 'count': 4, 'max_duration': 60}
        old = ClipSettings(**common)
        new = ClipSettings(**common, clip_ratio='1:1', background='white')
        self.assertEqual((old.clip_ratio, old.background), ('auto', 'black'))
        self.assertEqual((new.clip_ratio, new.background), ('1:1', 'white'))
        with self.assertRaises(ValueError):
            ClipSettings(**common, clip_ratio='invalid', background='white')
