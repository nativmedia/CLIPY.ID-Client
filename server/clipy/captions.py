"""Timed captions with fixed word anchors, rendered by FFmpeg/libass."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from fontTools.ttLib import TTFont
from PIL import ImageFont


@dataclass
class Word:
    text: str
    start: float
    end: float


@dataclass
class Chunk:
    words: list[Word]
    start: float
    end: float


DEFAULT_COLOR = "white"
FONT_WEIGHTS = {"thin": 100, "regular": 400, "bold": 700, "extra-bold": 800}
# The weight control is all-or-nothing: these faces provide every option the
# UI exposes (100, 400, 700, and 800).  Other families stay at their native
# regular face rather than relying on fake browser/renderer bolding.
FONT_WEIGHT_FONTS = frozenset({"jetbrains-mono", "montserrat", "poppins"})
# Bangers has a few alternate capital glyphs in its lowercase slots.
UPPERCASE_ONLY_FONTS = frozenset({"bangers", "bebas"})
VARIABLE_WEIGHT_FONTS = frozenset({"jetbrains-mono", "montserrat"})


def font_weight_value(font: str, weight: str) -> int:
    """Translate a supported UI weight to ASS/CSS, otherwise use regular."""
    if font not in FONT_WEIGHT_FONTS:
        return FONT_WEIGHTS["regular"]
    return FONT_WEIGHTS.get(weight, FONT_WEIGHTS["regular"])


def font_file(font: str, weight: str = "regular") -> str:
    """Return the source file that contains this font's real requested weight."""
    resolved = font_weight_value(font, weight)
    if font == "poppins":
        return {
            100: "Poppins-Thin.ttf",
            400: "Poppins-Regular.ttf",
            700: "Poppins-Bold.ttf",
            800: "Poppins-ExtraBold.ttf",
        }[resolved]
    return FONT_FILES[font]


@dataclass
class CaptionStyle:
    font: str = "montserrat"
    font_weight: str = "regular"
    animation: str = "pop"
    color: str = DEFAULT_COLOR
    case: str = "upper"
    position: int | None = None
    display: str | None = None
    # Kept only so re-clipping an older saved project does not break.
    # New settings use font_weight instead.
    text_style: str = "plain"
    max_lines: int = 2


# Family names as libass sees them (the TTFs live in server/fonts).  Keep this
# display order alphabetical as the API order is also the fallback picker order.
FONTS = {
    "anton": ("Anton", "Anton"),
    "bangers": ("Bangers", "Bangers"),
    "bebas": ("Bebas Neue", "Bebas Neue"),
    "gaegu": ("Gaegu", "Gaegu"),
    "instrument-serif": ("Instrument Serif", "Instrument Serif"),
    "jetbrains-mono": ("JetBrains Mono", "JetBrains Mono"),
    "just-me": ("Just Me Again Down Here", "Just Me Again Down Here"),
    "montserrat": ("Montserrat", "Montserrat"),
    "poppins": ("Poppins", "Poppins"),
}
ANIMATIONS = {
    "highlight": "Word highlight",
    "box": "Word box",
    "karaoke": "Karaoke sweep",
    "reveal": "Word reveal",
    "underline": "Word underline",
    "glow": "Word glow",
    "pop": "Pop",
    "fade": "Fade",
    "word-shadow": "Shadow",
    "word-3d": "3D",
    "word-outline": "Outline",
    "word-bounce": "Bounce",
    "word-slide": "Slide",
    "word-punch": "Punch",
    "word-neon": "Neon",
    "word-box": "Box",
}
FONT_FILES = {
    "montserrat": "Montserrat[wght].ttf",
    "anton": "Anton-Regular.ttf",
    "bebas": "BebasNeue-Regular.ttf",
    "poppins": "Poppins-Regular.ttf",
    "bangers": "Bangers-Regular.ttf",
    "just-me": "JustMeAgainDownHere.ttf",
    "instrument-serif": "InstrumentSerif-Regular.ttf",
    "jetbrains-mono": "JetBrainsMono[wght].ttf",
    "gaegu": "Gaegu-Regular.ttf",
}
FONT_DIR = Path(__file__).resolve().parent.parent / "fonts"

# libass sizes fonts against each font's Windows line box.  A few otherwise
# legible faces have a much smaller visible cap-height in that box, notably
# Gaegu.  Only lift undersized faces; do not shrink a font the user selected.
CAPTION_SIZE_SCALES = {
    "bebas": 1.20,
    "gaegu": 1.60,
    "instrument-serif": 1.15,
    "jetbrains-mono": 1.14,
}


