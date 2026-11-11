"""Preserve timed laughter cues and detect laughter in the audio used by a clip."""
from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

import numpy as np

from .captions import Word

LOG = logging.getLogger(__name__)
MODEL = Path(__file__).resolve().parent.parent / "models" / "yamnet.tflite"
SAMPLE_RATE = 16_000
# The MediaPipe YAMNet model classifies 975 ms windows.
WINDOW = .975
BRACKETED_CUE = re.compile(r"\[[^\[\]]*\]")
TYPED_LAUGHTER = re.compile(r"(?:ha){2,}|(?:he){2,}|(?:hi){2,}|(?:wk){2,}|(?:ja){2,}", re.IGNORECASE)
LAUGHTER_LABEL = re.compile(r"\b(?:laugh\w*|tertawa|tawa|giggl\w*|chuckl\w*|snicker\w*|cekikikan)\b", re.IGNORECASE)
QUIET_LABEL = re.compile(r"\b(?:giggl\w*|chuckl\w*|snicker\w*|cekikikan)\b", re.IGNORECASE)
LAUGHTER_CLASSES = {
    "Laughter": "ha",
    "Baby laughter": "ha",
    "Belly laugh": "ha",
    "Giggle": "he",
    "Snicker": "he",
    "Chuckle, chortle": "he",
}


def _spelled_laughter(text: str) -> str:
    return re.sub(r"[\s.,!?:;\-]+", "", text).casefold()


def is_laughter_cue(text: str) -> bool:
    if not BRACKETED_CUE.fullmatch(text):
        return False
    label = text[1:-1]
    return bool(LAUGHTER_LABEL.search(label) or TYPED_LAUGHTER.fullmatch(_spelled_laughter(label)))


def laugh_text(duration: float, syllable: str = "ha") -> str:
    return syllable * max(2, min(7, round(duration / .32)))


def normalize_cues(text: str, duration: float) -> str:
    """Turn timed laughter annotations into caption text and strip other cues."""
    laughter_cues = [match for match in BRACKETED_CUE.finditer(text) if is_laughter_cue(match[0])]
    spoken = BRACKETED_CUE.sub(" ", text).split()
    seconds = duration / max(1, len(spoken) + len(laughter_cues))

    def replace(match: re.Match) -> str:
        if not is_laughter_cue(match[0]):
            return " "
        label = match[0][1:-1]
        spelled = _spelled_laughter(label)
        if TYPED_LAUGHTER.fullmatch(spelled):
            return f" {spelled} "
        syllable = "he" if QUIET_LABEL.search(label) else "ha"
        return f" {laugh_text(seconds, syllable)} "

    return " ".join(BRACKETED_CUE.sub(replace, text).split())


def _classify(samples: np.ndarray):
    from mediapipe.tasks.python import BaseOptions, audio
    from mediapipe.tasks.python.components.containers import AudioData

    options = audio.AudioClassifierOptions(
        base_options=BaseOptions(model_asset_path=str(MODEL)),
        max_results=8,
    )
    with audio.AudioClassifier.create_from_options(options) as classifier:
        # Offset the second pass by half a window to narrow the detected time span.
        for offset in (0, round(WINDOW * SAMPLE_RATE / 2)):
            if len(samples) - offset < SAMPLE_RATE * .15:
                continue
            data = AudioData.create_from_array(samples[offset:], sample_rate=SAMPLE_RATE)
            for result in classifier.classify(data):
                if result.timestamp_ms is not None and result.classifications:
                    start = offset / SAMPLE_RATE + result.timestamp_ms / 1000
                    yield start, result.classifications[0].categories


def detect(source: Path, duration: float) -> list[Word]:
    if not MODEL.is_file() or duration <= 0:
        return []
    pcm = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(source), "-t", f"{duration:.3f}",
         "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "-"],
        capture_output=True,
    ).stdout
    samples = np.frombuffer(pcm, dtype="<f4").copy()
    duration = min(duration, len(samples) / SAMPLE_RATE)
    if duration < .15:
        return []
    hits = []
    for start, categories in _classify(samples):
        matches = [category for category in categories if category.category_name in LAUGHTER_CLASSES]
        if not matches or start >= duration:
            continue
        laugh = matches[0]
        top = categories[0].category_name
        threshold = .18 if top in LAUGHTER_CLASSES else .23
        # Speech can mask a chuckle. Require support from two laughter classes for a weaker match.
        if top == "Speech" and len(matches) > 1 and matches[1].score >= .08:
            threshold = .14
        if laugh.score < threshold:
            continue
        center = (start + min(duration, start + WINDOW)) / 2
        left = 0.0 if start == 0 else max(0.0, center - .30)
        right = min(duration, center + .30)
        hits.append((left, right, laugh.score, LAUGHTER_CLASSES[laugh.category_name]))

    spans: list[tuple[float, float, float, str]] = []
    for start, end, score, syllable in sorted(hits):
        if spans and start <= spans[-1][1] + .12:
            left, right, prior_score, prior_syllable = spans[-1]
            strongest = syllable if score > prior_score else prior_syllable
            spans[-1] = (left, max(end, right), max(prior_score, score), strongest)
        else:
            spans.append((start, end, score, syllable))
    return [Word(laugh_text(end - start, syllable), start, end) for start, end, _, syllable in spans]


def caption_words(source: Path, words: list[Word], duration: float) -> list[Word]:
    normalized = []
    for word in words:
        text = normalize_cues(word.text, word.end - word.start)
        if text:
            normalized.append(Word(text, word.start, word.end))
    transcribed = [word for word in normalized if TYPED_LAUGHTER.fullmatch(_spelled_laughter(word.text))]
    try:
        detected = detect(source, duration)
    except (OSError, RuntimeError, ValueError, ImportError, subprocess.SubprocessError):
        LOG.warning("Laughter detection skipped for %s", source.name, exc_info=True)
        return normalized
    for laugh in detected:
        if any(laugh.start < word.end + .2 and laugh.end > word.start - .2 for word in transcribed):
            continue
        normalized.append(laugh)
    return sorted(normalized, key=lambda word: (word.start, word.end))
