"""User-provided subtitles skip transcription and preserve source timings."""
import json
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from clipy import projects, subtitles
from clipy.captions import Word
from fastapi import HTTPException, UploadFile
from app import create_project


class SubtitleTests(unittest.TestCase):
    def test_utf8_bom_multiline_and_markup(self):
        data = ('\ufeff1\r\n00:00:01,000 --> 00:00:03,000\r\n<i>Halo</i> dunia!\r\n'
                '\r\n2\r\n00:00:03,500 --> 00:00:04,500\r\nApa kabar?\r\n').encode('utf-8')
        words = subtitles.parse_srt(data)
        self.assertEqual([w.text for w in words], ['Halo', 'dunia!', 'Apa', 'kabar?'])
        self.assertEqual(words[0].start, 1)
        self.assertEqual(words[1].end, 3)
        self.assertEqual(words[-1].end, 4.5)

    def test_rejects_invalid_content(self):
        for raw in (b'', b'\xff', b'1\nnot a timestamp\nhello',
                    b'1\n00:00:02,000 --> 00:00:01,000\nhello', b'x' * (subtitles.MAX_BYTES + 1)):
            with self.subTest(raw=raw[:30]), self.assertRaises(ValueError):
                subtitles.parse_srt(raw)

    def test_overlapping_cues_are_ordered_by_word_start(self):
        raw = b'1\n00:00:01,000 --> 00:00:05,000\nfirst last\n\n2\n00:00:02,000 --> 00:00:03,000\nother\n'
        words = subtitles.parse_srt(raw)
        self.assertEqual([w.text for w in words], ['first', 'other', 'last'])

    def test_uploaded_srt_replaces_reused_transcript(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base_dir = root / 'base'
            new_dir = root / 'new'
            base_dir.mkdir()
            new_dir.mkdir()
            (base_dir / 'words.json').write_text('[{"text":"old","start":0,"end":1}]', encoding='utf8')
            (new_dir / 'source.srt').write_text('1\n00:00:01,000 --> 00:00:03,000\nBaru\n', encoding='utf8')
            base = SimpleNamespace(dir=base_dir, title='video', duration=10)
            p = SimpleNamespace(settings={'clip': {'count': 1, 'max_duration': 60}}, local_video=None,
                                reuse_from='base', subtitle='source.srt', upload_id=None, url='url', music_url=None, music=None,
                                dir=new_dir, title='', duration=0, language=None, source_key='video', id='id')
            with patch.object(projects, 'get', return_value=base), patch.object(projects, '_clip_options', return_value={}), \
                 patch.object(projects, '_stage'), patch.object(projects, 'used_ranges', return_value=[]), \
                 patch.object(projects.select, 'pick_clips', side_effect=RuntimeError('selection reached')):
                with self.assertRaisesRegex(RuntimeError, 'selection reached'):
                    projects._run(p)
            self.assertEqual(json.loads((new_dir / 'words.json').read_text(encoding='utf8'))[0]['text'], 'Baru')

    def test_youtube_srt_does_not_download_or_transcribe_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'source.srt').write_text('1\n00:00:01,000 --> 00:00:03,000\nHalo dunia\n', encoding='utf8')
            p = SimpleNamespace(settings={'clip': {'count': 1, 'max_duration': 60}}, local_video=None, reuse_from=None, subtitle='source.srt',
                                upload_id=None, url='https://youtu.be/example', dir=root, title='', duration=0, language=None, source_key='video', id='id', music_url=None, music=None)
            with patch.object(projects, '_clip_options', return_value={}), patch.object(projects, '_stage'), \
                 patch.object(projects.media, 'video_info', return_value={'title': 'video', 'duration': 10}), \
                 patch.object(projects.media, 'download_audio', side_effect=AssertionError('audio downloaded')), \
                 patch.object(projects, 'used_ranges', return_value=[]), \
                 patch.object(projects.select, 'pick_clips', side_effect=RuntimeError('selection reached')):
                with self.assertRaisesRegex(RuntimeError, 'selection reached'):
                    projects._run(p)
            words = json.loads((root / 'words.json').read_text(encoding='utf8'))
            self.assertEqual([w['text'] for w in words], ['Halo', 'dunia'])

    def test_rejects_subtitle_past_video_end(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'source.srt').write_text('1\n00:00:01,000 --> 00:00:12,000\nHalo\n', encoding='utf8')
            p = SimpleNamespace(settings={'clip': {}}, local_video=None, reuse_from=None, subtitle='source.srt',
                                upload_id=None, url='url', dir=root, title='', duration=0, language=None, music_url=None, music=None)
            with patch.object(projects, '_clip_options', return_value={}), patch.object(projects, '_stage'), \
                 patch.object(projects.media, 'video_info', return_value={'title': 'video', 'duration': 10}):
                with self.assertRaisesRegex(ValueError, 'melewati durasi'):
                    projects._run(p)

    def test_clipping_retranscribes_reused_srt_transcript(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base_dir, new_dir = root / 'base', root / 'new'
            base_dir.mkdir()
            new_dir.mkdir()
            (base_dir / 'words.json').write_text('[{"text":"old","start":0,"end":1}]', encoding='utf8')
            base = SimpleNamespace(dir=base_dir, title='video', duration=10, transcript_origin='srt', subtitle=None)
            p = SimpleNamespace(settings={'mode': 'clipping', 'clip': {'count': 1, 'max_duration': 60}},
                                local_video=None, reuse_from='base', subtitle=None, upload_id=None, url='url', music_url=None, music=None,
                                dir=new_dir, title='', duration=0, language=None, source_key='video', id='id', save=lambda: None)
            with patch.object(projects, 'get', return_value=base), patch.object(projects, '_clip_options', return_value={}), \
                 patch.object(projects, '_stage'), patch.object(projects, '_span', return_value=None), \
                 patch.object(projects, 'used_ranges', return_value=[]), \
                 patch.object(projects.media, 'video_info', return_value={'title': 'video', 'duration': 10}), \
                 patch.object(projects.media, 'download_audio', return_value=new_dir / 'audio-src'), \
                 patch.object(projects.media, 'extract_audio', return_value=new_dir / 'audio.wav'), \
                 patch('clipy.transcribe.transcribe', return_value=([Word('fresh', 0, 1)], 'id')) as transcribe, \
                 patch.object(projects.select, 'pick_clips', side_effect=RuntimeError('selection reached')):
                with self.assertRaisesRegex(RuntimeError, 'selection reached'):
                    projects._run(p)
            transcribe.assert_called_once()
            self.assertEqual(p.transcript_origin, 'whisper')
            self.assertEqual(json.loads((new_dir / 'words.json').read_text(encoding='utf8'))[0]['text'], 'fresh')


class SubtitleModeTests(unittest.IsolatedAsyncioTestCase):
    async def test_clipping_rejects_subtitle(self):
        settings = {'mode': 'clipping', 'clip': {'ratio': '9:16', 'fit': 'crop', 'count': 1, 'max_duration': 60},
                    'caption': {'font': 'poppins', 'animation': 'pop', 'color': 'yellow', 'case': 'upper'},
                    'thumb': {'source': 'auto', 'title_style': 'bold', 'voice': None}}
        upload = UploadFile(filename='caption.srt', file=io.BytesIO(b'1\n00:00:00,000 --> 00:00:01,000\nHello\n'))
        with patch('app.keys.load', return_value={'provider': 'deepseek'}):
            with self.assertRaises(HTTPException) as error:
                await create_project(settings=json.dumps(settings), url='https://youtu.be/abcdefghijk',
                                     upload_id='', title='', reuse='', image=None, logo=None, subtitle=upload, music=None, music_url='')
        self.assertEqual(error.exception.status_code, 400)
        self.assertIn('filming', error.exception.detail)


if __name__ == '__main__':
    unittest.main()
