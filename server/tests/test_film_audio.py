import math
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from clipy import film, tts
from clipy.captions import CaptionStyle, Word


class FilmAudioTests(unittest.TestCase):
    def test_edge_word_boundaries_are_used(self):
        class FakeVoice:
            def __init__(self, *args, **kwargs):
                assert kwargs['boundary'] == 'WordBoundary'
                assert kwargs['rate'] == '+50%'

            async def stream(self):
                yield {'type': 'audio', 'data': b'audio'}
                yield {'type': 'WordBoundary', 'offset': 1_000_000, 'duration': 4_000_000, 'text': 'Halo'}
                yield {'type': 'WordBoundary', 'offset': 9_000_000, 'duration': 3_000_000, 'text': 'dunia'}

        with tempfile.TemporaryDirectory() as tmp, patch('clipy.tts.edge_tts.Communicate', FakeVoice):
            _, words = tts.speak_aligned('Halo dunia', 'id-ID-GadisNeural', Path(tmp) / 'a.mp3', speed=1.5)
            self.assertEqual([(w.start, w.end) for w in words], [(0.1, 0.5), (0.9, 1.2)])

    def test_absent_alignment_fails(self):
        class FakeVoice:
            def __init__(self, *args, **kwargs): pass
            async def stream(self):
                yield {'type': 'audio', 'data': b'audio'}
        with tempfile.TemporaryDirectory() as tmp, patch('clipy.tts.edge_tts.Communicate', FakeVoice):
            with self.assertRaisesRegex(ValueError, 'word timing'):
                tts.speak_aligned('Halo', 'id-ID-GadisNeural', Path(tmp) / 'a.mp3')

    def test_render_has_narrator_audio_only(self):
        import numpy as np
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            src, voice = d / 'source.mp4', d / 'voice.mp3'
            subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i', 'color=blue:s=160x120:r=30:d=3', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=3', '-c:v', 'libx264', '-c:a', 'aac', str(src)], check=True)
            subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=880:duration=2', '-c:a', 'libmp3lame', str(voice)], check=True)
            p = SimpleNamespace(dir=d, upload_id='test', local_video=src, upload=None,
                                clip_asset_path=lambda clip, extension: d / f"clip_{clip['n']}.{extension}")
            clip = {'n': 1, 'shots': [{'start': 0, 'end': 2.8}], 'narration': 'Halo dunia', 'title': 'Episode'}
            opts = {'width': 180, 'height': 320, 'fit': 'crop', 'style': CaptionStyle(), 'brand': None, 'brand_position': 'bc', 'thumb_cfg': {'title_style': 'bold'}}
            with patch('clipy.film.tts.speak_aligned', return_value=(voice, [Word('Halo', 0.1, 0.5), Word('dunia', 0.9, 1.2)])), patch('clipy.media.video_encoders', return_value=[['-c:v', 'libx264', '-preset', 'ultrafast']]):
                film.render(p, clip, opts, {'max_duration': 90, 'voice': 'id-ID-GadisNeural', 'background': 'black'})
            raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(d / clip['video']), '-vn', '-ac', '1', '-ar', '8000', '-f', 'f32le', '-'], capture_output=True, check=True).stdout
            audio = np.frombuffer(raw, dtype='<f4')[:8000]
            t = np.arange(len(audio)) / 8000
            def magnitude(freq):
                return abs(np.dot(audio, np.exp(-2j * math.pi * freq * t)))
            self.assertGreater(magnitude(880), magnitude(440) * 20)
            srt = (d / clip['srt']).read_text(encoding='utf-8')
            self.assertIn('00:00:00,100', srt)

    def test_video_cuts_when_narrator_introduces_next_scene(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            src, voice = d / 'source.mp4', d / 'voice.mp3'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=red:s=160x120:r=30:d=3',
                            '-f', 'lavfi', '-i', 'color=blue:s=160x120:r=30:d=3',
                            '-filter_complex', '[0:v][1:v]concat=n=2:v=1:a=0', '-c:v', 'libx264', str(src)], check=True)
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=880:duration=4',
                            '-c:a', 'libmp3lame', str(voice)], check=True)
            (d / 'words.json').write_text('[{"text":"Merah","start":0.1,"end":0.5},{"text":"Biru","start":3.1,"end":3.5}]')
            p = SimpleNamespace(dir=d, upload_id='test', local_video=src, upload=None,
                                clip_asset_path=lambda clip, extension: d / f"clip_{clip['n']}.{extension}")
            clip = {'n': 1, 'shots': [{'start': 0, 'end': 3}, {'start': 3, 'end': 6}],
                    'narration': 'Merah. Biru.', 'title': 'Episode'}
            opts = {'width': 180, 'height': 320, 'style': CaptionStyle(position=7),
                    'brand': None, 'brand_position': 'bc', 'thumb_cfg': {}, 'dubbing_speed': 1.5}
            with patch('clipy.film.tts.speak_aligned', return_value=(voice, [Word('Merah', .1, .5), Word('Biru', 1.2, 1.6)])) as speak, \
                    patch('clipy.film.keys.ask_json', return_value={'cuts': [{'shot': 1, 'word': 1}, {'shot': 2, 'word': 2}]}), \
                    patch('clipy.media.video_encoders', return_value=[['-c:v', 'libx264', '-preset', 'ultrafast']]):
                film.render(p, clip, opts, {'max_duration': 60, 'voice': 'id-ID-GadisNeural', 'background': 'black'})
            self.assertEqual(speak.call_args.kwargs['speed'], 1.5)

            def center_pixel(frame):
                return subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(d / clip['video']),
                                                '-frames:v', '1', '-vf', f'select=eq(n\\,{frame}),crop=2:2:90:160', '-pix_fmt', 'rgb24',
                                                '-f', 'rawvideo', '-'])[:3]

            r, _, b = center_pixel(35)
            self.assertGreater(r, b + 100)
            r, _, b = center_pixel(36)
            self.assertGreater(b, r + 100)