@lru_cache(maxsize=96)
def _font(font: str, size: int, weight: str = "regular"):
    face = ImageFont.truetype(str(FONT_DIR / font_file(font, weight)), size)
    if font in VARIABLE_WEIGHT_FONTS:
        # Both bundled variable files have one Weight axis, so Pillow measures
        # the exact same glyph weight that is sent to the renderer.
        face.set_variation_by_axes([font_weight_value(font, weight)])
    return face


@lru_cache(maxsize=96)
def _ass_font_scale(font: str, weight: str = "regular") -> float:
    # ASS sizes refer to the Windows line box; Pillow measures an em at the requested size.
    with TTFont(FONT_DIR / font_file(font, weight), lazy=True) as face:
        metrics = face["OS/2"]
        return (metrics.usWinAscent + metrics.usWinDescent) / face["head"].unitsPerEm
COLORS = {
    DEFAULT_COLOR: "#FFFFFF",
    "yellow": "#FFE600",
    "green": "#3CFF6E",
    "cyan": "#33E1FF",
    "pink": "#FF4FD8",
    "red": "#FF3B30",
}
CASES = {"upper": "UPPERCASE", "lower": "lowercase", "title": "Title Case", "sentence": "Sentence case"}

OPTIONS = {
    "fonts": dict(sorted(((k, v[0]) for k, v in FONTS.items()), key=lambda item: item[1].casefold())),
    "font_weight_fonts": sorted(FONT_WEIGHT_FONTS),
    "uppercase_only_fonts": sorted(UPPERCASE_ONLY_FONTS),
    "animations": ANIMATIONS,
    "colors": COLORS,
    "cases": CASES,
}

# A pause this long (seconds) always starts a new chunk.
BREAK_GAP = 0.45
# How long a chunk may linger after its last word when nothing follows straight away.
LINGER = 0.3
MIN_CHUNK = 0.35


@dataclass
class Layout:
    width: int
    height: int
    font_size: int
    baseline: int
    max_words: int
    max_chars: int


def layout_for(width: int, height: int) -> Layout:
    """Caption size and position for an output frame; landscape gets smaller, longer lines."""
    if width > height:
        return Layout(width, height, round(height * 0.075), round(height * 0.88), 5, 28)
    baseline = 0.724 if height / width > 1.5 else 0.78
    return Layout(width, height, round(width * 0.096), round(height * baseline), 3, 15)


def chunk_words(words: list[Word], max_words: int = 3, max_chars: int = 15, *, sentence: bool = False) -> list[Chunk]:
    """Group words into short chunks, splitting on pauses and punctuation."""
    chunks: list[Chunk] = []
    current: list[Word] = []
    for w in words:
        if current:
            prev = current[-1]
            chars = sum(len(x.text) + 1 for x in current) + len(w.text)
            if (
                len(current) >= max_words
                or chars > max_chars
                or w.start - prev.end > BREAK_GAP
                or re.search(r"[.!?]$" if sentence else r"[.!?;:,]$", prev.text)
            ):
                chunks.append(Chunk(current, current[0].start, current[-1].end))
                current = []
        current.append(w)
    if current:
        chunks.append(Chunk(current, current[0].start, current[-1].end))

    # Let each chunk hang on a moment, but never into the next one.
    for i, c in enumerate(chunks):
        nxt = chunks[i + 1].start if i + 1 < len(chunks) else c.end + LINGER
        c.end = max(c.start + MIN_CHUNK, min(c.end + LINGER, nxt))
        if i + 1 < len(chunks):
            c.end = min(c.end, nxt)
    return chunks


_VOWEL_GROUPS = re.compile(r"[aeiouy]+", re.IGNORECASE)
COARSE_WORD_LEAD = 0.16


def _spoken_weight(token: str) -> float:
    """Estimate spoken duration from syllables, not raw character count."""
    letters = re.sub(r"[^A-Za-zÀ-ÖØ-öø-ÿ]", "", token)
    syllables = len(_VOWEL_GROUPS.findall(letters))
    # Acronyms/numbers still take time to say.  A tiny length component keeps
    # unusually long words from being rushed without delaying every later cue.
    return max(1.0, min(4.5, syllables + max(0, len(letters) - 4) * 0.06))


