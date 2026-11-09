"""
Projects: one YouTube video → several captioned shorts.

    downloading   audio of the whole video (or, for an uploaded file, just its sound)
    transcribing  Whisper large-v3 on the GPU, word timestamps
    selecting     the LLM behind the stored key picks the clips
    clipping      per clip: fetch the section, frame the speaker, render with captions,
                  thumbnail, dubbed intro

One scheduler runs projects in order, each in its own process. Everything for a
project lives in data/projects/<id>/, and project.json survives server restarts.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
import uuid
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from urllib.parse import quote
import re

from . import captions, framing, media, select, social, thumbs, tts, uploads
from .captions import CaptionStyle, Word

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "projects"
TERMINAL = {"done", "error", "cancelled"}

# Share of the progress bar for each stage; clipping is split evenly between clips.
STAGES = {"queued": (0, 0), "downloading": (0, 0.08), "transcribing": (0.08, 0.4), "selecting": (0.4, 0.45), "clipping": (0.45, 1.0)}

_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def clip_file_stem(clip: dict) -> str:
    """A readable, Windows- and FFmpeg-safe base name for final deliverables."""
    number = int(clip.get("n", 0) or 0)
    title = re.sub(r"[^\w -]+", " ", str(clip.get("title") or "clip"), flags=re.UNICODE)
    title = re.sub(r"\s+", " ", title).strip(" .")[:100].rstrip(" .") or "clip"
    if title.upper() in _WINDOWS_RESERVED:
        title = f"_{title}"
    return f"{number:02d} - {title}"


@dataclass
class Project:
    id: str
    url: str
    video_id: str
    settings: dict
    title: str = ""
    duration: float = 0
    language: str | None = None
    status: str = "queued"
    progress: float = 0.0
    stage: str = "queued"
    error: str | None = None
    clips: list[dict] = field(default_factory=list)
    upload: str | None = None
    logo: str | None = None
    subtitle: str | None = None
    music: str | None = None
    music_url: str | None = None
    transcript_origin: str | None = None
    transcript_note: str | None = None
    transcript_timing: str | None = None
    transcription_backend: str | None = None
    transcription_note: str | None = None
    # Set when the video came from the user's computer instead of YouTube.
    upload_id: str | None = None
    # Set when this is a re-clip of a video done before: that project's transcript is reused.
    reuse_from: str | None = None
    # A folder explicitly picked by the local user for a copy of completed results.
    export_dir: str | None = None
    export_status: str | None = None
    export_error: str | None = None
    created: float = field(default_factory=time.time)

    @property
    def local_video(self) -> Path | None:
        return uploads.path_of(self.upload_id) if self.upload_id else None

    @property
    def source_key(self) -> str:
        """Same for every project made from the same video, wherever it came from."""
        return f"upload:{self.upload_id}" if self.upload_id else f"youtube:{self.video_id}"

    @property
    def dir(self) -> Path:
        return DATA_DIR / self.id

    @property
    def export_output_dir(self) -> Path | None:
        if not self.export_dir:
            return None
        safe_title = "".join(ch if ch.isalnum() or ch in " -_" else " " for ch in self.title).strip()
        safe_title = "-".join(safe_title.split())[:60] or "project"
        return Path(self.export_dir) / f"CLIPY-{safe_title}-{self.id}"

    def clip_asset_name(self, clip: dict, extension: str) -> str:
        return f"{clip_file_stem(clip)}.{extension.lstrip('.')}"

    def clip_asset_path(self, clip: dict, extension: str) -> Path:
        return self.dir / self.clip_asset_name(clip, extension)

    def file_url(self, name: str) -> str:
        return f"/api/projects/{self.id}/files/{quote(name, safe='')}"

    def public(self) -> dict:
        d = asdict(self)
        d.pop("upload")
        d.pop("export_dir")
        d["has_export_dir"] = self.export_output_dir is not None
        d["export_ready"] = bool(self.export_output_dir and self.export_output_dir.is_dir())
        d["has_logo"] = bool(d.pop("logo"))
        d["progress"] = round(self.progress, 3)
        # Always the source video's own thumbnail, so projects are easy to recognise.
        if self.video_id:
            d["thumbnail"] = f"https://i.ytimg.com/vi/{self.video_id}/mqdefault.jpg"
        else:
            d["thumbnail"] = self.file_url("poster.jpg") if (self.dir / "poster.jpg").exists() else ""
        for c in d["clips"]:
            for key in ("video", "thumb", "srt", "ass"):
                if c.get(key):
                    c[key] = self.file_url(c[key])
        return d

    def save(self) -> None:
        tmp = self.dir / "project.json.tmp"
        tmp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        # Windows may briefly lock the destination while the API reads a checkpoint.
        for attempt in range(5):
            try:
                tmp.replace(self.dir / "project.json")
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.02)


_projects: dict[str, Project] = {}
_queue: queue.Queue[str | tuple | None] = queue.Queue()
_lock = threading.RLock()
_active: tuple[str, subprocess.Popen] | None = None
_scheduler: threading.Thread | None = None
_shutting_down = False


def _terminate(process: subprocess.Popen) -> None:
    if process.poll() is None:
        if os.name == "nt":
            result = subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                    capture_output=True, timeout=3, creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode and process.poll() is None:
                raise OSError("Windows gagal menghentikan proses proyek")
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    process.wait(timeout=2)


def stop(p: Project) -> Project:
    with _lock:
        current = get(p.id)
        if current is None or current.status in ("done", "error"):
            raise ValueError("Proyek sudah berhenti")
        if current.status != "cancelled":
            if _active and _active[0] == p.id:
                _terminate(_active[1])
                current = _checkpoint(p.id) or current
            clips = [clip if clip.get("status") == "done" else
                     {**clip, "status": "cancelled", "video": None, "thumb": None, "srt": None}
                     for clip in current.clips]
            current = replace(current, status="cancelled", stage="dihentikan", error=None, clips=clips)
            current.save()
            _projects[p.id] = current
        return current


def _active_running(project_id: str) -> bool:
    if _active is None or _active[0] != project_id:
        return False
    poll = getattr(_active[1], "poll", None)
    return True if poll is None else poll() is None


def delete_stopped(p: Project) -> None:
    with _lock:
        current = _projects.get(p.id)
        if (current is None or current.status not in ("cancelled", "error")
                or _active_running(current.id)):
            raise ValueError("Tunggu hingga proses berhenti")
        if p.dir.resolve().parent != DATA_DIR.resolve() or p.dir.is_symlink():
            raise ValueError("Folder proyek tidak valid")
        shutil.rmtree(p.dir)
        _projects.pop(p.id, None)


def _checkpoint(project_id: str) -> Project | None:
    try:
        return Project(**json.loads((DATA_DIR / project_id / "project.json").read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return None


def _load(*, interrupted: bool = False) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for f in DATA_DIR.glob("*/project.json"):
        p = _checkpoint(f.parent.name)
        if p is None:
            continue
        if interrupted and p.status not in TERMINAL:
            p.status, p.error, p.stage = "error", "interrupted: the server restarted", "interrupted"
            p.save()
        if p.status in TERMINAL and migrate_clip_assets(p):
            p.save()
        _projects[p.id] = p


def migrate_clip_assets(p: Project) -> bool:
    """Give legacy final outputs the same readable names used by new projects."""
    changed = False
    for clip in p.clips:
        if not isinstance(clip.get("n"), int):
            continue
        n = clip["n"]
        for key, extension, legacy in (
            ("video", "mp4", (f"clip_{n}.mp4",)),
            ("srt", "srt", (f"clip_{n}.srt",)),
            ("ass", "ass", (f"clip_{n}.ass", f"film_{n}.ass")),
        ):
            current = clip.get(key)
            candidates = [current] if isinstance(current, str) and Path(current).name == current else []
            candidates.extend(name for name in legacy if name not in candidates)
            source = next((p.dir / name for name in candidates if (p.dir / name).is_file()), None)
            if source is None:
                continue
            target = p.clip_asset_path(clip, extension)
            if source != target and not target.exists():
                source.replace(target)
            if target.is_file() and clip.get(key) != target.name:
                clip[key] = target.name
                changed = True
    return changed


def create(url: str, video_id: str, settings: dict, upload_id: str | None = None, title: str = "",
           reuse_from: str | None = None, export_dir: str | None = None) -> Project:
    p = Project(id=uuid.uuid4().hex[:12], url=url, video_id=video_id, settings=settings, upload_id=upload_id,
                title=title, reuse_from=reuse_from, export_dir=export_dir,
                export_status="pending" if export_dir else None)
    p.dir.mkdir(parents=True, exist_ok=True)
    p.save()
    return p


def reclip(base: Project, settings: dict, export_dir: str | None = None) -> Project:
    """A new project on the same video as `base`, reusing its transcript."""
    return create(base.url, base.video_id, settings, base.upload_id, base.title, reuse_from=base.id,
                  export_dir=export_dir)


def used_ranges(source_key: str, skip: str | None = None) -> list[tuple[float, float]]:
    """Every stretch of this video already picked as a clip, in any project."""
    return sorted(
        (float(c["start"]), float(c["end"]))
        for p in all_projects()
        if p.source_key == source_key and p.id != skip
        for c in p.clips
        if "start" in c and "end" in c
        and (p.status != "cancelled" or c.get("status") == "done")
    )


def sources() -> list[dict]:
    """
    Every video clipped before whose transcript is still on disk, newest first: what the
    "local project" picker offers for clipping again.
    """
    by_key: dict[str, list[Project]] = {}
    for p in all_projects():
        if p.transcript_origin in ("hyperframes-demo", "ffmpeg-demo"):
            continue
        if (p.dir / "words.json").exists() and (not p.upload_id or p.local_video):
            by_key.setdefault(p.source_key, []).append(p)
    out = []
    for key, group in by_key.items():
        group.sort(key=lambda p: p.created, reverse=True)
        latest = group[0]
        out.append({
            "key": key,
            "project": latest.id,
            "kind": "upload" if latest.upload_id else "youtube",
            "title": latest.title,
            "url": latest.url,
            "video_id": latest.video_id,
            "upload_id": latest.upload_id,
            "duration": latest.duration,
            "thumbnail": latest.public()["thumbnail"],
            "projects": len(group),
            "clips_used": len(used_ranges(key)),
            "last_used": latest.created,
        })
    # Uploaded videos never turned into a project yet: listed too, they just start from scratch.
    for meta in uploads.all_meta():
        if f"upload:{meta['id']}" in by_key:
            continue
        out.append({
            "key": f"upload:{meta['id']}",
            "project": None,
            "kind": "upload",
            "title": meta["name"].rsplit(".", 1)[0],
            "url": "",
            "video_id": "",
            "upload_id": meta["id"],
            "duration": meta.get("duration", 0),
            "thumbnail": f"/api/uploads/{meta['id']}/poster",
            "projects": 0,
            "clips_used": 0,
            "last_used": meta["created"],
        })
    return sorted(out, key=lambda s: s["last_used"], reverse=True)


def enqueue(p: Project) -> None:
    with _lock:
        _projects[p.id] = p
        _queue.put(p.id)


def restart_unstarted(p: Project) -> Project:
    """Requeue an unstarted stopped/failed project without losing its inputs."""
    with _lock:
        current = _projects.get(p.id)
        if (current is not p or current.status not in ("cancelled", "error")
                or current.progress != 0 or current.clips
                or (_active is not None and _active[0] == p.id)):
            raise ValueError("Proyek ini tidak dapat dimulai ulang dari awal")
        queued = replace(current, status="queued", stage="queued", error=None)
        queued.save()
        _projects[queued.id] = queued
        _queue.put(queued.id)
        return queued


def get(project_id: str) -> Project | None:
    with _lock:
        p = _projects.get(project_id)
        if p and p.status != "cancelled" and _active and _active[0] == project_id:
            p = _checkpoint(project_id) or p
            _projects[project_id] = p
        return p


def all_projects() -> list[Project]:
    with _lock:
        if _active:
            get(_active[0])
        return sorted(_projects.values(), key=lambda p: p.created, reverse=True)


# ---------- pipeline ----------

def update_busy() -> bool:
    """Prevent an installer from stopping a running or queued project."""
    with _lock:
        return _active is not None or any(p.status not in TERMINAL for p in _projects.values())


def _stage(p: Project, status: str, stage: str | None = None) -> None:
    p.status = status
    p.stage = stage or status
    p.progress = STAGES[status][0]
    p.save()


def _span(p: Project, lo: float, hi: float):
    last_saved = 0.0

    def report(x: float) -> None:
        nonlocal last_saved
        p.progress = lo + (hi - lo) * x
        now = time.monotonic()
        if now - last_saved >= 0.5:
            p.save()
            last_saved = now

    return report


def _clip_options(p: Project):
    clip_cfg = p.settings["clip"]
    width, height = media.RATIOS[clip_cfg["ratio"]]
    brand = p.settings.get("brand") or {}
    # One branding image (logo and/or text, at its final size and opacity) for every clip.
    mark = thumbs.build_brand(
        p.dir / "brand.png", width, height,
        p.dir / p.logo if p.logo else None,
        brand.get("text") or "", brand.get("style") or "outline", int(brand.get("opacity") or 100),
    )
    return {
        "width": width,
        "height": height,
        "fit": clip_cfg["fit"],
        "clip_ratio": clip_cfg.get("clip_ratio"),
        "background": clip_cfg.get("background") or "black",
        "style": CaptionStyle(**p.settings["caption"]),
        "thumb_cfg": p.settings["thumb"],
        "dubbing_speed": clip_cfg.get("dubbing_speed", 1.0),
        "brand": mark,
        "brand_position": brand.get("position") or "bc",
    }


def _load_words(p: Project) -> list[Word]:
    return [Word(**w) for w in json.loads((p.dir / "words.json").read_text(encoding="utf-8"))]


def _save_words(p: Project, words: list[Word]) -> None:
    tmp = p.dir / "words.json.tmp"
    tmp.write_text(json.dumps([asdict(w) for w in words], ensure_ascii=False), encoding="utf-8")
    tmp.replace(p.dir / "words.json")


def _run(p: Project) -> None:
    if p.music_url and not p.music:
        from . import music
        _stage(p, "downloading", "mengambil musik")
        p.music = music.download(p.music_url, p.dir).name
        p.save()
    clip_cfg = p.settings["clip"]
    opts = _clip_options(p)

    local = p.local_video
    base = get(p.reuse_from) if p.reuse_from else None
    if p.subtitle:
        from . import subtitles
        _stage(p, "downloading", "reading source and subtitle")
        if base:
            p.title, p.duration = p.title or base.title, base.duration
            if (base.dir / "poster.jpg").exists():
                shutil.copy(base.dir / "poster.jpg", p.dir / "poster.jpg")
        elif p.upload_id:
            if not local:
                raise FileNotFoundError("the uploaded video is gone; upload it again")
            p.duration = media.probe_duration(local)
            media.poster(local, p.dir / "poster.jpg")
        else:
            info = media.video_info(p.url)
            p.title, p.duration = info.get("title", ""), float(info.get("duration") or 0)
        if not 0 < p.duration <= 4 * 3600:
            raise ValueError("Durasi video tidak valid atau lebih dari 4 jam")
        _stage(p, "transcribing", "using uploaded subtitle")
        words = subtitles.parse_srt((p.dir / "source.srt").read_bytes())
        if max(w.end for w in words) > p.duration + 0.5:
            raise ValueError("Waktu subtitle melewati durasi video; pastikan SRT sesuai video")
        p.language = None
        p.transcript_origin = "srt"
        p.transcription_backend = None
        p.transcript_note = 'Subtitle yang diunggah pengguna.'
        _save_words(p, words)
    elif (base and p.upload_id and (base.dir / "words.json").exists()
          and not (p.settings.get("mode", "clipping") == "clipping"
                   and (base.transcript_origin == "srt" or base.subtitle))):
        # Clipping a video done before: its transcript is already here, so skip straight on.
        _stage(p, "downloading", "reusing transcript")
        tmp = p.dir / "words.json.tmp"
        shutil.copyfile(base.dir / "words.json", tmp)
        tmp.replace(p.dir / "words.json")
        if (base.dir / "poster.jpg").exists():
            shutil.copy(base.dir / "poster.jpg", p.dir / "poster.jpg")
        p.title, p.duration, p.language = p.title or base.title, base.duration, base.language
        p.transcript_origin = base.transcript_origin or ("srt" if base.subtitle else "whisper")
        p.transcript_note = getattr(base, 'transcript_note', None)
        p.transcript_timing = getattr(base, 'transcript_timing', None)
        p.transcription_backend = getattr(base, 'transcription_backend', None)
        p.transcription_note = getattr(base, 'transcription_note', None)
        words = _load_words(p)
    else:
        words = None
        if p.upload_id:
            # A video from the user's computer: nothing to download, just read its sound.
            if not local:
                raise FileNotFoundError("the uploaded video is gone; upload it again")
            _stage(p, "downloading", "reading video")
            p.duration = media.probe_duration(local)
            media.poster(local, p.dir / "poster.jpg")
            audio = media.extract_audio(local, p.dir / "audio.wav")
        else:
            _stage(p, "downloading", "memeriksa subtitle YouTube")
            info = media.video_info(p.url)
            p.title, p.duration = info.get("title", ""), float(info.get("duration") or 0)
            if not 0 < p.duration <= 4 * 3600:
                raise ValueError("Durasi video tidak valid atau lebih dari 4 jam")
            from . import youtube_transcript
            words, origin, p.language, reason, precision = youtube_transcript.fetch(info)
            if words:
                p.transcript_origin = origin
                p.transcript_timing = precision
                p.transcript_note = ('Timestamp per kata dari YouTube.' if precision == 'kata' else
                                     'Timestamp per segmen; ketepatan batas kata tidak dapat dipastikan.')
                p.transcription_backend = None
                p.transcription_note = None
                p.save()
            else:
                p.transcript_note = reason + ' Menggunakan transkripsi lokal.'
                p.save()
                _stage(p, "downloading", "mengunduh audio untuk transkripsi lokal")
                audio_src = media.download_audio(p.url, p.dir, _span(p, *STAGES["downloading"]))
                audio = media.extract_audio(audio_src, p.dir / "audio.wav")
                audio_src.unlink(missing_ok=True)
        if p.duration > 4 * 3600:
            raise ValueError("videos over 4 hours aren't supported")

        if words is None:
            _stage(p, "transcribing", "menyiapkan transkripsi lokal")
            # Imported here so the API can start while model libraries load.
            from . import transcribe
            def status(backend, reason):
                p.transcription_backend, p.transcription_note = backend, reason or None
                p.stage = f"transkripsi · {backend}"
                p.save()
            words, p.language = transcribe.transcribe(str(audio), None, _span(p, *STAGES["transcribing"]), on_status=status)
            p.transcript_origin = "whisper"
            audio.unlink(missing_ok=True)
        _save_words(p, words)

    _stage(p, "selecting", "picking new clips" if p.reuse_from else "picking clips")
    if p.settings.get("mode") == "filming":
        from . import film
        cfg = p.settings["film"]
        _stage(p, "selecting", "planning story episodes from transcript")
        episodes = film.plan(words, p.duration, cfg, speed=opts["dubbing_speed"])
        if getattr(p, 'transcript_timing', None) == 'segmen' and len(episodes) > 1:
            lengths = [sum(float(shot['end']) - float(shot['start']) for shot in ep['shots']) for ep in episodes]
            if max(lengths) - min(lengths) > 3:
                p.transcript_note = (p.transcript_note or '') + ' Durasi antarbagiannya berbeda lebih dari 3 detik.'
                p.save()
        p.clips = [{"n": i + 1, "start": ep["shots"][0]["start"], "end": ep["shots"][-1]["end"],
                    "title": str(ep.get("title", f"Episode {i+1}"))[:80],
                    "reason": str(ep.get("reason", ""))[:300], "narration": str(ep["narration"]),
                    "shots": ep["shots"], "status": "pending"} for i, ep in enumerate(episodes)]
        _stage(p, "clipping", f"episode 1/{len(p.clips)}")
        for i, clip in enumerate(p.clips):
            p.stage = f"episode {i+1}/{len(p.clips)}"
            clip["status"] = "rendering"
            p.save()
            try:
                film.render(p, clip, opts, cfg)
            except Exception as e:
                clip["status"], clip["error"] = "error", str(e) or e.__class__.__name__
                p.save()
                raise
            clip["status"] = "done"
            p.progress = 0.45 + 0.55 * (i + 1) / len(p.clips)
            p.save()
        p.status, p.stage, p.progress = "done", "done", 1.0
        _export_results(p)
        p.save()
        return
    # Never hand back a moment this video has already been clipped at.
    def uncertain_boundary():
        p.transcript_note = 'Batas clip diperkirakan karena timestamp subtitle tidak cukup rinci.'
        p.save()
    picks = select.pick_clips(words, int(clip_cfg["count"]), float(clip_cfg["max_duration"]), p.language,
                              exclude=used_ranges(p.source_key, skip=p.id), mood=p.settings.get("mood", "auto"),
                              allow_approximate=getattr(p, 'transcript_timing', None) == 'segmen',
                              on_approximate=uncertain_boundary)
    if getattr(p, 'transcript_timing', None) == 'segmen' and len(picks) > 1:
        lengths = [item.end - item.start for item in picks]
        if max(lengths) - min(lengths) > 3:
            p.transcript_note = (p.transcript_note or '') + ' Durasi antarclip berbeda lebih dari 3 detik.'
            p.save()
    p.clips = [
        {"n": i + 1, "start": c.start, "end": c.end, "title": c.title, "reason": c.reason, "status": "pending"}
        for i, c in enumerate(picks)
    ]
    _stage(p, "clipping", f"clip 1/{len(p.clips)}")

    lo, hi = STAGES["clipping"]
    share = (hi - lo) / len(p.clips)
    for i, clip in enumerate(p.clips):
        p.stage = f"clip {i + 1}/{len(p.clips)}"
        _attempt_clip(p, clip, words, opts, lo + share * i, share)

    if not any(c["status"] == "done" for c in p.clips):
        raise RuntimeError(p.clips[0].get("error") or "no clip could be made")
    p.status, p.stage, p.progress = "done", "done", 1.0
    _export_results(p)
    p.save()


def _attempt_clip(p: Project, clip: dict, words: list[Word], opts: dict, base: float, share: float) -> None:
    clip.pop("error", None)
    clip.pop("note", None)
    try:
        _make_clip(p, clip, words, opts, base, share)
        clip["status"] = "done"
    except Exception as e:
        traceback.print_exc()
        clip["status"], clip["error"] = "error", str(e) or e.__class__.__name__
    p.save()


def _make_clip(p: Project, clip: dict, words: list[Word], opts: dict, base: float, share: float) -> None:
    from . import film
    n = clip["n"]
    d = p.dir
    width, height, fit = opts["width"], opts["height"], opts["fit"]
    thumb_cfg = opts["thumb_cfg"]
    for stale in ("video", "thumb", "srt", "ass"):
        clip.pop(stale, None)

    clip["status"] = "downloading"
    src = d / f"src_{n}.mp4"
    if not src.exists():
        fetched = _span(p, base, base + share * 0.25)
        if p.upload_id:
            src = media.cut_section(p.local_video, clip["start"], clip["end"], src, fetched)
        else:
            src = media.download_section(p.url, clip["start"], clip["end"], src, fetched)

    clip_ratio = opts.get("clip_ratio")
    inset = media.crop_insets(src, clip_ratio)
    has_inset = any(inset) if isinstance(inset, tuple) else bool(inset)
    if has_inset:
        clean = d / f"src_{n}_active.mp4"
        if not clean.exists():
            media.remove_letterbox(src, clean, inset, _span(p, base + share * .25, base + share * .32))
        src = clean

    local = [
        Word(w.text, w.start - clip["start"], w.end - clip["start"])
        for w in words
        if w.start >= clip["start"] - 0.05 and w.end <= clip["end"] + 0.3
    ]
    ass = p.clip_asset_path(clip, "ass")
    srt = p.clip_asset_path(clip, "srt")
    ass.write_text(captions.build_ass(local, opts["style"], width, height), encoding="utf-8")
    srt.write_text(captions.build_srt(local, opts["style"], width, height), encoding="utf-8")
    clip["srt"], clip["ass"] = srt.name, ass.name

    source_ratio = film._source_ratio(src) if clip_ratio is not None else None
    background = opts.get("background", "black")
    plan = None
    if fit == "crop" and clip_ratio not in ("none", "blackbar"):
        clip["status"] = "framing"
        try:
            frame_width, frame_height = (media.clip_frame_size(width, clip_ratio, source_ratio, output_height=height)
                                         if clip_ratio is not None and source_ratio is not None else (width, height))
            plan = framing.plan_crop(src, frame_width, frame_height, local)
        except Exception:
            # Framing is a nicety; a centre crop is still a usable short.
            traceback.print_exc()
        p.progress = base + share * 0.4

    clip["status"] = "rendering"
    voice = thumb_cfg.get("voice")
    body = d / f"body_{n}.mp4"
    # Without a dubbed intro, the title shows over the opening seconds instead.
    title = None if voice else thumbs.title_overlay(d / f"title_{n}.png", width, height, clip["title"], thumb_cfg.get("title_style", "bold"))
    media.render_clip(src, ass, body, width, height, fit, _span(p, base + share * 0.4, base + share * 0.85),
                      opts["brand"], opts["brand_position"], plan, title,
                      clip_ratio=clip_ratio, background=background, source_ratio=source_ratio)

    clip["status"] = "thumbnail"
    frame = d / f"frame_{n}.jpg"
    if thumb_cfg.get("source") == "upload" and p.upload:
        thumbs.prepare(d / p.upload, frame, width, height)
    else:
        media.grab_frame(src, frame, width, height, fit, plan, clip_ratio=clip_ratio,
                         background=background, source_ratio=source_ratio)
    thumb = d / f"thumb_{n}.jpg"
    title_text = thumbs.thumbnail_text(thumb_cfg, clip, " ".join(w.text for w in local))
    thumbs.add_title(frame, thumb, clip["title"], "none" if thumb_cfg.get("text_mode") == "none" else thumb_cfg.get("title_style", "bold"), custom_text=title_text,
                     number=n, sequence=thumb_cfg.get("sequence") or ("number" if thumb_cfg.get("numbered") else "off"),
                     position=thumb_cfg.get("position"))
    frame.unlink(missing_ok=True)
    if opts["brand"]:
        thumbs.add_brand(thumb, opts["brand"], opts["brand_position"], media.brand_margin(width, height))
    clip["thumb"] = thumb.name

    out = p.clip_asset_path(clip, "mp4")
    if voice:
        clip["status"] = "dubbing"
        try:
            speech = tts.speak(clip["title"], voice, d / f"dub_{n}.mp3", speed=opts["dubbing_speed"])
            media.add_intro(body, thumb, speech, out, width, height)
            body.unlink(missing_ok=True)
        except Exception as e:
            # A dubbing hiccup shouldn't cost the whole clip: keep it without the intro.
            traceback.print_exc()
            clip["note"] = f"no dubbed intro ({e or e.__class__.__name__})"
    if body.exists():
        body.replace(out)
    if getattr(p, "music", None):
        mixed = d / f"clip_{n}_music.mp4"
        media.mix_music(out, d / p.music, mixed, start=p.settings.get("music_start", 0), length=p.settings.get("music_length"))
        mixed.replace(out)
    clip["video"] = out.name
    p.progress = base + share


def _write_posts(p: Project) -> None:
    """Platform titles and descriptions; best effort, the editor can regenerate them."""
    done = [c for c in p.clips if c["status"] == "done"]
    if not done:
        return
    p.stage = "writing posts"
    try:
        words = json.loads((p.dir / "words.json").read_text(encoding="utf-8"))
        social.generate(p.dir, p.title, done, words)
    except Exception:
        traceback.print_exc()


def _export_results(p: Project) -> None:
    """Copy finished deliverables to the folder selected when the project was started."""
    output = p.export_output_dir
    if output is None:
        return
    p.export_status, p.export_error = "copying", None
    p.save()
    try:
        output.mkdir(parents=True, exist_ok=True)
        names = {"dashboard-text.json", "filming-sequence.mp4", "filming-sequence.json"}
        for clip in p.clips:
            if clip.get("status") == "done":
                names.update(name for name in (clip.get("video"), clip.get("thumb"), clip.get("srt"), clip.get("ass")) if name)
        for name in names:
            source = p.dir / name
            if source.is_file():
                shutil.copy2(source, output / source.name)
        p.export_status = "done"
        p.save()
        shutil.copy2(p.dir / "project.json", output / "project.json")
    except OSError as exc:
        p.export_status, p.export_error = "error", str(exc)


def sequence_parts(p: Project) -> list[tuple[Path, float]]:
    """Return finished clips in display order, excluding thumbnail intros."""
    if p.status not in TERMINAL:
        raise ValueError("Tunggu hingga proses proyek selesai")
    parts = []
    for clip in p.clips:
        if clip.get("status") != "done":
            continue
        n = clip["n"]
        name = clip.get("video")
        if not isinstance(name, str) or Path(name).name != name:
            raise ValueError(f"Video klip {n} tidak tersedia")
        video = p.dir / name
        if not video.is_file():
            raise ValueError(f"Video klip {n} tidak tersedia")
        body = p.dir / f"body_{n}.mp4"
        if body.is_file():
            parts.append((body, 0.0))
        elif p.settings.get("mode") != "filming" and p.settings.get("thumb", {}).get("voice") and not clip.get("note", "").startswith("no dubbed intro"):
            # Older renders kept only the final video and the generated speech.
            dub = p.dir / f"dub_{n}.mp3"
            if not dub.is_file():
                raise ValueError(f"Intro thumbnail klip {n} tidak dapat dihapus: audio dubbing tidak tersedia")
            parts.append((video, float(f"{media.probe_duration(dub) + 0.4:.2f}")))
        else:
            parts.append((video, 0.0))
    if not parts:
        raise ValueError("Tidak ada klip yang selesai")
    return parts


def ensure_posts(p: Project) -> dict:
    """Saved posts, generating them first if this project has none yet (or is missing clips)."""
    data = social.load(p.dir) or {}
    done = [c for c in p.clips if c["status"] == "done"]
    if done and any(str(c["n"]) not in data for c in done):
        words = json.loads((p.dir / "words.json").read_text(encoding="utf-8"))
        data = {**data, **social.generate(p.dir, p.title, done, words)}
        social.save(p.dir, data)
    return data


def retry(p: Project, n: int) -> None:
    with _lock:
        if _projects.get(p.id) is not p or p.status not in ("done", "error") or (_active and _active[0] == p.id):
            raise ValueError("this project can't retry clips right now")
        clip = next(c for c in p.clips if c["n"] == n)
        clip["status"] = "pending"
        clip.pop("error", None)
        p.status, p.stage, p.progress, p.error = "clipping", f"retry clip {n}", STAGES["clipping"][0], None
        p.save()
        _queue.put(("retry", p.id, n))


def _retry(p: Project, n: int) -> None:
    if p.settings.get("mode") == "filming":
        from . import film
        opts = _clip_options(p)
        cfg = p.settings["film"]
        for clip in p.clips:
            if clip["n"] != n and clip.get("status") == "done":
                continue
            clip["status"] = "rendering"
            clip.pop("error", None)
            p.stage = f"episode {clip['n']}/{len(p.clips)}"
            p.save()
            try:
                film.render(p, clip, opts, cfg)
            except Exception as e:
                clip["status"], clip["error"] = "error", str(e) or e.__class__.__name__
                p.save()
                raise
            clip["status"] = "done"
            p.progress = 0.45 + 0.55 * sum(c.get("status") == "done" for c in p.clips) / len(p.clips)
            p.save()
        p.status, p.stage, p.progress = "done", "done", 1.0
        _export_results(p)
        p.save()
        return
    clip = next(c for c in p.clips if c["n"] == n)
    lo, hi = STAGES["clipping"]
    _attempt_clip(p, clip, _load_words(p), _clip_options(p), lo, hi - lo)
    if any(c.get("status") == "done" for c in p.clips):
        p.status, p.stage, p.progress, p.error = "done", "done", 1.0, None
        _export_results(p)
    else:
        p.status, p.stage, p.error = "error", "failed", clip.get("error") or "Klip gagal diproses"
    p.save()


def _fail(p: Project, error: str) -> None:
    p.status, p.stage, p.error = "error", "failed", error
    p.save()


def _start_project(pid: str, args: list) -> subprocess.Popen:
    # Frozen desktop executables dispatch the worker through their own entry point.
    command = ([sys.executable, "--project-worker"] if getattr(sys, "frozen", False)
               else [sys.executable, "-m", "clipy.project_worker"])
    return subprocess.Popen(
        [*command, str(DATA_DIR), pid, *map(str, args)],
        cwd=Path(__file__).resolve().parent.parent,
        # The desktop parent monitors stdin for launcher exit. Inheriting its
        # blocked pipe can hang Python startup on Windows before the job runs.
        stdin=subprocess.DEVNULL,
        start_new_session=os.name != "nt",
    )


def _worker() -> None:
    global _active
    while (task := _queue.get()) is not None:
        _, pid, *args = task if isinstance(task, tuple) else ("run", task)
        with _lock:
            if _shutting_down:
                return
            p = _projects.get(pid)
            if not p or p.status in TERMINAL:
                continue
            try:
                proc = _start_project(pid, args)
            except OSError as e:
                traceback.print_exc()
                _fail(p, str(e))
                continue
            _active = (pid, proc)
        code = proc.wait()
        with _lock:
            _active = None
            p = _projects.get(pid)
            if p and p.status != "cancelled":
                p = _checkpoint(pid) or p
                if p.status in TERMINAL and migrate_clip_assets(p):
                    p.save()
                _projects[pid] = p
                if p.status not in TERMINAL:
                    _fail(p, f"Project worker exited before completing the project (status {code})")


def start_worker() -> None:
    global _scheduler, _shutting_down
    with _lock:
        _shutting_down = False
        _projects.clear()
        _load(interrupted=True)
        _scheduler = threading.Thread(target=_worker, daemon=True, name="clipy-worker")
        _scheduler.start()


def shutdown_worker() -> None:
    global _queue, _scheduler, _shutting_down
    with _lock:
        _shutting_down = True
        if _active:
            _terminate(_active[1])
        _queue.put(None)
    if _scheduler:
        _scheduler.join()
    _scheduler = None
    _queue = queue.Queue()
