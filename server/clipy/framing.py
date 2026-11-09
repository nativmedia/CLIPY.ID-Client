"""
Speaker framing for "crop" mode: keep whoever is talking in frame, as a series of still
shots (never a pan or a drift), tighter on wide shots.

  1. Shot cuts come from FFmpeg's scene detector (scdet).
  2. Frames are sampled a few times a second. OpenCV's YuNet finds every face (it copes with
     the small, distant faces of wide podcast shots); each face is then cut out, enlarged and
     handed to MediaPipe's Face Landmarker for a jawOpen score.
  3. Within each shot, faces are grouped by where they sit. Every half second the speaker is
     the face whose jaw opens and closes in step with the loudness of the audio around that
     moment (correlation, so a face turned slightly away still counts fully); when that's too
     close to call, the face whose jaw moves most while words are heard.
  4. Those moment-by-moment calls become still segments. Inside a shot the framing only moves
     to a new speaker once the current framing has held for MIN_HOLD seconds, the new speaker
     has kept talking for a while, and there is at least MIN_HOLD left before the next cut;
     at a cut it simply follows the new picture.
  5. Each segment's crop is centred on its speaker. On wide shots (small faces) it zooms in
     until the face fills about TARGET_FACE of the frame height, up to MAX_ZOOM.
  6. In portrait output, foreground groups can use two or three stacked close-ups. Long
     group shots vary deliberately with held speaker close-ups and a centered 6:4 wide
     view over blur. Crop geometry stays locked for each person/layout in a camera shot.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks.python import BaseOptions, vision

from .captions import Word

MODELS = Path(__file__).resolve().parent.parent / "models"
LANDMARKER = MODELS / "face_landmarker.task"
YUNET = MODELS / "face_detection_yunet_2023mar.onnx"

# Fast enough to see the jaw move with syllables.
SAMPLE_FPS = 6
# scdet score above which a frame change counts as a cut; camera switches score 10+.
CUT_THRESHOLD = 10
MIN_SHOT = 0.8
# Faces closer than this (fraction of frame width) are the same person within a shot.
SAME_FACE = 0.08

# Speaker calls are made every STEP seconds from the WINDOW seconds either side.
STEP = 0.5
WINDOW = 2.0
# Framing inside a shot never changes sooner than this after the last change...
MIN_HOLD = 3.0
# ...and only for a new speaker who keeps talking at least this long.
CONFIRM = 2.0
# A sync lead smaller than this is too close to call.
MARGIN = 0.08

# Face height as a share of the frame height that a wide shot is zoomed to.
TARGET_FACE = 0.2
# Beyond this the picture gets visibly soft.
MAX_ZOOM = 1.6
# Measured on a fixed-size face patch so distant faces are not rewarded by scale.
MIN_SHARPNESS = 35.0
RELATIVE_SHARPNESS = 0.25
FOREGROUND_SIZE = 0.70
PANEL_FACE = 0.30  # face height within one half-frame, rather than the whole portrait
LAYOUT_HOLD = 4.0


@dataclass
class Segment:
    start: float  # seconds into the clip
    x: int  # crop rectangle in source pixels
    y: int
    w: int
    h: int
    secondary: tuple[int, int, int, int] | None = None
    fit: bool = False  # preserve the whole shot when no reliable face is visible
    tertiary: tuple[int, int, int, int] | None = None
    wide: bool = False  # centered 6:4 foreground over a portrait blurred background

    @property
    def layout(self) -> str:
        if self.wide:
            return "wide"
        return "triple" if self.tertiary is not None else "double" if self.secondary is not None else "single"


@dataclass
class Plan:
    segments: list[Segment]

    def at(self, t: float) -> Segment:
        seg = self.segments[0]
        for s in self.segments:
            if s.start <= t:
                seg = s
        return seg


def _probe(src: Path) -> tuple[int, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
         "-of", "csv=p=0", str(src)],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    w, h = (int(v) for v in out.split(",")[:2])
    return w, h


def shot_cuts(src: Path) -> list[float]:
    """Times (seconds) where the picture cuts to a new shot."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(src), "-vf", f"scdet=threshold={CUT_THRESHOLD}:sc_pass=0",
         "-an", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    cuts: list[float] = []
    for m in re.finditer(r"lavfi\.scd\.time: ([\d.]+)", proc.stderr):
        t = float(m.group(1))
        if t >= MIN_SHOT and (not cuts or t - cuts[-1] >= MIN_SHOT):
            cuts.append(t)
    return cuts