def expand_coarse_words(words: list[Word]) -> list[Word]:
    """Split cue-level subtitle timing into readable, approximately timed words.

    YouTube's fallback captions sometimes provide a whole spoken passage as one
    ``Word``.  Caption rendering, clipping, and framing all need word-sized
    entries, so distribute that cue's duration by spoken-syllable weight. A
    short, per-word anticipation makes the visual highlight feel aligned with
    speech without applying one global offset. Real word-timed transcripts
    contain one token per entry and pass through unchanged.
    """
    expanded: list[Word] = []
    for word in words:
        tokens = word.text.split()
        if len(tokens) < 2 or word.end <= word.start:
            expanded.append(word)
            continue
        weights = [_spoken_weight(token) for token in tokens]
        total = sum(weights)
        elapsed = 0
        previous_start = word.start
        for index, (token, weight) in enumerate(zip(tokens, weights)):
            raw_start = word.start + (word.end - word.start) * elapsed / total
            elapsed += weight
            end = word.end if index == len(tokens) - 1 else word.start + (word.end - word.start) * elapsed / total
            # Segment tracks do not expose the true onset of each token. Lead
            # only later words by a fraction of their own estimated duration,
            # so the active word does not visibly trail the speaker.
            lead = 0 if index == 0 else min(COARSE_WORD_LEAD, (end - raw_start) * 0.4)
            start = max(word.start, previous_start, raw_start - lead)
            expanded.append(Word(token, start, end))
            previous_start = start
    # Segment captions can overlap while they are refreshed by YouTube.  Sort
    # the synthesized word starts before a renderer turns them into cues.
    return sorted(expanded, key=lambda entry: (entry.start, entry.end, entry.text.casefold()))


def apply_case(text: str, case: str, first_in_sentence: bool) -> str:
    if case == "upper":
        return text.upper()
    if case == "lower":
        return text.lower()
    if case == "title":
        return text[:1].upper() + text[1:]
    # Sentence case: as transcribed, with the first word of a sentence capitalised.
    return text[:1].upper() + text[1:] if first_in_sentence else text


def display_words(words: list[Word], case: str) -> list[str]:
    out = []
    sentence_start = True
    for w in words:
        # Trending caption style: drop trailing commas/periods, keep ?! for tone.
        text = re.sub(r"[.,;:]+$", "", w.text.strip())
        text = text.replace("\\", "").replace("{", "(").replace("}", ")")
        out.append(apply_case(text, case, sentence_start))
        sentence_start = w.text.rstrip().endswith((".", "?", "!"))
    return out


def ass_color(hex_rgb: str, alpha: int = 0) -> str:
    """Colour as written in a Style line: &HAABBGGRR."""
    r, g, b = hex_rgb[1:3], hex_rgb[3:5], hex_rgb[5:7]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def tag_color(hex_rgb: str) -> str:
    """Colour as written inside an override tag: &HBBGGRR&."""
    return f"&H{hex_rgb[5:7]}{hex_rgb[3:5]}{hex_rgb[1:3]}&".upper()


