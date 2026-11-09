"""Read SubRip cues into approximate per-word timestamps."""
from __future__ import annotations

import html
import re

from .captions import Word

MAX_BYTES = 5 * 1024 * 1024
_TIME = r"(\d{1,3}):(\d{2}):(\d{2})[,.](\d{3})"
_RANGE = re.compile(rf"^\s*{_TIME}\s*-->\s*{_TIME}(?:\s+.*)?\s*$")


def _seconds(parts: tuple[str, ...]) -> float:
    hours, minutes, seconds, millis = map(int, parts)
    if minutes > 59 or seconds > 59:
        raise ValueError("Waktu pada subtitle tidak valid")
    return hours * 3600 + minutes * 60 + seconds + millis / 1000


def parse_srt(raw: bytes) -> list[Word]:
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError("File SRT kosong atau melebihi 5 MB")
    try:
        text = raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError as exc:
        raise ValueError("File SRT harus menggunakan encoding UTF-8") from exc
    if "\x00" in text:
        raise ValueError("File SRT tidak valid")
    words: list[Word] = []
    previous_start = -1.0
    for cue in re.split(r"\n\s*\n", text.strip()):
        lines = cue.strip().splitlines()
        if lines and re.fullmatch(r"\d+", lines[0].strip()):
            lines = lines[1:]
        match = _RANGE.fullmatch(lines[0]) if lines else None
        if not match or len(lines) < 2:
            raise ValueError("Format SRT tidak valid: periksa nomor, waktu dan teks subtitle")
        start, end = _seconds(match.groups()[:4]), _seconds(match.groups()[4:])
        if end <= start or start < previous_start:
            raise ValueError("Waktu subtitle harus berurutan dan durasinya lebih dari nol")
        previous_start = start
        subtitle = html.unescape(re.sub(r"<[^>]*>|\{\\[^}]*\}", "", " ".join(lines[1:])))
        tokens = subtitle.split()
        if not tokens:
            continue
        weights = [max(1, len(token)) for token in tokens]
        total = sum(weights)
        elapsed = 0
        for token, weight in zip(tokens, weights):
            word_start = start + (end - start) * elapsed / total
            elapsed += weight
            word_end = start + (end - start) * elapsed / total
            words.append(Word(token, word_start, word_end))
    if not words:
        raise ValueError("File SRT tidak memiliki teks subtitle")
    return sorted(words, key=lambda word: word.start)