_landmarker = None


def _landmarks():
    global _landmarker
    if _landmarker is None:
        _landmarker = vision.FaceLandmarker.create_from_options(
            vision.FaceLandmarkerOptions(
                base_options=BaseOptions(model_asset_path=str(LANDMARKER)),
                num_faces=1,
                min_face_detection_confidence=0.3,
                min_face_presence_confidence=0.3,
                output_face_blendshapes=True,
            )
        )
    return _landmarker


def _mouth_signal(result, width: int, height: int) -> float:
    """Lip aperture stays useful on profiles where jawOpen can approach zero."""
    jaw = next((s.score for s in result.face_blendshapes[0] if s.category_name == "jawOpen"), 0.0) if result.face_blendshapes else 0.0
    if not result.face_landmarks:
        return jaw
    points = result.face_landmarks[0]
    def distance(a, b):
        return np.hypot((points[a].x - points[b].x) * width, (points[a].y - points[b].y) * height)
    aperture = float(np.clip(distance(13, 14) / max(distance(61, 291), 1.0), 0.0, 1.0))
    return 0.75 * aperture + 0.25 * jaw


def _jaw_open(frame: np.ndarray, box) -> float:
    """jawOpen for one detected face: cut it out with some margin, enlarge, run the landmarker."""
    x, y, w, h = box
    pad = 0.45
    x0, y0 = int(max(0, x - w * pad)), int(max(0, y - h * pad))
    x1, y1 = int(min(frame.shape[1], x + w * (1 + pad))), int(min(frame.shape[0], y + h * (1 + pad)))
    crop = frame[y0:y1, x0:x1]
    if crop.size == 0:
        return 0.0
    crop = cv2.resize(crop, (256, round(256 * crop.shape[0] / crop.shape[1])))
    result = _landmarks().detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)))
    return _mouth_signal(result, crop.shape[1], crop.shape[0])


