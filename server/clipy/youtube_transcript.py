"""Obtain YouTube caption tracks only when they provide usable word boundaries."""
from __future__ import annotations

import json
import html
import math
import re
import time
from urllib.parse import urlsplit

import yt_dlp
from .captions import Word

MAX_BYTES = 8 * 1024 * 1024


BRACKETED_CUE = re.compile(r"\[[^\[\]]*\]")


def _without_cues(text: str) -> str:
    return BRACKETED_CUE.sub(lambda match: " " * len(match[0]), text)


def _token_key(token: str) -> str:
    """Compare caption tokens without punctuation-only differences."""
    return re.sub(r"^\W+|\W+$", "", token).casefold()


def _rolling_overlap(previous: list[str], current: list[str]) -> int:
    """Return the longest rolling-caption tail/prefix overlap."""
    previous_keys = [_token_key(token) for token in previous]
    current_keys = [_token_key(token) for token in current]
    for size in range(min(len(previous_keys), len(current_keys)), 0, -1):
        if previous_keys[-size:] == current_keys[:size]:
            return size
    return 0


def _token_weight(token: str) -> int:
    return max(1, len(_token_key(token)))


def _merge_cues(cues: list[Word], duration: float) -> list[Word]:
    """Flatten rolling caption windows without turning a whole video into one cue.

    Auto-generated YouTube tracks commonly repeat the previous words in every
    overlapping window.  Concatenating those windows makes caption timing drift
    further behind the video with every event.  Keep only each new suffix and
    place it inside the part of the current window where it is spoken.
    """
    result = []
    previous_tokens: list[str] = []
    previous_end = -1.0
    for cue in sorted(cues, key=lambda cue: cue.start):
        start = min(max(0.0, cue.start), duration)
        end = min(max(start, cue.end), duration)
        if end <= start:
            end = duration
        if end <= start or not cue.text.strip():
            continue
        text = re.sub(r'\s+', ' ', _without_cues(cue.text)).strip()
        if not text:
            continue
        tokens = text.split()
        overlap = _rolling_overlap(previous_tokens, tokens) if start <= previous_end + 0.08 else 0
        fresh = tokens[overlap:]
        if not fresh:
            # A rolling window sometimes redraws exactly the same phrase. Keep
            # the existing phrase visible for its refreshed end time.
            if result and overlap:
                result[-1].end = max(result[-1].end, end)
            previous_tokens, previous_end = tokens, end
            continue
        if overlap:
            total = sum(_token_weight(token) for token in tokens)
            consumed = sum(_token_weight(token) for token in tokens[:overlap])
            start += (end - start) * consumed / total
            text = ' '.join(fresh)
        result.append(Word(text, start, end))
        previous_tokens, previous_end = tokens, end
    if not result:
        raise ValueError('subtitle tidak memiliki teks')
    return result


def parse_cues(data: bytes, duration: float) -> tuple[list[Word], str]:
    if not data or len(data) > MAX_BYTES:
        raise ValueError('subtitle kosong atau terlalu besar')
    text = data.decode('utf-8-sig').replace('\r', '')
    time_re = re.compile(r'(?P<start>(?:\d+:)?\d{1,2}:\d{2}[.,]\d{3})\s*-->\s*(?P<end>(?:\d+:)?\d{1,2}:\d{2}[.,]\d{3})')
    def seconds(value):
        parts = value.replace(',', '.').split(':')
        return sum(float(part) * 60 ** i for i, part in enumerate(reversed(parts)))
    cues = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        match = time_re.search(lines[i])
        i += 1
        if not match:
            continue
        content = []
        while i < len(lines) and lines[i].strip():
            content.append(lines[i]); i += 1
        phrase = html.unescape(re.sub(r'<[^>]*>|\{\\[^}]*\}', '', ' '.join(content))).strip()
        if phrase:
            cues.append(Word(phrase, seconds(match.group('start')), seconds(match.group('end'))))
    if not cues:
        # Preserve readable text even when no reliable timing is present.
        plain = ' '.join(line.strip() for line in lines if line.strip() and
                         not line.startswith(('WEBVTT', 'NOTE', 'STYLE', 'Kind:', 'Language:')) and
                         '-->' not in line and not line.strip().isdigit())
        plain = html.unescape(re.sub(r'<[^>]*>', '', plain)).strip()
        if not plain:
            raise ValueError('subtitle tidak dapat dibaca')
        cues = [Word(plain, 0, duration)]
    return _merge_cues(cues, duration), 'segmen'


