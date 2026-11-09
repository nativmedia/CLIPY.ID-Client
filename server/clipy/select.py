"""
Picking the clips: the full transcript goes to the LLM behind the stored API key, which
returns the strongest self-contained moments. Boundaries are then snapped to words and,
where the model cut a conversation short, the clip is carried on to where the exchange
actually winds down (a sentence end followed by a pause), within the chosen maximum.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import keys
from .captions import Word

SCHEMA = {
    "type": "object",
    "properties": {
        "clips": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start": {"type": "number", "description": "seconds from the start of the video"},
                    "end": {"type": "number", "description": "seconds from the start of the video"},
                    "title": {"type": "string", "description": "short catchy title, in the video's language"},
                    "reason": {"type": "string", "description": "one sentence: why this moment works as a short"},
                },
                "required": ["start", "end", "title", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["clips"],
    "additionalProperties": False,
}

SYSTEM = (
    "You are a short-form video editor. From a timestamped transcript of a long video you pick "
    "the moments that work best as standalone vertical shorts: a strong hook in the first seconds, "
    "a complete thought or story, a payoff (insight, punchline, surprise, strong opinion). "
    "Each clip must make sense without the rest of the video and must carry its context to the end: "
    "include the setup the payoff depends on, and do not stop until the exchange is resolved: the "
    "question is answered, the story reaches its punchline, and the reaction to it has played out. "
    "Never end in the middle of a back-and-forth or right after a question. Use the time you are "
    "given: a clip far shorter than the target is almost always cut off too early, so extend it to "
    "the natural end of that conversation. Start on the first word of a sentence and end after a "
    "sentence finishes. Clips must not overlap. Order them best first. "
    "Titles are at most 60 characters, in the same language as the video, written to make people "
    "stop scrolling, without clickbait that the clip doesn't deliver."
)

# A pause at least this long (seconds) after a sentence reads as the conversation winding down.
WIND_DOWN_GAP = 0.6


@dataclass
class Pick:
    start: float
    end: float
    title: str
    reason: str


def transcript_lines(words: list[Word]) -> str:
    """Compact transcript: one line per sentence-ish run of words, prefixed with [start-end]."""
    lines: list[str] = []
    run: list[Word] = []

    def flush():
        if run:
            lines.append(f"[{run[0].start:.1f}-{run[-1].end:.1f}] " + " ".join(w.text for w in run))
            run.clear()

    for w in words:
        if run and (w.start - run[-1].end > 0.8 or len(run) >= 30):
            flush()
        run.append(w)
        if w.text.endswith((".", "?", "!")):
            flush()
    flush()
    return "\n".join(lines)


def durations(max_duration: float) -> tuple[float, float]:
    """(shortest acceptable, target) clip length for a chosen maximum."""
    return max(8.0, max_duration * 0.6), max_duration * 0.85


MOODS = {
    "auto": "Choose the strongest moments naturally, without a specific mood preference.",
    "senang": "Prioritize joyful, happy, celebratory or uplifting moments.",
    "sedih": "Prioritize sad, emotional, touching or reflective moments.",
    "lucu": "Prioritize humorous exchanges, jokes and complete comedic setups and payoffs.",
    "serius": "Prioritize serious, thoughtful discussions and substantial arguments.",
    "seram": "Prioritize eerie, suspenseful or frightening stories and their context.",
    "informatif": "Prioritize useful explanations, concrete facts and educational takeaways.",
    "inspiratif": "Prioritize inspiring stories, motivation and lessons from overcoming challenges.",
    "konyol": "Prioritize silly, goofy, absurd or playfully ridiculous exchanges with their complete context and payoff.",
}


def pick_clips(words: list[Word], count: int, max_duration: float, language: str | None,
               exclude: list[tuple[float, float]] | None = None, mood: str = "auto",
               allow_approximate: bool = False, on_approximate=None) -> list[Pick]:
    """
    `exclude` holds stretches already clipped in earlier projects of this video: the new clips
    must be different moments, so they may not overlap any of them.
    """
    if not words:
        raise ValueError("no speech found in this video")
    if mood not in MOODS:
        raise ValueError("unknown mood")
    total = words[-1].end
    shortest, target = durations(max_duration)
    used = [Pick(s, e, "", "") for s, e in exclude or []]
    taken_note = ""
    if used:
        spans = ", ".join(f"{p.start:.0f}-{p.end:.0f}" for p in used)
        taken_note = (
            f"This video was clipped before. These stretches (seconds) are already used, so every "
            f"new clip must be a different moment that does not overlap any of them: {spans}.\n"
        )
    user = (
        f"Video length: {total:.0f} seconds. Language: {language or 'unknown'}.\n"
        f"Mood preference: {mood.upper()}. {MOODS[mood]} "
        "Use only evidence in the transcript; do not invent emotions, events or misleading titles.\n"
        f"{taken_note}"
        f"Pick up to {count} clips. Each clip should run about {target:.0f} seconds: at least "
        f"{shortest:.0f} and never more than {max_duration:.0f}. Choose the end time where that "
        f"conversation is really finished, not where the first funny line lands.\n\n"
        f"Transcript:\n{transcript_lines(words)}"
    )
    data = keys.ask_json(SYSTEM, user, SCHEMA)

    picks: list[Pick] = []
    for c in data.get("clips", []):
        try:
            start, end = _snap(words, float(c["start"]), float(c["end"]), max_duration)
        except ValueError:
            if not allow_approximate:
                continue
            start = max(0.0, min(float(c['start']), total))
            end = min(total, max(start, float(c['end'])), start + max_duration)
            if on_approximate:
                on_approximate()
        end = _complete(words, start, end, max_duration, picks + used)
        if end - start < 3:
            continue
        if any(start < p.end and end > p.start for p in picks + used):
            continue
        picks.append(Pick(start, end, str(c["title"]).strip()[:80], str(c.get("reason", "")).strip()))
        if len(picks) == count:
            break
    if not picks:
        raise ValueError(
            "no new moments left that differ from earlier clips" if used else "the model didn't return any usable clips"
        )
    return picks


def _snap(words: list[Word], start: float, end: float, max_duration: float) -> tuple[float, float]:
    """Start on the first word at/after `start` (with a little slack), end on the last word before `end`."""
    first = next((i for i, w in enumerate(words) if w.start >= start - 0.6), len(words) - 1)
    while first and words[first - 1].end > words[first].start:
        first -= 1
    s = max(0.0, words[first].start - 0.15, words[first - 1].end if first else 0.0)
    def boundary(i):
        next_start = words[i + 1].start if i + 1 < len(words) else float('inf')
        return min(words[i].end + 0.25, next_start) if words[i].end <= next_start else None
    candidates = [(i, boundary(i)) for i in range(first, len(words)) if words[i].end <= end + 0.6]
    candidates = [(i, t) for i, t in candidates if t is not None]
    if not candidates:
        first_end = boundary(first)
        if first_end is None or first_end - s > max_duration:
            raise ValueError('Tidak ditemukan batas kata yang aman untuk clip')
        candidates = [(first, first_end)]
    _, e = candidates[-1]
    if e - s > max_duration:
        fitting = [t for _, t in candidates if t - s <= max_duration]
        if not fitting:
            raise ValueError('Durasi clip terlalu pendek untuk batas kata yang aman')
        e = fitting[-1]
    return round(s, 3), round(e, 3)


def _complete(words: list[Word], start: float, end: float, max_duration: float, taken: list[Pick]) -> float:
    """
    Carry a clip on to where its conversation winds down. Ends are only ever moved later, to a
    word that finishes a sentence or is followed by a pause, and never past the chosen maximum
    or into another clip. A clip already long enough just gets its last sentence finished.
    """
    shortest, _ = durations(max_duration)
    # The latest the clip may end: the maximum, or just before the next clip that follows it.
    limit = start + max_duration
    for p in taken:
        if p.start >= end:
            limit = min(limit, p.start - 0.3)

    candidates: list[tuple[float, float]] = []  # (end time, pause after that word)
    for i, w in enumerate(words):
        following = words[i + 1].start if i + 1 < len(words) else float('inf')
        if w.end > following:
            continue
        boundary = min(w.end + 0.25, following)
        if boundary <= end - 0.05 or boundary > limit:
            continue
        gap = words[i + 1].start - w.end if i + 1 < len(words) else 5.0
        sentence_end = w.text.rstrip().endswith((".", "?", "!")) and not w.text.rstrip().endswith("?")
        if sentence_end or gap >= WIND_DOWN_GAP:
            candidates.append((boundary, gap + (0.4 if sentence_end else 0.0)))
    if not candidates:
        return end

    if end - start >= shortest:
        # Long enough: just make sure it stops at the end of a sentence, not mid-thought.
        sentence_ends = [
            min(w.end + 0.25, words[i + 1].start if i + 1 < len(words) else float("inf"))
            for i, w in enumerate(words)
            if w.end <= (words[i + 1].start if i + 1 < len(words) else float("inf"))
            and end - 0.05 <= min(w.end + 0.25, words[i + 1].start if i + 1 < len(words) else float("inf")) <= limit
            and w.text.rstrip().endswith((".", "?", "!"))
        ]
        nearest = sentence_ends[0] if sentence_ends else candidates[0][0]
        return round(nearest, 3)

    # Too short: among the ends that reach the shortest length, take the clearest wind-down
    # (longest pause); if none reach it, the clearest one available.
    long_enough = [c for c in candidates if c[0] - start >= shortest]
    pool = long_enough or candidates
    best = max(pool, key=lambda c: (round(c[1], 1), c[0]))
    return round(best[0], 3)
