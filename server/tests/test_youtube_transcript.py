import io
import json
import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from clipy import projects, select, transcribe, whisper_vulkan, youtube_transcript
from clipy.captions import Word


def payload(events):
    return json.dumps({'events': events}).encode()


class YouTubeTranscriptTests(unittest.TestCase):
    def test_word_offsets_are_preserved(self):
        data = payload([{'tStartMs': 1000, 'dDurationMs': 2000, 'segs': [
            {'utf8': 'Halo'}, {'utf8': ' dunia', 'tOffsetMs': 500}]}])
        words, precision = youtube_transcript.parse_json3(data, 10)
        self.assertEqual(precision, 'kata')
        self.assertEqual([(w.text, w.start, w.end) for w in words], [('Halo', 1, 1.5), ('dunia', 1.5, 3)])
        start, end = select._snap(words, 1, 1.5, 5)
        self.assertLessEqual(start, 1)
        self.assertEqual(end, 1.5)

    def test_sentence_cues_are_preserved_as_atomic_segments(self):
        data = payload([{'tStartMs': 1000, 'dDurationMs': 1000, 'segs': [{'utf8': 'Halo dunia.'}]},
                        {'tStartMs': 2500, 'dDurationMs': 1000, 'segs': [{'utf8': 'Apa kabar?'}]}])
        words, precision = youtube_transcript.parse_json3(data, 10)
        self.assertEqual(precision, 'segmen')
        self.assertEqual([w.text for w in words], ['Halo dunia.', 'Apa kabar?'])
        start, end = select._snap(words, 1, 2, 5)
        self.assertLessEqual(start, 1)
        self.assertGreaterEqual(end, 2)
        self.assertLessEqual(end, 2.5)

    def test_overlapping_sentence_cues_remain_available_as_one_segment(self):
        data = payload([{'tStartMs': 1000, 'dDurationMs': 2000, 'segs': [{'utf8': 'Halo dunia.'}]},
                        {'tStartMs': 2500, 'dDurationMs': 1000, 'segs': [{'utf8': 'Apa kabar?'}]}])
        words, precision = youtube_transcript.parse_json3(data, 10)
        self.assertEqual(precision, 'segmen')
        self.assertEqual(len(words), 1)
        self.assertIn('Halo dunia.', words[0].text)
        self.assertIn('Apa kabar?', words[0].text)

    def test_vtt_sentence_track_is_accepted(self):
        data = b'WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nHello world.\n'
        words, precision = youtube_transcript.parse_cues(data, 10)
        self.assertEqual((precision, words[0].text, words[0].start, words[0].end), ('segmen', 'Hello world.', 1, 3))

    def test_manual_track_precedes_automatic(self):
        data = payload([{'tStartMs': 1000, 'dDurationMs': 1000, 'segs': [{'utf8': 'Halo dunia.'}]}])
        response = MagicMock(); response.__enter__.return_value = io.BytesIO(data)
        ydl = MagicMock(); ydl.__enter__.return_value = ydl; ydl.urlopen.return_value = response
        info = {'duration': 10, 'language': 'id', 'subtitles': {'id': [{'ext': 'json3', 'url': 'https://www.youtube.com/api/timedtext?manual'}]},
                'automatic_captions': {'id': [{'ext': 'json3', 'url': 'https://www.youtube.com/api/timedtext?auto'}]}}
        with patch.object(youtube_transcript.yt_dlp, 'YoutubeDL', return_value=ydl):
            words, origin, language, reason, timing = youtube_transcript.fetch(info)
        self.assertEqual((origin, language, timing), ('youtube_manual', 'id', 'segmen'))
        self.assertEqual(words[0].text, 'Halo dunia.')
        self.assertEqual(ydl.urlopen.call_count, 1)

    def test_unavailable_captions_explain_fallback(self):
        with patch.object(youtube_transcript.yt_dlp, 'YoutubeDL'):
            result = youtube_transcript.fetch({'duration': 10})
        self.assertIsNone(result[0])
        self.assertIn('tidak tersedia', result[3])

    def test_other_automatic_languages_are_tried_after_original(self):
        tracks = {'id-orig': [], 'en': [], 'ja': []}
        self.assertEqual(youtube_transcript._languages(tracks, 'id', True), ['id-orig', 'en', 'ja'])

    def test_coarse_youtube_track_drives_selection_without_local_audio(self):
        with tempfile.TemporaryDirectory() as folder:
            project = SimpleNamespace(settings={'clip': {'count': 1, 'max_duration': 60}}, local_video=None,
                reuse_from=None, subtitle=None, upload_id=None, music_url=None, music=None, url='https://youtube.com/watch?v=example',
                dir=Path(folder), title='', duration=0, language=None, source_key='source', id='id', save=lambda: None)
            source = [Word('Kalimat pertama.', 1, 5), Word('Kalimat berikutnya.', 6, 9)]
            with patch.object(projects, '_clip_options', return_value={}), patch.object(projects, '_stage'), \
                 patch.object(projects.media, 'video_info', return_value={'title': 'Video', 'duration': 10}), \
                 patch.object(youtube_transcript, 'fetch', return_value=(source, 'youtube_manual', 'id', '', 'segmen')), \
                 patch.object(projects.media, 'download_audio', side_effect=AssertionError('local transcription started')), \
                 patch.object(projects, 'used_ranges', return_value=[]), \
                 patch.object(projects.select, 'pick_clips', side_effect=RuntimeError('selection reached')) as pick:
                with self.assertRaisesRegex(RuntimeError, 'selection reached'):
                    projects._run(project)
            self.assertEqual(project.transcript_origin, 'youtube_manual')
            self.assertEqual(project.transcript_timing, 'segmen')
            self.assertEqual(pick.call_args.args[0], source)
            self.assertTrue(pick.call_args.kwargs['allow_approximate'])

    def test_approximate_boundary_keeps_coarse_text_when_segment_exceeds_duration(self):
        words = [Word('Kalimat panjang tanpa timestamp kata.', 0, 100)]
        picked = {'clips': [{'start': 20, 'end': 50, 'title': 'Contoh', 'reason': 'Alasan'}]}
        with patch.object(select.keys, 'ask_json', return_value=picked):
            notices = []
            clips = select.pick_clips(words, 1, 30, 'id', allow_approximate=True,
                                      on_approximate=lambda: notices.append(True))
        self.assertEqual((clips[0].start, clips[0].end), (20, 50))
        self.assertTrue(notices)

    def test_gpu_failure_falls_back_to_cpu_with_reason(self):
        status = []
        with patch.object(transcribe, 'device', return_value='cuda'), \
             patch.object(transcribe, '_gpu_process', side_effect=RuntimeError('memori GPU tidak mencukupi')), \
             patch.object(transcribe, '_backend_process', return_value=([Word('oke', 0, 1)], 'id')) as cpu:
            words, lang = transcribe.transcribe('sample.wav', on_status=lambda *args: status.append(args))
        self.assertEqual(lang, 'id')
        self.assertEqual(cpu.call_args.kwargs['dev'], 'cpu')
        self.assertIn('memori GPU', status[-1][1])
        self.assertEqual(words[0].text, 'oke')

    def test_vulkan_detects_amd_and_integrated_intel(self):
        output = '''GPU0:
    vendorID = 0x1002
    deviceType = PHYSICAL_DEVICE_TYPE_DISCRETE_GPU
    deviceName = AMD Radeon
GPU1:
    vendorID = 0x8086
    deviceType = PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU
    deviceName = Intel Graphics
GPU2:
    vendorID = 0x8086
    deviceType = PHYSICAL_DEVICE_TYPE_CPU
    deviceName = Software
'''
        self.assertEqual(whisper_vulkan.parse_devices(output), [(0, 'AMD', 'AMD Radeon'), (1, 'Intel', 'Intel Graphics')])

    def test_vulkan_failure_uses_cpu_and_reports_reason(self):
        status = []
        def run(audio, language, progress, *, dev, **kwargs):
            if dev == 'vulkan':
                raise RuntimeError('memori GPU Vulkan tidak cukup')
            return [Word('oke', 0, 1)], 'id'
        with patch.object(transcribe, 'device', return_value='cpu'), \
             patch.object(whisper_vulkan, 'detect_device', return_value=(0, 'AMD', 'AMD Radeon')), \
             patch.object(transcribe, '_backend_process', side_effect=run):
            words, language = transcribe.transcribe('sample.wav', on_status=lambda *args: status.append(args))
        self.assertEqual(language, 'id')
        self.assertEqual(words[0].text, 'oke')
        self.assertIn('GPU AMD', status[0][0])
        self.assertIn('memori', status[-1][1])


if __name__ == '__main__':
    unittest.main()
