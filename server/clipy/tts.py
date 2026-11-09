"""
Intro dubbing: the clip title read aloud, using Microsoft Edge's online read-aloud voices
(edge-tts). Needs an internet connection; no key.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import edge_tts
from .captions import Word

# Only Gadis and Ardi are native Indonesian voices; the four "multilingual" ones read
# Indonesian titles in Indonesian too (checked by transcribing them back with Whisper).
VOICES = {
    "id-ID-GadisNeural": "Gadis · Indonesian · female",
    "id-ID-ArdiNeural": "Ardi · Indonesian · male",
    "en-US-AvaMultilingualNeural": "Ava · Indonesian · female",
    "en-US-AndrewMultilingualNeural": "Andrew · Indonesian · male",
    "en-US-EmmaMultilingualNeural": "Emma · Indonesian · female",
    "en-US-BrianMultilingualNeural": "Brian · Indonesian · male",
    "en-US-JennyNeural": "Jenny · English (US) · female",
    "en-US-GuyNeural": "Guy · English (US) · male",
    "en-GB-SoniaNeural": "Sonia · English (UK) · female",
    "en-GB-RyanNeural": "Ryan · English (UK) · male",
}


def speak(text: str, voice: str, out: Path, *, speed: float = 1.0) -> Path:
    if voice not in VOICES:
        raise ValueError(f"unknown voice {voice}")
    # The read-aloud service now and then answers with no audio; a retry usually works.
    for attempt in range(3):
        try:
            # Called from the worker thread, which has no event loop of its own.
            asyncio.run(edge_tts.Communicate(text, voice, rate=f"+{round((speed - 1) * 100)}%").save(str(out)))
            return out
        except edge_tts.exceptions.NoAudioReceived:
            if attempt == 2:
                raise
            time.sleep(1.5 * (attempt + 1))
    return out


def speak_aligned(text: str, voice: str, out: Path, *, speed: float = 1.0) -> tuple[Path, list[Word]]:
    """Generate audio and use the service's real word-boundary offsets (100ns ticks)."""
    if voice not in VOICES:
        raise ValueError(f"unknown voice {voice}")

    async def generate() -> list[Word]:
        words = []
        with out.open("wb") as f:
            async for chunk in edge_tts.Communicate(text, voice, rate=f"+{round((speed - 1) * 100)}%", boundary="WordBoundary").stream():
                if chunk["type"] == "audio":
                    f.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    start = chunk["offset"] / 10_000_000
                    end = (chunk["offset"] + chunk["duration"]) / 10_000_000
                    if end > start and chunk.get("text", "").strip():
                        words.append(Word(chunk["text"].strip(), start, end))
        if not out.stat().st_size or not words:
            raise ValueError("Voice service returned no audio or word timing; cannot align subtitles")
        if any(w.start < 0 or (i and w.start < words[i - 1].start) for i, w in enumerate(words)):
            raise ValueError("Voice service returned invalid word timing; cannot align subtitles")
        return words

    for attempt in range(3):
        try:
            return out, asyncio.run(generate())
        except edge_tts.exceptions.NoAudioReceived:
            out.unlink(missing_ok=True)
            if attempt == 2:
                raise
            time.sleep(1.5 * (attempt + 1))
        except Exception:
            out.unlink(missing_ok=True)
            raise
    raise RuntimeError("Voice generation failed")