def ass_time(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"



@dataclass
class CaptionCue:
    rows: list[list[tuple[Word, str, float]]]
    size: int
    line_height: int
    space: float
    start: float
    end: float


def _caption_cues(words: list[Word], style: CaptionStyle, width: int, height: int) -> list[CaptionCue]:
    """Wrap once using the actual font, then keep those same lines for every highlight."""
    words = expand_coarse_words(words)
    layout = layout_for(width, height)
    single = style.display == "word" or (style.display is None and style.animation == "word")
    sentence = style.display == "sentence"
    font_name = style.font if style.font in FONTS else "montserrat"
    weight = style.font_weight if font_name in FONT_WEIGHT_FONTS else "regular"
    mode_scale = 1.12 if single else 0.63 if sentence else 1
    size = max(1, round(layout.font_size * mode_scale * CAPTION_SIZE_SCALES.get(font_name, 1)))
    # Caption anchors are emitted at `size * ass_scale` below.  Measure at the
    # same visual size so proportional fonts cannot collide horizontally.
    ass_scale = _ass_font_scale(font_name, weight)
    measure_size = max(1, round(size * ass_scale))
    font = _font(font_name, measure_size, weight)
    limit = width * 0.86
    max_lines = style.max_lines if sentence else 1
    if single:
        chunks = [Chunk([word], word.start, word.end + LINGER) for word in words]
    elif sentence:
        chunks = chunk_words(words, 60, 360, sentence=True)
    else:
        chunks = chunk_words(words, layout.max_words, layout.max_chars)
    labels = dict(zip(map(id, words), display_words(words, style.case)))
    cues = []

    for chunk in chunks:
        rows = [[]]
        row_width = 0.0
        space = font.getlength(" ")
        def append_cue():
            if not rows[0]:
                return
            first, last = rows[0][0][0], rows[-1][-1][0]
            cues.append(CaptionCue(rows, size, round(measure_size * 1.32), space, first.start, last.end + LINGER))

        for word in chunk.words:
            label = labels[id(word)]
            if not label:
                continue
            advance = font.getlength(label)
            if advance > limit:
                append_cue()
                fitted = max(1, int(size * limit / advance))
                advance = _font(font_name, max(1, round(fitted * ass_scale)), weight).getlength(label)
                cues.append(CaptionCue([[(word, label, advance)]], fitted, round(fitted * ass_scale * 1.32),
                                       0, word.start, word.end + LINGER))
                rows, row_width = [[]], 0.0
                continue
            if rows[-1] and row_width + space + advance > limit:
                if len(rows) == max_lines:
                    append_cue()
                    rows = [[]]
                else:
                    rows.append([])
                row_width = 0.0
            row_width += advance + (space if rows[-1] else 0)
            rows[-1].append((word, label, advance))
        append_cue()

    # A rolling subtitle source can produce a later phrase whose estimated
    # word onset comes before an earlier phrase ends.  ASS draws both dialogue
    # events, making the text stack.  Serialize the actual visual cues and
    # drop zero-length fragments rather than rendering unreadable collisions.
    cues.sort(key=lambda cue: (cue.start, cue.end))
    for cue, following in zip(cues, cues[1:]):
        cue.end = min(cue.end, following.start)
    return [cue for cue in cues if cue.end - cue.start >= .01]


def build_ass(words: list[Word], style: CaptionStyle, width: int = 1080, height: int = 1920) -> str:
    font_name = style.font if style.font in FONTS else "montserrat"
    family = FONTS[font_name][1]
    font_weight = font_weight_value(font_name, style.font_weight)
    font_scale = _ass_font_scale(font_name, style.font_weight)
    hi = tag_color(COLORS.get(style.color, COLORS[DEFAULT_COLOR]))
    white = tag_color("#FFFFFF")
    black = tag_color("#000000")
    animation = "pop" if style.animation == "word" else style.animation
    baseline = layout_for(width, height).baseline
    if style.position is None:
        center = baseline
        stack_shift = round(height * 0.88) - baseline
    else:
        center = round(height * (0.17 + (style.position - 1) * 0.11))
        stack_shift = 0
    header = f"""[Script Info]
; CLIPY stack shift: {stack_shift}
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{family},40,&H00FFFFFF,&H00FFFFFF,&H00000000,&H60000000,{font_weight},0,0,0,100,100,0,0,1,0,0,5,0,0,0,1
Style: Shape,{family},40,&H00FFFFFF,&H00FFFFFF,&H00000000,&H60000000,{font_weight},0,0,0,100,100,0,0,1,0,0,7,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []

    def emit(start, end, tags, content, *, name="Cap", layer=1):
        if ass_time(end) != ass_time(start) and end > start:
            events.append(f"Dialogue: {layer},{ass_time(start)},{ass_time(end)},{name},,0,0,0,,{{{tags}}}{content}")

    for cue in _caption_cues(words, style, width, height):
        size = cue.size
        # Put each word at a fixed anchor. Only paint changes as the voice advances.
        outline = 0
        shadow = 0
        block_half = cue.line_height * len(cue.rows) / 2
        y = max(block_half, min(height - block_half, center)) - block_half + cue.line_height / 2
        spoken = [item[0] for row in cue.rows for item in row]
        next_time = {id(w): min(cue.end, nxt.start) for w, nxt in zip(spoken, spoken[1:])}

        for row in cue.rows:
            row_width = sum(item[2] for item in row) + (len(row) - 1) * cue.space
            x = (width - row_width) / 2
            for word, text, advance in row:
                start, end = max(cue.start, word.start), next_time.get(id(word), cue.end)
                tags = fr"\an5\pos({round(x + advance / 2)},{round(y)})\fs{size * font_scale:.2f}\b{font_weight}\bord{outline}\shad{shadow}"
                if animation == "word-shadow":
                    emit(start, end, tags + fr"\c{hi}\shad5\4c{black}\fad(45,35)", text)
                elif animation == "word-3d":
                    depth = max(2, round(size * .09))
                    emit(start, end, tags + fr"\c{hi}\bord1\3c{black}\xshad{depth}\yshad{depth}\4c{black}", text)
                elif animation == "word-outline":
                    emit(start, end, tags + fr"\c{hi}\bord{max(2, round(size * .08))}\3c{black}", text)
                elif animation == "word-bounce":
                    emit(start, end, tags + fr"\c{hi}\fscx78\fscy78\t(0,85,\fscx112\fscy112)\t(85,155,\fscx100\fscy100)", text)
                elif animation == "word-slide":
                    anchor = round(x + advance / 2)
                    move = fr"\move({anchor},{round(y + size * .18)},{anchor},{round(y)},0,120)"
                    emit(start, end, tags.replace(fr"\pos({anchor},{round(y)})", move) + fr"\c{hi}\fad(65,0)", text)
                elif animation == "word-punch":
                    emit(start, end, tags + fr"\c{hi}\fscx120\fscy120\t(0,120,\fscx100\fscy100)", text)
                elif animation == "fade":
                    emit(cue.start, cue.end, tags + fr"\c{hi}\fad(140,80)", text)
                elif animation == "karaoke":
                    # A single word event includes a delay and a left-to-right fill.
                    delay = max(0, round((start - cue.start) * 100))
                    sweep = max(1, round((min(end, word.end) - start) * 100))
                    emit(cue.start, cue.end, tags + fr"\1c{hi}\2c{white}\kt{delay}\kf{sweep}", text)
                elif animation == "reveal":
                    emit(start, cue.end, tags + fr"\c{hi}\fad(70,0)", text)
                else:
                    idle = tags + fr"\c{white}"
                    if animation in ("glow", "word-neon"):
                        idle += fr"\bord{max(outline, 1, round(size * .028))}"
                    emit(cue.start, start, idle, text)
                    emit(end, cue.end, idle, text)
                    active = tags + fr"\c{hi}"
                    if animation in ("glow", "word-neon"):
                        # Keep the halo's fill invisible and draw the sharp letters over it.
                        boxed = False
                        if boxed:
                            emit(start, end, tags + r"\1a&HFF&", text, layer=0)
                        glow = tags + (fr"\1a&HFF&\3c{hi}\3a&H78&\shad0"
                                       fr"\bord{max(2, round(size * .105))}\blur{max(1, round(size * .06))}")
                        emit(start, end, glow, text, name="Shape", layer=int(boxed))
                        sharp = active + fr"\3c{black}\bord{max(1, round(size * .045))}\blur0"
                        emit(start, end, sharp, text, name="Shape", layer=1 + int(boxed))
                        x += advance + cue.space
                        continue
                    elif animation == "pop":
                        active += fr"\3c{hi}\bord{max(2, round(size * .10))}\t(0,130,\bord{outline}\3c{black})"
                    elif animation in ("box", "underline", "word-box"):
                        pad = max(2, round(size * .08))
                        box_w = round(advance + 2 * pad)
                        box_h = round(size * 1.12) if animation in ("box", "word-box") else pad
                        top = round(y - box_h / 2) if animation in ("box", "word-box") else round(y + size * .48)
                        box_tags = fr"\an7\pos({round(x - pad)},{top})\p1\bord0\shad0\c{hi}"
                        emit(start, end, box_tags, f"m 0 0 l {box_w} 0 {box_w} {box_h} 0 {box_h}", name="Shape", layer=0)
                        if animation in ("box", "word-box"):
                            active = tags + fr"\c{black}\bord0\shad0"
                    emit(start, end, active, text)
                x += advance + cue.space
            y += cue.line_height
    return header + "\n".join(events) + "\n"


def for_stacked_panels(ass: str, height: int) -> str:
    """Move the legacy auto-positioned layout as a unit, including word decorations."""
    marker = re.search(r"; CLIPY stack shift: (-?\d+)", ass)
    if marker:
        shift = int(marker[1])
        return re.sub(r"\\pos\((\d+),(\d+)\)", lambda m: fr"\pos({m[1]},{int(m[2]) + shift})", ass) if shift else ass
    baseline = round(height * 0.88)
    return re.sub(r"(?<!\\an5)\\pos\((\d+),\d+\)", lambda m: fr"\pos({m[1]},{baseline})", ass)


def build_srt(words: list[Word], style: CaptionStyle | None = None, width: int = 1080, height: int = 1920) -> str:
    """Export the same cue boundaries and line breaks as the video."""
    def srt_time(t: float) -> str:
        ms = int(round(max(0.0, t) * 1000))
        h, ms = divmod(ms, 3600000)
        m, ms = divmod(ms, 60000)
        s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    cues = [(c.start, c.end, "\n".join(" ".join(w.text.strip() for w, _, _ in row) for row in c.rows))
            for c in _caption_cues(words, style or CaptionStyle(), width, height)]
    return "\n".join(f"{n}\n{srt_time(start)} --> {srt_time(end)}\n{text}\n"
                     for n, (start, end, text) in enumerate(cues, 1) if end > start)

