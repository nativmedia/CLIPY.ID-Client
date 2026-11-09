"""Run from server/: .venv/Scripts/python -m unittest discover -s tests -v."""
import json
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from clipy import captions, framing, media
from clipy.captions import Word
from clipy.framing import Plan, Segment


class FramingTests(unittest.TestCase):
    def test_profile_lips_are_not_treated_as_a_closed_mouth(self):
        points = [SimpleNamespace(x=.5, y=.5) for _ in range(478)]
        points[61] = SimpleNamespace(x=.3, y=.5)
        points[291] = SimpleNamespace(x=.7, y=.5)
        points[13] = SimpleNamespace(x=.5, y=.4)
        points[14] = SimpleNamespace(x=.5, y=.6)
        result = SimpleNamespace(face_landmarks=[points], face_blendshapes=[[
            SimpleNamespace(category_name='jawOpen', score=.001)]])
        self.assertGreater(framing._mouth_signal(result, 256, 256), .35)

    def test_missing_landmarks_uses_available_jaw_signal(self):
        result = SimpleNamespace(face_landmarks=[], face_blendshapes=[[
            SimpleNamespace(category_name='jawOpen', score=.12)]])
        self.assertEqual(framing._mouth_signal(result, 256, 256), .12)
    def samples(self, second=True, lose_focus=False, silent_partner=False):
        samples = []
        for n in range(36):
            t = n / 6
            faces = [(0.25, 0.4, 0.1 + (n % 2) * 0.15, 0.15)]
            if lose_focus and t >= 3:
                faces = []
            if second:
                faces.append((0.75, 0.4, 0.1 if silent_partner else 0.1 + ((n + 1) % 2) * 0.15, 0.15))
            samples.append((t, faces))
        return samples

    def plan(self, samples, width=1080, height=1920, cuts=()):
        with patch.object(framing, '_probe', return_value=(1920, 1080)), \
             patch.object(framing, 'sample_faces', return_value=samples), \
             patch.object(framing, 'Loudness', return_value=lambda t: 0.1), \
             patch.object(framing, 'shot_cuts', return_value=list(cuts)):
            return framing.plan_crop(Path('unused.mp4'), width, height, [Word('talk', 0, 6)])

    def test_blur_is_rejected(self):
        image = np.random.default_rng(42).integers(0, 255, (160, 160, 3), dtype=np.uint8)
        blurred = cv2.GaussianBlur(image, (31, 31), 8)
        self.assertGreater(framing._sharpness(image, (0, 0, 160, 160)), framing.MIN_SHARPNESS)
        self.assertLess(framing._sharpness(blurred, (0, 0, 160, 160)), framing.MIN_SHARPNESS)


    def group_samples(self, count=3, seconds=6):
        return [(n / 6, [(0.18 + i * 0.64 / max(1, count - 1), 0.4,
                           0.1 + ((n + i) % 2) * 0.1, 0.12) for i in range(count)])
                for n in range(seconds * 6)]




    def test_wide_view_is_centered_six_by_four(self):
        for sw, sh in ((1920, 1080), (1080, 1920)):
            x, y, w, h = framing._center_wide(sw, sh)
            self.assertAlmostEqual(w / h, 6 / 4, delta=0.01)
            self.assertAlmostEqual(x + w / 2, sw / 2, delta=2)
            self.assertAlmostEqual(y + h / 2, sh / 2, delta=2)

    def test_wide_does_not_cut_off_people_at_source_edges(self):
        samples = [(n / 6, [(0.04, 0.4, (n % 2) * .1, .12),
                            (0.96, 0.4, ((n + 1) % 2) * .1, .12)]) for n in range(150)]
        self.assertFalse(any(s.wide for s in self.plan(samples).segments))

    def test_layout_change_waits_for_nearby_speaker_handoff(self):
        with patch.object(framing, '_hold', return_value=[(0, 0), (8.5, 1)]):
            plan = self.plan(self.group_samples(seconds=18))
        self.assertNotIn(8, [s.start for s in plan.segments])
        self.assertIn(8.5, [s.start for s in plan.segments])

    def test_two_visible_listeners_do_not_force_collage(self):
        plan = self.plan(self.samples(silent_partner=True))
        self.assertTrue(all(s.layout == 'single' for s in plan.segments))

    def test_three_visible_people_do_not_force_collage(self):
        plan = self.plan(self.group_samples())
        self.assertTrue(all(s.layout == 'single' for s in plan.segments))

    def test_long_quiet_group_stays_on_speaker_without_timer_variation(self):
        samples = [(n / 6, [(0.25, .4, .1 + (n % 2) * .08, .15),
                            (.75, .4, .1, .15)]) for n in range(240)]
        plan = self.plan(samples)
        self.assertTrue(all(s.layout == 'single' for s in plan.segments))
        self.assertEqual(len(plan.segments), 1)

    def test_animated_response_is_brief_and_returns_to_speaker(self):
        plan = self.plan(self.group_samples(seconds=40))
        collages = [(a, b.start) for a,b in zip(plan.segments,plan.segments[1:]) if a.secondary is not None]
        self.assertTrue(collages)
        self.assertEqual(plan.segments[0].layout, 'single')
        self.assertEqual(plan.segments[-1].layout, 'single')
        self.assertLessEqual(sum(end-a.start for a,end in collages), 40*.15)
        self.assertTrue(all(end-a.start <= 4.01 for a,end in collages))
        self.assertTrue(all(a.tertiary is not None for a,end in collages))

    def test_reaction_budget_and_cooldown_block_more_collages(self):
        samples = self.group_samples(seconds=40)
        people = framing._people(samples)
        for p in people:
            p.track = [(t,j,True,h,y) for t,j,_,h,y in p.track]
        self.assertEqual(framing._reaction_runs(people,0,40,[(0,0)],3,0),[(0,'single')])
        self.assertEqual(framing._reaction_runs(people,0,40,[(0,0)],8,38),[(0,'single')])

    def test_recorded_profile_follows_speaker_without_default_collage(self):
        samples = json.loads((Path(__file__).parent/'fixtures/profile_conversation.json').read_text())
        segment = self.plan(samples).at(1)
        self.assertEqual(segment.layout,'single')
        self.assertAlmostEqual((segment.x+segment.w/2)/1920,.298,delta=.04)

    def test_other_ratios_remain_single(self):
        plan = self.plan(self.samples(), 1080, 1350)
        self.assertTrue(all(s.secondary is None for s in plan.segments))



    def test_noisy_face_measurements_do_not_rezoom_same_single_subject(self):
        samples = [(n / 6, [(0.4, 0.4 + 0.01 * np.sin(n / 9),
                              0.1 + (n % 2) * 0.15, 0.17 + 0.015 * np.sin(n / 11))])
                   for n in range(108)]
        plan = self.plan(samples)
        self.assertEqual(len(plan.segments), 1)
        self.assertIsNone(plan.segments[0].secondary)

    def test_real_camera_cut_can_change_crop(self):
        samples = [(n / 6, [(0.25 if n < 18 else 0.75, 0.4,
                              0.1 + (n % 2) * 0.15, 0.17)]) for n in range(36)]
        plan = self.plan(samples, cuts=(3,))
        self.assertEqual(len(plan.segments), 2)
        self.assertEqual(plan.segments[1].start, 3)
        self.assertGreater(plan.segments[1].x, plan.segments[0].x)


    def test_single_view_switches_to_new_speaker_instead_of_microzoom(self):
        samples = self.samples()
        samples += [(t + 6, faces) for t, faces in self.samples()]
        with patch.object(framing, '_hold', return_value=[(0, 0), (6, 1)]):
            plan = self.plan(samples, width=1080, height=1350)
        self.assertEqual(len(plan.segments), 2)
        self.assertEqual(plan.segments[1].start, 6)
        self.assertGreater(plan.segments[1].x, plan.segments[0].x)

    def test_speaker_switch_waits_two_seconds_of_past_evidence(self):
        calls = [0] * 8 + [1] * 16
        with patch.object(framing, '_speaker_at', side_effect=calls):
            runs = framing._hold([None, None], 0, 12, lambda _: 0)
        self.assertEqual(runs, [(0, 0), (6.0, 1)])

    def test_brief_speaker_predictions_do_not_change_camera(self):
        calls = [0] * 8 + [1, 1, 1, None, 1, 1, 0, 1] + [0] * 8
        with patch.object(framing, '_speaker_at', side_effect=calls):
            runs = framing._hold([None, None], 0, 12, lambda _: 0)
        self.assertEqual(runs, [(0, 0)])

    def test_subtle_talking_mouth_beats_still_center_listener(self):
        def person(cx, moving):
            track = [(n / 6, .1 + (n % 2) * (.009 if moving else .0001), True, .15, .4)
                     for n in range(36)]
            return framing.Person(cx, track, [cx] * 36)
        people = [person(.5, False), person(.8, True)]
        self.assertEqual(framing._speaker_at(people, 2, lambda _: .1), 1)

    def test_correlation_on_still_listener_does_not_override_speaker(self):
        track = lambda amplitude: [(n / 6, .1 + (n % 2) * amplitude, True, .15, .4) for n in range(36)]
        people = [framing.Person(.5, track(.0001), [.5]), framing.Person(.8, track(.01), [.8])]
        with patch.object(framing, '_sync', return_value=.8):
            self.assertEqual(framing._speaker_at(people, 2, lambda _: .1), 1)
            self.assertEqual(framing._overall_speaker(people, lambda _: .1), 1)


    def test_third_person_does_not_appear_as_half_face_in_panel(self):
        samples = self.samples()
        for n, (_, faces) in enumerate(samples):
            faces.append((0.59, 0.4, 0.08 + (n % 2) * 0.05, 0.13))
        people = framing._people(samples)
        right = max(people, key=lambda p: p.cx)
        x, y, w, h = framing._panel_crop(right, people, 0, 6, 1920, 1080, 9 / 8)
        # The neighboring person's face and a margin should sit outside this crop.
        self.assertGreater(x, (0.59 + 0.03) * 1920)
        self.assertAlmostEqual(w / h, 9 / 8, delta=0.02)

    def test_closeup_captions_move_below_faces_without_changing_animation(self):
        source = r'Dialogue: 0,0:00:01.00,0:00:02.00,Cap,,0,0,0,,{\pos(540,1390)\t(0,90,\fscx106)}Word'
        result = captions.for_stacked_panels(source, 1920)
        self.assertIn(r'\pos(540,1690)', result)
        self.assertEqual(result.replace('1690', '1390'), source)


    def test_small_background_bystander_does_not_trigger_split(self):
        samples = self.samples(silent_partner=True)
        for _, faces in samples:
            cx, cy, jaw, _ = faces[1]
            faces[1] = (cx, cy, jaw, 0.065)
        self.assertTrue(all(s.secondary is None for s in self.plan(samples).segments))

    def test_crisp_audience_does_not_disqualify_foreground_pair(self):
        # Focus/size measurements from the reported wide shot, around 46 seconds.
        candidates = [(459.2, 1100, 400, 80, 65), (432.9, 1700, 400, 80, 68),
                      (98.8, 550, 400, 100, 108), (111.6, 1350, 400, 100, 130)]
        self.assertEqual(framing._clear_foreground(candidates), candidates[2:])

    def test_blurred_background_cannot_share_closeup_panel(self):
        candidates = [(773.0, 800, 300, 200, 265), (6.0, 400, 400, 100, 128)]
        self.assertEqual(framing._clear_foreground(candidates), candidates[:1])

    def test_clear_profile_survives_sharper_listener_in_reported_clip(self):
        candidates = [(210, 500, 300, 180, 215), (1181, 1100, 300, 180, 225)]
        self.assertEqual(framing._clear_foreground(candidates), candidates)

    def test_reported_profile_speaker_is_not_removed_by_short_detection_gaps(self):
        samples = json.loads((Path(__file__).parent / 'fixtures/profile_conversation.json').read_text())
        people = framing._people(samples)
        speaker = min(people, key=lambda p: p.cx)
        self.assertTrue(framing._visible(speaker, 0, 8.98))
        segment = self.plan(samples).at(1)
        crops = [(segment.x, segment.y, segment.w, segment.h)]
        if segment.secondary is not None:
            crops.append(segment.secondary)
        self.assertTrue(any(x <= speaker.cx * 1920 <= x + w for x, y, w, h in crops))

    def test_reported_real_closeup_remains_cropped(self):
        fixture = json.loads((Path(__file__).parent / 'fixtures/reported_conversation.json').read_text())
        segment = self.plan(fixture['closeup']).at(2)
        self.assertFalse(segment.fit)
        self.assertIsNone(segment.secondary)
        self.assertAlmostEqual((segment.x + segment.w / 2) / 1920, 0.48, delta=0.04)



    def test_temporary_detection_loss_keeps_subject_crop(self):
        plan = self.plan(self.samples(second=False, lose_focus=True))
        self.assertFalse(plan.at(0).fit)
        self.assertFalse(plan.at(4).fit)
        self.assertEqual(plan.at(0).x, plan.at(4).x)

    def test_no_faces_at_camera_cut_still_fills_frame(self):
        plan = self.plan(self.samples(second=False, lose_focus=True), cuts=(3,))
        self.assertFalse(plan.at(3).fit)
        self.assertAlmostEqual(plan.at(3).w / plan.at(3).h, 9 / 16, delta=0.01)

    def test_no_detections_never_enables_fit_in_crop_mode(self):
        plan = self.plan([(n / 6, []) for n in range(36)])
        self.assertTrue(all(not s.fit for s in plan.segments))

    def test_single_speaker(self):
        plan = self.plan(self.samples(second=False))
        self.assertTrue(all(not s.fit and s.secondary is None for s in plan.segments))

    def test_quality_checks_do_not_create_flash_before_cut(self):
        plan = self.plan(self.samples(second=False), cuts=(3.2,))
        self.assertFalse(any(0 < b.start - a.start < framing.MIN_HOLD
                             for a, b in zip(plan.segments, plan.segments[1:])))

    def test_ffmpeg_mixed_layouts_and_thumbnail(self):
        plan = Plan([
            Segment(0, 0, 0, 200, 360),
            Segment(1, 0, 0, 320, 284, secondary=(320, 0, 320, 284)),
            Segment(2, 0, 0, 640, 360, fit=True),
        ])
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'render.mp4'
            graph = media.reframe(180, 320, 'crop', plan)
            subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i',
                            'testsrc2=size=640x360:rate=12:duration=3', '-filter_complex', graph,
                            '-map', '[r]', '-c:v', 'libx264', '-threads', '2', str(out)],
                           check=True, capture_output=True)

            result = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(out)]))
            stream = result['streams'][0]
            self.assertEqual((stream['width'], stream['height']), (180, 320))
            self.assertAlmostEqual(float(stream['duration']), 3.0, delta=0.1)
            graph = media.reframe(180, 320, 'crop', plan, still_at=1.5)
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                            'testsrc2=size=640x360:rate=12:duration=1', '-filter_complex', graph,
                            '-map', '[r]', '-frames:v', '1', '-f', 'null', '-'],
                           check=True, capture_output=True)

    def test_ffmpeg_triple_wide_and_thumbnail_match(self):
        plan = Plan([
            Segment(0, 0, 0, 200, 360),
            Segment(1, 0, 0, 200, 180, secondary=(440, 0, 200, 180)),
            Segment(2, 0, 0, 200, 120, secondary=(220, 0, 200, 120), tertiary=(440, 0, 200, 120)),
            Segment(3, 50, 0, 540, 360, wide=True),
        ])
        source = 'color=red:s=640x360:r=12:d=4,drawbox=x=220:y=0:w=200:h=360:c=lime:t=fill,drawbox=x=440:y=0:w=200:h=360:c=blue:t=fill'
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'layouts.mp4'
            subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i', source,
                            '-filter_complex', media.reframe(180, 320, 'crop', plan), '-map', '[r]',
                            '-c:v', 'libx264', '-threads', '2', str(out)], check=True, capture_output=True)
            cap = cv2.VideoCapture(str(out))
            self.assertEqual((int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))), (180, 320))
            self.assertAlmostEqual(cap.get(cv2.CAP_PROP_FRAME_COUNT) / cap.get(cv2.CAP_PROP_FPS), 4, delta=.1)
            cap.set(cv2.CAP_PROP_POS_MSEC, 2400)
            ok, triple = cap.read()
            self.assertTrue(ok)
            # OpenCV BGR: red above, green in the middle, blue below.
            self.assertGreater(int(triple[30, 90, 2]), 200)
            self.assertGreater(int(triple[160, 90, 1]), 200)
            self.assertGreater(int(triple[290, 90, 0]), 200)
            cap.set(cv2.CAP_PROP_POS_MSEC, 3400)
            ok, wide = cap.read()
            cap.release()
            self.assertTrue(ok)
            self.assertGreater(int(wide[160, 10, 2]), 200)  # sharp 6:4 foreground
            self.assertGreater(int(wide[20, 10, 1]), int(wide[20, 10, 2]))  # blurred portrait background
            for t in (2.4, 3.4):
                image = Path(directory) / f'{t}.png'
                subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i', source,
                                '-filter_complex', media.reframe(180, 320, 'crop', plan, still_at=t),
                                '-map', '[r]', '-frames:v', '1', str(image)], check=True, capture_output=True)
                still = cv2.imread(str(image))
                expected = triple if t < 3 else wide
                self.assertLess(float(np.mean(np.abs(still.astype(float) - expected.astype(float)))), 8)


if __name__ == '__main__':
    unittest.main()