def parse_json3(data: bytes, duration: float) -> tuple[list[Word], str]:
    if not data or len(data) > MAX_BYTES:
        raise ValueError('subtitle kosong atau terlalu besar')
    events = json.loads(data.decode('utf-8-sig')).get('events', [])
    starts = []
    cues = []
    word_timing = True
    for event in events:
        segs = event.get('segs') or []
        segment_texts = [str(seg.get('utf8', '')) for seg in segs]
        cleaned = _without_cues(''.join(segment_texts))
        nonempty = []
        offset = 0
        for seg, text in zip(segs, segment_texts):
            part = cleaned[offset:offset + len(text)]
            offset += len(text)
            if part.strip():
                nonempty.append({**seg, 'utf8': part})
        if not nonempty:
            continue
        try:
            start = float(event.get('tStartMs', 0)) / 1000
            length = float(event.get('dDurationMs', 0)) / 1000
        except (TypeError, ValueError):
            start, length, word_timing = 0, duration, False
        if not math.isfinite(start) or not math.isfinite(length) or start < 0 or length <= 0:
            word_timing = False
            start = start if math.isfinite(start) and start >= 0 else 0
            length = length if math.isfinite(length) and length > 0 else max(0, duration - start)
        cue_text = re.sub(r'\s+', ' ', ''.join(str(s.get('utf8', '')) for s in nonempty).strip())
        cues.append(Word(cue_text, start, min(start + length, duration)))
        for index, seg in enumerate(nonempty):
            text = re.sub(r'\s+', ' ', str(seg.get('utf8', '')).strip())
            cjk_chars = len(re.findall(r'[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]', text))
            if len(text.split()) != 1 or len(text) > 64 or cjk_chars > 1 or (index and 'tOffsetMs' not in seg):
                word_timing = False
                continue
            try:
                when = start + float(seg.get('tOffsetMs', 0)) / 1000
            except (TypeError, ValueError):
                word_timing = False
                continue
            if not math.isfinite(when) or when < start or when > start + length or when > duration + 0.5:
                word_timing = False
                continue
            starts.append((when, min(start + length, duration), text))
    if not word_timing:
        return _merge_cues(cues, duration), 'segmen'
    starts.sort(key=lambda item: item[0])
    if not starts:
        raise ValueError('subtitle tidak memiliki kata')
    words = []
    for i, (start, end, text) in enumerate(starts):
        if i + 1 < len(starts):
            end = min(end, starts[i + 1][0])
        if end <= start:
            return _merge_cues(cues, duration), 'segmen'
        words.append(Word(text, start, end))
    return words, 'kata'


def _languages(tracks: dict, language: str | None, automatic: bool) -> list[str]:
    # Prefer the original language, but try every available track before giving up.
    keys = list(tracks)
    base = (language or '').split('-')[0].lower()
    preferred = ([base + '-orig', base] if base else []) + [k for k in keys if k.endswith('-orig')]
    if automatic:
        preferred += [k for k in ('id', 'en') if k in tracks]
    preferred += keys
    return list(dict.fromkeys(k for k in preferred if k in tracks))


def fetch(info: dict) -> tuple[list[Word] | None, str | None, str | None, str, str | None]:
    """Return words, origin, language and a safe user-facing fallback reason."""
    duration = float(info.get('duration') or 0)
    if not math.isfinite(duration) or duration <= 0:
        return None, None, None, 'Durasi video tidak valid untuk memeriksa subtitle.', None
    attempted = False
    invalid = False
    failed = False
    deadline = time.monotonic() + 60
    opts = {'quiet': True, 'no_warnings': True, 'socket_timeout': 10, 'retries': 0}
    with yt_dlp.YoutubeDL(opts) as ydl:
        for key, origin, automatic in (('subtitles', 'youtube_manual', False),
                                       ('automatic_captions', 'youtube_auto', True)):
            tracks = info.get(key) or {}
            for language in _languages(tracks, info.get('language'), automatic):
                ordered = sorted(tracks.get(language, []), key=lambda t: {'json3': 0, 'srt': 1, 'vtt': 2}.get(t.get('ext'), 3))
                for track in ordered:
                    if time.monotonic() >= deadline:
                        return None, None, None, 'Pemeriksaan subtitle YouTube melewati batas waktu.', None
                    if track.get('ext') not in ('json3', 'srt', 'vtt') or not track.get('url'):
                        continue
                    parsed = urlsplit(track['url'])
                    host = (parsed.hostname or '').lower()
                    if parsed.scheme != 'https' or not (host == 'youtube.com' or host.endswith('.youtube.com')):
                        failed = True
                        continue
                    attempted = True
                    try:
                        with ydl.urlopen(track['url']) as response:
                            data = response.read(MAX_BYTES + 1)
                        words, precision = (parse_json3(data, duration) if track.get('ext') == 'json3'
                                            else parse_cues(data, duration))
                        return words, origin, language.removesuffix('-orig'), '', precision
                    except (ValueError, TypeError, KeyError, UnicodeError):
                        invalid = True
                    except Exception:
                        failed = True
    reason = ('Subtitle YouTube tidak dapat dibaca.' if invalid else
              'Subtitle YouTube gagal diambil.' if failed or attempted else
              'Subtitle YouTube tidak tersedia.')
    return None, None, None, reason, None
