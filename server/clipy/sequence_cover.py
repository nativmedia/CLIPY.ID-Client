"""Add an optional poster to a Filming sequence without changing its frames."""
from __future__ import annotations

import json
import subprocess
import tempfile
import threading
from pathlib import Path

from . import film_sequence, media, projects, thumbs

_VIDEO = "filming-sequence-cover.mp4"
_IMAGE = "filming-sequence-cover.jpg"
_META = "filming-sequence-cover.json"
_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def _stat(path: Path) -> list[int]:
    item = path.stat()
    return [item.st_size, item.st_mtime_ns]


def _valid(p) -> dict | None:
    try:
        original = film_sequence.download(p)
        info = json.loads((p.dir / _META).read_text(encoding="utf-8"))
        video, image = p.dir / _VIDEO, p.dir / _IMAGE
        if (video.is_symlink() or image.is_symlink() or not video.is_file() or not image.is_file()
                or info["source"] != _stat(original) or info["video"] != _stat(video)
                or info["image"] != _stat(image) or not isinstance(info["title"], str)):
            return None
        return info
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


def status(p) -> dict:
    applied = _valid(p)
    with _lock:
        job = dict(_jobs.get(p.id, {}))
    result = {"status": "done" if applied else "idle", "progress": 1.0 if applied else 0.0,
              "message": "", "has_applied": bool(applied), "title": applied["title"] if applied else ""}
    if job.get("status") in ("running", "error"):
        result.update(status=job["status"], progress=job.get("progress", 0), message=job.get("message", ""))
    return result


def _update(project_id: str, **changes):
    with _lock:
        if project_id in _jobs:
            _jobs[project_id].update(changes)


def _run(project_id: str, source: Path, signature: list[int], title: str, style: str, position: str | None):
    try:
        duration = media.probe_duration(source)
        if duration <= 0:
            raise ValueError("Durasi video gabungan tidak valid")
        with tempfile.TemporaryDirectory(prefix=".sequence-cover-", dir=source.parent) as folder:
            temporary = Path(folder)
            frame, cover, result = temporary / "frame.jpg", temporary / "cover.jpg", temporary / "result.mp4"
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{min(1.0, duration / 2):.3f}",
                            "-i", str(source), "-map", "0:v:0", "-frames:v", "1", str(frame)],
                           check=True, capture_output=True, timeout=90)
            thumbs.add_title(frame, cover, title, style if style in thumbs.TITLE_STYLES and style != "none" else "bold",
                             custom_text=title, sequence="off", position=position)
            _update(project_id, message="Menerapkan thumbnail tanpa merender ulang video")
            with (temporary / "ffmpeg.log").open("w+", encoding="utf-8") as errors:
                process = subprocess.Popen([
                    "ffmpeg", "-y", "-v", "error", "-i", str(source), "-i", str(cover),
                    "-map", "0:v:0", "-map", "0:a?", "-map", "1:v:0", "-map_metadata", "0",
                    "-c", "copy", "-disposition:v:1", "attached_pic", "-movflags", "+faststart",
                    "-progress", "pipe:1", "-nostats", str(result),
                ], stdout=subprocess.PIPE, stderr=errors, text=True)
                assert process.stdout is not None
                timed_out = threading.Event()
                def stop_if_stuck():
                    if process.poll() is None:
                        timed_out.set()
                        try:
                            process.kill()
                        except OSError:
                            pass
                watchdog = threading.Timer(600, stop_if_stuck)
                watchdog.daemon = True
                watchdog.start()
                try:
                    with process.stdout:
                        for line in process.stdout:
                            key, _, value = line.strip().partition("=")
                            if key == "out_time_us":
                                try:
                                    _update(project_id, progress=max(0.0, min(.99, int(value) / 1e6 / duration)))
                                except ValueError:
                                    pass
                    code = process.wait()
                finally:
                    watchdog.cancel()
                if timed_out.is_set():
                    raise RuntimeError("Penerapan thumbnail terlalu lama dan dihentikan. Coba lagi; video gabungan asli tetap tersedia.")
                if code:
                    errors.seek(0)
                    raise RuntimeError("FFmpeg gagal menerapkan thumbnail: " + (errors.read()[-700:].strip() or f"kode {code}"))
            current = projects.get(project_id)
            if not current or film_sequence.download(current) != source or _stat(source) != signature:
                raise ValueError("Video gabungan berubah selama proses; ulangi apply")
            if not result.is_file() or result.stat().st_size == 0:
                raise RuntimeError("Video dengan thumbnail tidak berhasil dibuat")
            final_video, final_image = current.dir / _VIDEO, current.dir / _IMAGE
            result.replace(final_video)
            cover.replace(final_image)
            metadata = temporary / "metadata.json"
            metadata.write_text(json.dumps({"source": signature, "video": _stat(final_video),
                                            "image": _stat(final_image), "title": title}, ensure_ascii=False), encoding="utf-8")
            metadata.replace(current.dir / _META)
        _update(project_id, status="done", progress=1.0, message="Thumbnail diterapkan")
    except Exception as exc:
        _update(project_id, status="error", progress=0.0, message=str(exc) or "Thumbnail gagal diterapkan")


def start(p, title: str) -> dict:
    title = title.strip()
    if not title or len(title) > 120:
        raise ValueError("Isi judul thumbnail, maksimal 120 karakter")
    source = film_sequence.download(p)
    signature = _stat(source)
    cfg = p.settings.get("thumb") or {}
    with _lock:
        if _jobs.get(p.id, {}).get("status") == "running":
            raise ValueError("Thumbnail sedang diterapkan")
        _jobs[p.id] = {"status": "running", "progress": 0.0, "message": "Menyiapkan thumbnail"}
    threading.Thread(target=_run, args=(p.id, source, signature, title, cfg.get("title_style", "bold"), cfg.get("position")), daemon=True).start()
    return status(p)


def video(p) -> Path:
    original = film_sequence.download(p)
    return p.dir / _VIDEO if _valid(p) else original


def image(p) -> Path:
    if not _valid(p):
        raise ValueError("Thumbnail belum diterapkan")
    return p.dir / _IMAGE