def _sharpness(frame: np.ndarray, box) -> float:
    """Focus estimate from the interior of a face, avoiding background edges."""
    x, y, w, h = box
    x0, y0 = max(0, int(x + w * 0.15)), max(0, int(y + h * 0.15))
    x1, y1 = min(frame.shape[1], int(x + w * 0.85)), min(frame.shape[0], int(y + h * 0.85))
    patch = frame[y0:y1, x0:x1]
    if min(patch.shape[:2], default=0) < 16:
        return 0.0
    gray = cv2.cvtColor(cv2.resize(patch, (96, 96), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def _clear_foreground(candidates):
    """Filter (sharpness, x, y, width, height) observations at comparable scale."""
    largest = max((f[4] for f in candidates), default=0.0)
    foreground = [f for f in candidates if f[4] >= largest * FOREGROUND_SIZE]
    best = max((f[0] for f in foreground), default=0.0)
    # Relative focus is only a tie-breaker near the blur floor. A crisp close-up
    # must not eliminate a usable profile (e.g. 210 vs 1181 on the reported clip).
    threshold = max(MIN_SHARPNESS, min(best * RELATIVE_SHARPNESS, MIN_SHARPNESS * 2))
    return [f for f in foreground if f[0] >= threshold]


def sample_faces(src: Path) -> list[tuple[float, list[tuple[float, float, float, float]]]]:
    """[(t, [(cx, cy, jaw_open, face_height), ...])] with positions and sizes as fractions of the frame."""
    cap = cv2.VideoCapture(str(src))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    step = max(1, round(fps / SAMPLE_FPS))
    detector = None
    samples = []
    index = 0
    while True:
        ok = cap.grab()
        if not ok:
            break
        if index % step == 0:
            ok, frame = cap.retrieve()
            if not ok:
                break
            h, w = frame.shape[:2]
            if detector is None:
                detector = cv2.FaceDetectorYN.create(str(YUNET), "", (w, h), 0.6, 0.3, 5000)
            _, found = detector.detect(frame)
            faces = []
            candidates = []
            for f in found if found is not None else []:
                bx, by, bw, bh = (float(v) for v in f[:4])
                # Ignore tiny background faces (posters, people far behind).
                if bh < h * 0.05:
                    continue
                quality = _sharpness(frame, (bx, by, bw, bh))
                candidates.append((quality, bx, by, bw, bh))
            # Compare focus among similarly sized foreground faces. A tiny, crisp
            # audience face must not disqualify the larger podcast participants.
            for quality, bx, by, bw, bh in _clear_foreground(candidates):
                faces.append(((bx + bw / 2) / w, (by + bh / 2) / h, _jaw_open(frame, (bx, by, bw, bh)), bh / h))
            samples.append((index / fps, faces))
        index += 1
    cap.release()
    return samples


def _speaking(words: list[Word], t: float) -> bool:
    return any(w.start - 0.15 <= t <= w.end + 0.15 for w in words)


class Loudness:
    """Audio loudness (RMS) around any moment of the clip."""

    RATE = 16000

    def __init__(self, src: Path):
        pcm = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", str(self.RATE), "-f", "s16le", "-"],
            capture_output=True,
        ).stdout
        self.audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768

    def __call__(self, t: float) -> float:
        half = 0.5 / SAMPLE_FPS
        seg = self.audio[max(0, int((t - half) * self.RATE)):max(0, int((t + half) * self.RATE))]
        return float(np.sqrt(np.mean(seg**2))) if len(seg) else 0.0


# ---------- who is where, and who is talking ----------

@dataclass
class Person:
    """One face seen across a shot: where it sits and its jaw over time."""

    cx: float
    track: list[tuple[float, float, bool, float, float]]  # (t, jaw, words heard, face height, cy)
    xs: list[float]


def _people(samples) -> list[Person]:
    people: list[Person] = []
    for t, faces in samples:
        for cx, cy, jaw, fh in faces:
            near = min(people, key=lambda p: abs(p.cx - cx), default=None)
            if near is None or abs(near.cx - cx) > SAME_FACE:
                near = Person(cx, [], [])
                people.append(near)
            near.track.append((t, jaw, False, fh, cy))
            near.xs.append(cx)
            near.cx = float(np.median(near.xs))
    # Ignore fleeting detections: a person has to be there for a good part of the shot.
    seen = max((len(p.track) for p in people), default=0)
    stable = [p for p in people if len(p.track) >= max(2, seen * 0.3)] or people
    largest = max((float(np.median([s[3] for s in p.track])) for p in stable), default=0.0)
    return [p for p in stable if float(np.median([s[3] for s in p.track])) >= largest * FOREGROUND_SIZE]


def _sync(track, loudness) -> float | None:
    """Best correlation between a face's jaw and the audio loudness, allowing ±1 sample of lag."""
    if len(track) < 8:
        return None
    jaw = np.array([s[1] for s in track])
    env = np.array([loudness(s[0]) for s in track])
    if jaw.std() < 1e-3 or env.std() < 1e-6:
        return None
    best = -1.0
    for lag in (-1, 0, 1):
        a = jaw[max(0, -lag):len(jaw) - max(0, lag)]
        b = env[max(0, lag):len(env) - max(0, -lag)]
        best = max(best, float(np.corrcoef(a, b)[0, 1]))
    return best


def _motion(track) -> float:
    """How much the jaw moves from one sample to the next while words are heard."""
    gap = 1.5 / SAMPLE_FPS
    moves = [abs(b[1] - a[1]) for a, b in zip(track, track[1:]) if b[0] - a[0] <= gap and a[2] and b[2]]
    return float(np.mean(moves)) if moves else 0.0


def _speaker_at(people: list[Person], t: float, loudness) -> int | None:
    """Index of the person talking around time t, or None when it's too close to call."""
    windows = [[s for s in p.track if abs(s[0] - t) <= WINDOW]
               if any(abs(s[0] - t) <= STEP for s in p.track) else [] for p in people]
    visible = [i for i, w in enumerate(windows) if w]
    if len(visible) == 1:
        return visible[0]
    motions = {i: _motion(w) for i, w in enumerate(windows) if w}
    # Audio correlation alone can reward a nearly still listener. Require actual
    # mouth motion, and allow a single valid speaking face among silent listeners.
    synced = [(_sync(w, loudness), i) for i, w in enumerate(windows) if w and motions[i] >= 0.003]
    synced = [(s, i) for s, i in synced if s is not None]
    if len(synced) >= 2:
        synced.sort(reverse=True)
        if synced[0][0] - synced[1][0] >= MARGIN and synced[0][0] > 0.15:
            return synced[0][1]
    if len(synced) == 1 and synced[0][0] > 0.25:
        return synced[0][1]
    moving = sorted(((_motion(w), i) for i, w in enumerate(windows) if w), reverse=True)
    if len(moving) >= 2 and moving[0][0] > 0.006 and moving[0][0] >= max(0.002, moving[1][0]) * 1.8:
        return moving[0][1]
    if len(moving) == 1:
        return moving[0][1]
    return None


def _overall_speaker(people: list[Person], loudness) -> int:
    """Who talks most across the whole shot: the starting choice, and the fallback."""
    if len(people) == 1:
        return 0
    moving = sorted(((_motion(p.track), i) for i, p in enumerate(people)), reverse=True)
    if moving[0][0] > .02 and moving[0][0] > max(.002, moving[1][0]) * 2.8:
        return moving[0][1]
    synced = [(s, i) for i, p in enumerate(people)
              if _motion(p.track) >= 0.003 and (s := _sync(p.track, loudness)) is not None and s > 0.15]
    if synced:
        return max(synced)[1]
    return max(range(len(people)), key=lambda i: _motion(people[i].track))


def _hold(people: list[Person], start: float, end: float, loudness) -> list[tuple[float, int]]:
    """Turn moment-by-moment speaker calls into (start, person) runs that respect MIN_HOLD."""
    calls = [(t, _speaker_at(people, t, loudness)) for t in np.arange(start, end, STEP)]
    first = next((who for _, who in calls[: int(MIN_HOLD / STEP)] if who is not None), None)
    current = first if first is not None else _overall_speaker(people, loudness)
    # Brief profile dropouts at a cut must not lock the first visible listener
    # in for the entire shot when the full shot clearly favors another mouth.
    if all(isinstance(p, Person) for p in people):
        overall = _overall_speaker(people, loudness)
        strongest = _motion(people[overall].track)
        if strongest > .02 and strongest > max(.002, _motion(people[current].track)) * 2.8:
            current = overall
    runs = [(start, current)]
    pending, pending_since = None, start
    for t, who in calls:
        if who is None or who == current:
            pending = None
            continue
        if who != pending:
            pending, pending_since = who, t
        # Confirm elapsed evidence, never look ahead and switch prematurely.
        if t - pending_since < CONFIRM:
            continue
        if t - runs[-1][0] < MIN_HOLD or end - t < MIN_HOLD:
            continue
        current = who
        pending = None
        runs.append((float(t), who))
    return runs


def _crop_on(person: Person, t0: float, t1: float, src_w: int, src_h: int, base_w: int, base_h: int,
             face_y: float = 0.38) -> tuple[int, int, int, int]:
    """Still crop around one person, zoomed in when their face is small in the frame."""
    seen = [s for s in person.track if t0 - 0.5 <= s[0] <= t1 + 0.5] or person.track
    face_h = float(np.median([s[3] for s in seen]))
    cy = float(np.median([s[4] for s in seen]))
    zoom = float(np.clip(TARGET_FACE / max(face_h * src_h / base_h, 1e-3), 1.0, MAX_ZOOM))
    w = int(base_w / zoom) // 2 * 2
    h = int(base_h / zoom) // 2 * 2
    x = int(np.clip(person.cx * src_w - w / 2, 0, src_w - w)) // 2 * 2
    # Keep the face in the upper part of the frame, with room for captions below.
    y = int(np.clip(cy * src_h - h * face_y, 0, src_h - h)) // 2 * 2
    return x, y, w, h


def _visible(person: Person, start: float, end: float) -> bool:
    """Require clear detections throughout a run, including its beginning and end."""
    times = [s[0] for s in person.track if start <= s[0] < end]
    if not times:
        return False
    gaps = np.diff([start, *times, end])
    return len(times) >= max(2, (end - start) * SAMPLE_FPS * 0.6) and max(gaps) <= CONFIRM + STEP


def _panel_crop(person: Person, others: list[Person], start: float, end: float,
                src_w: int, src_h: int, ratio: float) -> tuple[int, int, int, int]:
    """An independent head-and-shoulders crop for each stacked panel.

    Size it from this person's face, not the distance to the other selected
    speaker. Nearby third participants constrain the width to avoid half faces.
    """
    seen = [s for s in person.track if start - 0.5 <= s[0] <= end + 0.5] or person.track
    face_h = float(np.median([s[3] for s in seen])) * src_h
    cy = float(np.median([s[4] for s in seen])) * src_h
    h = min(src_h, src_w / ratio, face_h / PANEL_FACE)
    w = h * ratio
    gaps = [abs(p.cx - person.cx) * src_w for p in others if p is not person]
    if gaps:
        w = min(w, max(face_h * 1.6, min(gaps) * 1.35))
        h = w / ratio
    w, h = max(2, int(w) // 2 * 2), max(2, int(h) // 2 * 2)
    x = int(np.clip(person.cx * src_w - w / 2, 0, src_w - w)) // 2 * 2
    y = int(np.clip(cy - h * 0.25, 0, src_h - h)) // 2 * 2
    return x, y, w, h


def _partner(people: list[Person], who: int, start: float, end: float, loudness) -> int | None:
    """A foreground conversation partner; listening does not require jaw movement.

    This is a jaw/audio heuristic, not speaker diarization or emotion recognition.
    Keep panel order stable by source position rather than swapping on every turn.
    """
    candidates = []
    for i, person in enumerate(people):
        if i == who or abs(person.cx - people[who].cx) < SAME_FACE * 2 or not _visible(person, start, end):
            continue
        context = [s for s in person.track if start - WINDOW <= s[0] <= end + WINDOW]
        primary_context = [s for s in people[who].track if start - WINDOW <= s[0] <= end + WINDOW]
        size = float(np.median([s[3] for s in context]))
        primary_size = float(np.median([s[3] for s in primary_context]))
        if min(size, primary_size) < max(size, primary_size) * FOREGROUND_SIZE:
            continue
        sync = _sync(context, loudness)
        motion = _motion(context)
        # A listener can respond with a nod while their jaw remains still. Require
        # repeated vertical movement during speech, not a single noisy detection.
        heard = [s for s in context if s[2]]
        head_moves = [abs(b[4] - a[4]) for a, b in zip(heard, heard[1:])
                      if b[0] - a[0] <= 1.5 / SAMPLE_FPS]
        nodding = (len(head_moves) >= 8 and np.ptp([s[4] for s in heard]) > 0.015
                   and float(np.mean(head_moves)) > 0.0025)
        # A quiet interlocutor at the same visual scale is still part of the
        # conversation. Rank by prominence first so animated audience members
        # cannot take the second panel from the other foreground participant.
        response = motion + (0.02 if nodding else 0) + max(0.0, sync or 0.0) * 0.02
        candidates.append((size, response, i))
    return max(candidates)[2] if candidates else None


def _append_segment(segments: list[Segment], segment: Segment) -> None:
    """Identical compositions form one continuous shot, not repeated cuts."""
    if segments:
        last = segments[-1]
        if (last.x, last.y, last.w, last.h, last.secondary, last.tertiary, last.wide, last.fit) == (
                segment.x, segment.y, segment.w, segment.h, segment.secondary, segment.tertiary, segment.wide, segment.fit):
            return
    segments.append(segment)


def _responders(people: list[Person], who: int, t: float) -> list[int]:
    main = [s for s in people[who].track if t - 2 <= s[0] <= t]
    responding = []
    for i, p in enumerate(people):
        if i == who or not _visible(p, t - 2, t):
            continue
        track = [s for s in p.track if t - 2 <= s[0] <= t]
        ys = np.array([s[4] for s in track])
        changes = np.diff(ys)
        turns = sum(a * b < 0 for a, b in zip(changes, changes[1:]) if abs(a) > .003 and abs(b) > .003)
        nod = len(ys) >= 8 and np.ptp(ys) > .035 and turns >= 2
        animated_reply = _motion(track) > .045 and _motion(main) > .045
        if nod or animated_reply:
            responding.append(i)
    return sorted(responding, key=lambda i: _motion([s for s in people[i].track if t-2 <= s[0] <= t]), reverse=True)


def _reaction_runs(people: list[Person], start: float, end: float,
                   speaker_runs: list[tuple[float, int]], allowance: float,
                   earliest: float) -> list[tuple[float, str]]:
    """Speaker-first editing; only brief, strongly evidenced response cutaways.

    Mouth activity in two people suggests an animated exchange/laughter. A clear
    repeated nod can qualify too. These are visual heuristics, not emotion labels.
    """
    runs = [(start, "single")]
    if allowance < 4 or end - start < 10 or len(people) < 2:
        return runs
    for t in np.arange(max(start + 5, earliest), end - 7, STEP):
        who = next(w for at, w in reversed(speaker_runs) if at <= t)
        main = [s for s in people[who].track if t - 2 <= s[0] <= t]
        if not _visible(people[who], t - 2, t):
            continue
        responding = _responders(people, who, float(t))
        if responding:
            runs.extend([(float(t), "group"), (float(t + 4), "single")])
            break  # at most one brief reaction per source shot
    return runs


def _held_group_runs(people: list[Person], start: float, end: float,
                     speaker_runs: list[tuple[float, int]], earliest: float) -> list[tuple[float, str]]:
    """Use one held two/three-person view for a long, clearly detected group.

    This is deliberately a single, deterministic edit choice per camera shot,
    rather than a random choice at every sampled frame.  It waits for the
    ordinary speaker hold before changing composition, holds the collage for a
    fixed beat, then returns to the speaker so the frame never flickers.
    """
    runs = [(start, "single")]
    if len(people) < 2 or end - start < 2 * MIN_HOLD + LAYOUT_HOLD:
        return runs
    first = max(start + MIN_HOLD, earliest)
    last = end - LAYOUT_HOLD - MIN_HOLD
    for t in np.arange(first, last + STEP / 2, STEP):
        who = next(who for at, who in reversed(speaker_runs) if at <= t)
        window_start, window_end = float(t - MIN_HOLD), float(t + LAYOUT_HOLD)
        if not _visible(people[who], window_start, window_end):
            continue
        if any(i != who and _visible(person, window_start, window_end)
               for i, person in enumerate(people)):
            runs.extend([(float(t), "group"), (float(t + LAYOUT_HOLD), "single")])
            break
    return runs


def _center_wide(src_w: int, src_h: int) -> tuple[int, int, int, int]:
    h = int(min(src_h, src_w * 4 / 6)) // 2 * 2
    w = int(h * 6 / 4) // 2 * 2
    return (src_w - w) // 4 * 2, (src_h - h) // 4 * 2, w, h


def _wide_contains(people: list[Person], crop, src_w: int, src_h: int) -> bool:
    """Only use the centered wide view when it contains the visible faces."""
    x, y, w, h = crop
    for person in people:
        fh = float(np.median([s[3] for s in person.track])) * src_h
        cy = float(np.median([s[4] for s in person.track])) * src_h
        cx = person.cx * src_w
        if not (x <= cx - fh * 0.4 and cx + fh * 0.4 <= x + w
                and y <= cy - fh * 0.65 and cy + fh * 0.65 <= y + h):
            return False
    return True


def plan_crop(src: Path, width: int, height: int, words: list[Word]) -> Plan | None:
    """Still crop segments following the speaker, or None when no crop is needed."""
    src_w, src_h = _probe(src)
    target = width / height
    if abs(src_w / src_h - target) < 0.02:
        return None
    if src_w / src_h > target:
        base_w, base_h = int(src_h * target) // 2 * 2, src_h // 2 * 2
    else:
        base_w, base_h = src_w // 2 * 2, int(src_w / target) // 2 * 2

    samples = sample_faces(src)
    loudness = Loudness(src)
    duration = samples[-1][0] + 1 / SAMPLE_FPS if samples else 0
    bounds = [0.0, *(t for t in shot_cuts(src) if t < duration), duration]

    segments: list[Segment] = []
    reaction_remaining = duration * 0.15
    reaction_after = 0.0
    for start, end in zip(bounds, bounds[1:]):
        shot = [(t, faces) for t, faces in samples if start <= t < end]
        people = _people(shot)
        if not people:
            # Crop mode always fills the output. Never silently switch it to fit.
            _append_segment(segments, Segment(start, (src_w - base_w) // 4 * 2,
                                             (src_h - base_h) // 4 * 2, base_w, base_h))
            continue
        for p in people:
            p.track = [(t, jaw, _speaking(words, t), fh, cy) for t, jaw, _, fh, cy in p.track]
        # A seated subject gets one fixed rectangle per source-camera shot.
        # Recomputing these medians in each 3-second quality window caused tiny
        # zoom/position jumps even when the subjects and composition were unchanged.
        single_crops = [_crop_on(p, start, end, src_w, src_h, base_w, base_h) for p in people]
        portrait = abs(target - 9 / 16) < 0.01
        panel_crops = {count: [_panel_crop(p, people, start, end, src_w, src_h, width / (height / count))
                               for p in people] for count in (2, 3)} if portrait else {}
        wide_crop = _center_wide(src_w, src_h)
        current_composition = None
        changed_at = start
        runs = _hold(people, start, min(end, duration), loudness)
        speaker_changes = {t for t, _ in runs[1:]}
        layouts = _reaction_runs(people, start, end, runs, reaction_remaining, reaction_after) if portrait else [(start, "single")]
        if portrait and len(layouts) == 1:
            layouts = _held_group_runs(people, start, end, runs, reaction_after)
        if len(layouts) > 1:
            reaction_remaining -= 4
            reaction_after = layouts[-1][0] + 16
        # Quality checks also run inside long turns; a subject going out of focus
        # must not remain selected until the next camera cut or speaker change.
        boundaries = {t for t, _ in runs} | {t for t, _ in layouts}
        for check_at in np.arange(start + MIN_HOLD, end - MIN_HOLD + 1e-6, MIN_HOLD):
            if all(abs(check_at - t) >= MIN_HOLD for t in boundaries):
                boundaries.add(float(check_at))
        boundaries = sorted(boundaries)
        runs = [(t, next(who for rt, who in reversed(runs) if rt <= t)) for t in boundaries]
        for i, (t0, who) in enumerate(runs):
            t1 = runs[i + 1][0] if i + 1 < len(runs) else end
            visible = [j for j, p in enumerate(people) if _visible(p, t0, t1)]
            if who not in visible:
                if visible:
                    who = max(visible, key=lambda j: _motion(people[j].track))
                # With transient detection gaps keep this shot's known subject.
                # The crop remains focused instead of adding blurred letterboxing.
            mode = next(mode for at, mode in reversed(layouts) if at <= t0)
            # Rank partners over the camera shot, not each quality window: small
            # confidence changes must not alternate listeners in the same layout.
            reaction_at = next(at for at, value in reversed(layouts) if at <= t0)
            responses = _responders(people, who, reaction_at) if mode == "group" else []
            if mode == "group" and not responses:
                # The deliberate long-shot variation may contain quiet listeners.
                # They are still eligible when they are stable, foreground faces.
                responses = sorted(
                    (j for j in visible if j != who and abs(people[j].cx - people[who].cx) >= SAME_FACE * 2),
                    key=lambda j: (float(np.median([s[3] for s in people[j].track])), _motion(people[j].track)),
                    reverse=True,
                )
            partner = next((j for j in responses if j in visible), None)
            if partner not in visible:
                partner = None
            selected = [who]
            if partner is not None:
                selected.append(partner)
                # Keep the speaker, then the most active remaining participant;
                # cap the collage at three even when the foreground group is larger.
                others = [j for j in responses if j in visible and j not in selected and
                          all(abs(people[j].cx - people[k].cx) >= SAME_FACE * 2 for k in selected)]
                if others and mode == "group":
                    selected.append(max(others, key=lambda j: (
                        _motion(people[j].track), float(np.median([s[3] for s in people[j].track])))))
            if mode == "wide" and len(visible) >= 2 and _wide_contains([people[j] for j in visible], wide_crop, src_w, src_h):
                composition = ("wide", tuple(visible))
            elif mode != "single" and len(selected) >= 2:
                composition = ("panels", tuple(sorted(selected, key=lambda j: people[j].cx)))
            else:
                composition = ("single", (who,))
            if (current_composition is not None and composition != current_composition
                    and t0 not in {at for at, _ in layouts[1:]}
                    and t0 not in speaker_changes
                    and (t0 - changed_at < max(MIN_HOLD, 5.0) or end - t0 < MIN_HOLD)):
                composition = current_composition
            if composition != current_composition:
                current_composition, changed_at = composition, t0
            kind, ids = composition
            if kind == "wide":
                segment = Segment(round(t0, 3), *wide_crop, wide=True)
            elif kind == "panels":
                crops = [panel_crops[len(ids)][j] for j in ids]
                segment = Segment(round(t0, 3), *crops[0], secondary=crops[1],
                                  tertiary=crops[2] if len(crops) == 3 else None)
            else:
                segment = Segment(round(t0, 3), *single_crops[ids[0]])
            _append_segment(segments, segment)
    if not segments:
        segments = [Segment(0.0, (src_w - base_w) // 4 * 2,
                            (src_h - base_h) // 4 * 2, base_w, base_h)]
    return Plan(segments)
