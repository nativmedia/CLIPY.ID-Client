import unittest
from clipy.captions import Word
from clipy.select import _complete, _snap

class SentenceBoundaryTests(unittest.TestCase):
    def test_finish_sentence_even_when_more_than_six_seconds_remain(self):
        words = [Word('awalan', 0, 0.5), Word('masih', 48, 49), Word('selesai.', 56, 57)]
        self.assertEqual(_complete(words, 0, 48, 60, []), 57.25)

    def test_never_exceed_requested_maximum(self):
        words = [Word('awalan', 0, 0.2), Word('akhir.', 60.4, 60.5)]
        start, end = _snap(words, 0, 60.5, 60)
        self.assertLessEqual(end - start, 60)
