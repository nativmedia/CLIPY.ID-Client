# CLIPY.ID server

YouTube link → audio → Whisper large-v3 transcript (word timestamps, local GPU) → an LLM
picks the best moments → each clip is cut, reframed, captioned CapCut-style, given a
thumbnail with its title, and optionally a dubbed intro that reads the title.

## Setup

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
```

Needs `ffmpeg`/`ffprobe` (with libass) on PATH. The first transcription downloads the
Whisper model (~3 GB for `large-v3`) into the Hugging Face cache.

Speaker framing needs two small models in `models/`:

```bash
curl -L -o models/face_landmarker.task https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task
curl -L -o models/face_detection_yunet_2023mar.onnx https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx
```

## Speaker framing (crop mode)

`clipy/framing.py`: shot cuts from FFmpeg `scdet`; faces from YuNet; jaw movement per face
from MediaPipe; the speaker is the face whose jaw moves in step with the audio. Each shot
gets stable crops on its speakers, with held switches rather than pans.

Face patches are checked for focus before jaw analysis. Faces below an absolute sharpness
threshold are excluded. The relative threshold is capped near that floor so a usable
profile is not rejected merely because a listener's face is much sharper.
Small audience faces do not set the focus threshold for larger foreground participants.
Temporary detection gaps keep the known subject crop; a new shot with no reliable
detection uses a centered crop. A blurred wide view is an intentional group composition,
never a fallback triggered by a missed face. Focus is rechecked within long speaker turns.

For **9:16 crop**, two consistently visible conversation participants can be shown in
stacked panels; three or more can use three panels, capped at three people. Participants
are ordered left-to-right from the source into top/middle/bottom panels. The active
speaker is retained when selecting three from a larger foreground group.
A quiet foreground interlocutor can
share the frame without moving their mouth or nodding. Face scale and persistence separate
the foreground pair from smaller audience faces; jaw/audio and head-motion signals help
rank otherwise comparable candidates. Panel positions stay stable across speaker changes.
Each panel is cropped independently to a head-and-shoulders view, with the face around
30% of its panel height. Nearby third participants constrain the crop width so their
partial faces do not intrude into the selected subject's panel.
Crop position and zoom are locked per subject for each source-camera shot, using that
shot's face measurements. Periodic focus checks can change the selected subject or
composition but do not recalculate an unchanged subject's crop. Identical adjacent
compositions are merged so speaking turns within the same panels cannot cause small
zoom/position jumps. A camera cut or a different selected subject can still reframe.
Speaker changes require two seconds of continuous past evidence; uncertain or brief
predictions reset confirmation. Within one source-camera shot, composition changes are
held for at least five seconds and skipped in the last three seconds. Partner ranking
uses the whole camera shot rather than changing with each quality window. Source-camera
cuts remain immediate because crop coordinates from the old camera are no longer valid.
Speaker evidence combines normalized inner-lip aperture with jawOpen, which can be
near-zero on speaking profiles. Brief detection gaps up to 2.5 seconds are tolerated
when the face is present for at least 60% of the run, preserving an otherwise clear
speaker instead of substituting a listener. Audio correlation must be supported by mouth motion;
subtle but consistent mouth movement can beat a static listener without forcing a cut
before the existing two-second confirmation period.

Speaker-first policy: the default is a fixed crop of the active speaker, regardless of
how many faces are visible. There is no timed layout cycle. A brief collage requires
strong observed response evidence: sustained mouth activity in both people or repeated
clear nods from the listener. These visual signals approximate a reaction, not its meaning.
A reaction cutaway lasts four seconds, is followed by at least sixteen seconds without
another cutaway, and total planned cutaways are capped at 15% of clip duration. The edit
returns to the active speaker afterward. Quiet listeners never trigger collages solely
because they are visible. Two/three-panel rendering and 6:4 wide rendering remain
available, but a wide layout is not inserted on a timer.
Single-person shots retain subject cropping. Other output ratios keep single framing.
Clips using stacked panels keep captions at a lower baseline to leave close-up faces clear.
This is a visual/audio heuristic, not speaker diarization or semantic reaction recognition;
it requires the selected participants to be visible in the same source shot.
Thumbnails use the same composition as the rendered clip.

These changes apply to new renders; existing clips must be regenerated to use them.

Run framing and FFmpeg regression checks from `server/`:

```bash
.venv/Scripts/python -m unittest discover -s tests -v
```

## Run

Always with the venv's Python (the system Python lacks the GPU libraries):

```bash
.venv/Scripts/python -m uvicorn app:app --host 127.0.0.1 --port 4748
```

The web app (`../web`, port 4747) proxies `/api` here.

In the local project picker, **CLIPY WITH LAST SETTING** restores the latest settings
for that source video, closes the picker, and opens the editable form with START ready.
Previously uploaded logos and thumbnail images are restored too. A video with no earlier
project uses the most recently saved settings from another video. The button does not
start a job; START remains a separate action. Re-clips continue to avoid previously used moments.

## API key ("universal")

One key slot; the provider is recognised from the prefix and checked once:
`sk-ant-` Claude (model `claude-opus-5-5`, via the Anthropic SDK), `sk-` OpenAI or
DeepSeek (both tried), `AIza` Gemini, `sk-or-` OpenRouter, `gsk_` Groq. Stored in `data/settings.json` on this
machine only; the browser only ever sees a masked copy.

## API

| | |
|---|---|
| `GET/POST/DELETE /api/key` | stored key status / insert (`{key}`) / forget |
| `GET /api/options` | ratios, counts, durations, fonts, animations, colours, letter cases, title styles, voices |
| `POST /api/uploads` | form: `file` — a video from this computer → `{id, name, duration}` |
| `POST /api/projects` | form: `settings` (JSON: `clip`, `caption`, `thumb`, `brand`), `url` or `upload_id` (+ `title`), `image`, `logo` |
| `POST /api/projects/{id}/clips/{n}/retry` | make one clip again |
| `GET/PUT /api/projects/{id}/posts` | titles & descriptions per clip and platform |
| `GET /api/projects`, `/api/projects/{id}` | status, `progress` 0–1, `stage`, clips |
| `GET /api/projects/{id}/files/{name}` | `clip_N.mp4`, `thumb_N.jpg`, `clip_N.srt` |
| `GET /api/projects/{id}/assets/{kind}` | original saved `logo` or thumbnail `image` for restoring settings |

## Settings

- `CLIPY_WHISPER_MODEL` — Whisper model name (default `large-v3`; `large-v3-turbo` is faster).
- Caption look per option lives in `clipy/captions.py`; thumbnail title styles in `clipy/thumbs.py`;
  voices in `clipy/tts.py` (Microsoft Edge read-aloud voices via `edge-tts`, needs internet).

Project files are kept in `data/projects/<id>/`.
