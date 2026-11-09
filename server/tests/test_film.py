import unittest
import subprocess
from unittest.mock import patch

from clipy.captions import Word
from clipy import film


class FilmPlanningTests(unittest.TestCase):
    def setUp(self):
        self.words = [Word("Halo.", 0, 1), Word("Selesai.", 10, 11)]
        self.config = {"episodes": 1, "max_duration": 90, "source_type": "recap", "style": "serius", "voice": "id-ID-GadisNeural", "story_mode": "standalone"}
        editor = patch("clipy.film._write_scripts", side_effect=lambda episodes, *_: episodes)
        editor.start()
        self.addCleanup(editor.stop)

    def test_accepts_short_ordered_shots(self):
        response = {"episodes": [{"title": "A", "narration": "Halo", "shots": [{"start": 0, "end": 5}, {"start": 10, "end": 14}]}]}
        with patch("clipy.film.keys.ask_json", side_effect=[{"summary": "Halo"}, response]):
            self.assertEqual(len(film.plan(self.words, 20, self.config)), 1)

    def test_uncertain_source_is_neutral(self):
        response = {"episodes": [{"title": "A", "narration": "Halo", "shots": [{"start": 0, "end": 5}]}]}
        with patch("clipy.film.keys.ask_json", side_effect=[{"summary": "Halo", "source_type": "unknown"}, response]):
            film.plan(self.words, 20, self.config)
        self.assertEqual(self.config["detected_source_type"], "unknown")

    def test_background_only_when_ratio_differs(self):
        self.assertNotIn("pad=", film._filming_graph(16 / 9, 1920, 1080, "black"))
        self.assertIn("color=white", film._filming_graph(16 / 9, 1080, 1920, "white"))
        self.assertIn("boxblur", film._filming_graph(16 / 9, 1080, 1920, "blur"))

    def test_clip_shape_is_centered_on_output(self):
        # A 16:9 red source, cropped to 1:1 on a 9:16 black canvas.
        graph = film._filming_graph(16 / 9, 180, 320, "black", "1:1")
        raw = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=red:s=320x180:d=0.1", "-filter_complex", graph, "-map", "[r]", "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
        def pixel(x, y):
            i = (y * 180 + x) * 3
            return raw[i:i + 3]
        self.assertLess(max(pixel(90, 20)), 10)
        self.assertGreater(pixel(90, 160)[0], 200)
        self.assertLess(max(pixel(90, 300)), 10)

    def test_tall_clip_fills_output_width(self):
        graph = film._filming_graph(16 / 9, 320, 180, "black", "3:4")
        self.assertIn("crop=320:180", graph)
        self.assertNotIn("pad=", graph)

    def test_additional_prompt_reaches_planner(self):
        response = {"episodes": [{"title": "A", "narration": "Halo", "shots": [{"start": 0, "end": 5}]}]}
        with patch("clipy.film.keys.ask_json", side_effect=[{"summary": "Halo"}, response]) as ask:
            film.plan(self.words, 20, {**self.config, "additional_prompt": "Fokus pada persahabatan"})
            self.assertIn("Fokus pada persahabatan", ask.call_args.args[1])

    def test_rejects_oversized_shot(self):
        response = {"episodes": [{"title": "A", "narration": "Halo", "shots": [{"start": 0, "end": 5.1}]}]}
        with patch("clipy.film.keys.ask_json", side_effect=[{"summary": "Halo"}, response, response, response]):
            with self.assertRaises(ValueError):
                film.plan(self.words, 20, self.config)

    def test_rejects_missing_episodes(self):
        with patch("clipy.film.keys.ask_json", side_effect=[{"summary": "Halo"}, {"episodes": []}, {"episodes": []}, {"episodes": []}]):
            with self.assertRaises(ValueError):
                film.plan(self.words, 20, self.config)

    def test_retries_with_actionable_feedback_and_accepts_corrected_plan(self):
        invalid = {"episodes": [{"title": "A", "narration": "Halo", "shots": [{"start": 3, "end": 9}]}]}
        valid = {"episodes": [{"title": "A", "narration": "Halo", "shots": [{"start": 3, "end": 8}]}]}
        with patch("clipy.film.keys.ask_json", side_effect=[{"summary": "Halo"}, invalid, valid]) as ask:
            self.assertEqual(film.plan(self.words, 20, self.config), valid["episodes"])
            self.assertIn("Episode 1, shot 1", ask.call_args.args[1])
            self.assertIn("<=5s", ask.call_args.args[1])

    def test_order_validation_spans_episodes(self):
        config = {**self.config, "episodes": 2}
        response = {"episodes": [
            {"narration": "A", "shots": [{"start": 10, "end": 14}]},
            {"narration": "B", "shots": [{"start": 5, "end": 8}]},
        ]}
        with self.assertRaisesRegex(ValueError, "previous shot start"):
            film._validate_plan(response, 20, config)

    def test_replans_when_episode_uses_too_little_of_duration_budget(self):
        short = {"episodes": [{"narration": "Paul tiba.", "shots": [{"start": 0, "end": 5}]}]}
        extended = {"episodes": [{"narration": "Paul tiba dan menghadapi serangan.",
                                  "shots": [{"start": i, "end": i + 5} for i in range(0, 55, 5)]}]}
        config = {**self.config, "max_duration": 60}
        with patch("clipy.film.keys.ask_json", side_effect=[{"summary": "Paul"}, short, extended]) as ask:
            self.assertEqual(film.plan(self.words, 120, config), extended["episodes"])
        self.assertIn("episode 1: 5.0s", ask.call_args.args[1])

    def test_serial_writes_one_story_then_splits_it_without_changing_words(self):
        story = " ".join(f"kata{i}" for i in range(48))
        response = {"episodes": [
            {"title": str(n), "narration": "ignored", "shots": [{"start": n * 10, "end": n * 10 + 4}, {"start": n * 10 + 4, "end": n * 10 + 8}]}
            for n in range(4)
        ]}
        config = {**self.config, "story_mode": "serial", "episodes": 4, "max_duration": 10}
        with patch("clipy.film.keys.ask_json", side_effect=[{"summary": "sumber"}, {"summary": story}, response]) as ask:
            result = film.plan(self.words, 40, config)
        self.assertEqual(" ".join(ep["narration"] for ep in result), story)
        self.assertEqual([len(ep["narration"].split()) for ep in result], [12, 12, 12, 12])
        self.assertEqual(config["story_summary"], story)
        self.assertIn(result[0]["narration"], ask.call_args.args[1])
        self.assertNotIn("payoff that is shown within the episode", ask.call_args.args[1])

    def test_serial_replans_unbalanced_durations(self):
        def episode(start, duration):
            return {"narration": "ignored", "shots": [{"start": start, "end": start + 4},
                                                           {"start": start + 4, "end": start + duration}]}
        short = {"episodes": [episode(0, 8), episode(10, 5)]}
        balanced = {"episodes": [episode(0, 8), episode(10, 8)]}
        config = {**self.config, "story_mode": "serial", "episodes": 2, "max_duration": 10}
        story = " ".join(f"kata{i}" for i in range(20))
        with patch("clipy.film.keys.ask_json", side_effect=[{"summary": "sumber"}, {"summary": story}, short, balanced]) as ask:
            result = film.plan(self.words, 20, config)
        self.assertEqual(" ".join(ep["narration"] for ep in result), story)
        self.assertIn("at least 7.0s", ask.call_args.args[1])

    def test_serial_plan_reserves_encoding_headroom(self):
        response = {"episodes": [{"narration": "Cerita", "shots": [{"start": 0, "end": 5}, {"start": 5, "end": 10}]}]}
        with self.assertRaisesRegex(ValueError, "encoding headroom"):
            film._validate_plan(response, 20, {**self.config, "story_mode": "serial", "max_duration": 10})

    def test_story_split_prefers_nearby_sentence_without_losing_words(self):
        story = "satu dua tiga empat lima. enam tujuh delapan sembilan sepuluh sebelas dua belas"
        parts = film._split_story(story, 2)
        self.assertTrue(parts[0].endswith("lima."))
        self.assertEqual(" ".join(parts), story)

    def test_story_budget_tracks_voice_speed(self):
        story = " ".join("kata" for _ in range(36))
        with patch("clipy.film.keys.ask_json", return_value={"summary": story}) as ask:
            self.assertEqual(film._serial_story(["evidence"], {"episodes": 1}, 10, speed=2), story)
        self.assertIn("Aim for 36 spoken words", ask.call_args.args[1])


class FilmScriptTests(unittest.TestCase):
    def test_retries_over_budget_script_and_keeps_validated_shots(self):
        shots = [{"start": 1, "end": 5}, {"start": 7, "end": 11}]
        episodes = [{"narration": "Draf awal.", "shots": shots}]
        over_budget = {"scripts": [{"hook": "Paul terjebak.", "body": "kata " * 20, "ending": "Pintunya runtuh."}]}
        corrected = {"scripts": [{"hook": "Paul terjebak.", "body": "Ia berlari ke pintu.", "ending": "Pintunya runtuh."}]}
        with patch("clipy.film.keys.ask_json", side_effect=[over_budget, corrected]):
            result = film._write_scripts(episodes, [Word("Paul", 2, 3)], {})
        self.assertEqual(result[0]["narration"], "Paul terjebak. Ia berlari ke pintu. Pintunya runtuh.")
        self.assertEqual(result[0]["shots"], shots)
        self.assertEqual(episodes[0]["narration"], "Draf awal.")


if __name__ == "__main__":
    unittest.main()
