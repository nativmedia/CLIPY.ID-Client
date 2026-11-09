"""
Videos uploaded from the user's computer, kept in data/uploads/<id>/ until a project uses them.
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

from . import media

DIR = Path(__file__).resolve().parent.parent / "data" / "uploads"
EXTENSIONS = {"mp4", "mov", "mkv", "webm", "m4v", "avi"}


def new(filename: str) -> tuple[str, Path]:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in EXTENSIONS:
        raise ValueError(f"unsupported video type .{ext or '?'} (use {', '.join(sorted(EXTENSIONS))})")
    upload_id = uuid.uuid4().hex[:12]
    folder = DIR / upload_id
    folder.mkdir(parents=True, exist_ok=True)
    return upload_id, folder / f"video.{ext}"


def finish(upload_id: str, path: Path, filename: str) -> dict:
    if not media.has_video(path):
        path.unlink(missing_ok=True)
        raise ValueError("that file has no video track")
    meta = {"id": upload_id, "name": filename, "file": path.name, "duration": media.probe_duration(path)}
    (path.parent / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return meta


def register_local(path: Path) -> dict:
    """Register an explicitly selected local file without copying or changing it."""
    path = path.resolve(strict=True)
    if not path.is_file() or path.suffix.lower().lstrip(".") not in EXTENSIONS:
        raise ValueError("Pilih file video yang didukung")
    if not media.has_video(path):
        raise ValueError("File tidak memiliki video")
    duration = media.probe_duration(path)
    if not duration or duration <= 0:
        raise ValueError("Durasi video tidak dapat dibaca")
    upload_id = uuid.uuid4().hex[:12]
    folder = DIR / upload_id
    folder.mkdir(parents=True, exist_ok=False)
    # This metadata stays on the server and is never returned through the API.
    meta = {"id": upload_id, "name": path.name, "external_path": str(path), "duration": duration}
    (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return {"id": upload_id, "name": path.name, "duration": duration}


def all_meta() -> list[dict]:
    """Every upload still on disk, with its metadata."""
    out = []
    for meta_file in DIR.glob("*/meta.json"):
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        video = path_of(meta.get("id", ""))
        if video:
            meta["created"] = meta_file.stat().st_mtime
            meta.pop("external_path", None)
            out.append(meta)
    return out


def poster_of(upload_id: str) -> Path | None:
    """A still for the upload, made on first request."""
    video = path_of(upload_id)
    if not video:
        return None
    poster = DIR / upload_id / "poster.jpg"
    if not poster.exists():
        media.poster(video, poster)
    return poster


def path_of(upload_id: str) -> Path | None:
    if not re.fullmatch(r"[0-9a-f]{12}", upload_id or ""):
        return None
    try:
        meta = json.loads((DIR / upload_id / "meta.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    if "external_path" in meta:
        path = Path(meta["external_path"])
        if not path.is_absolute() or path.suffix.lower().lstrip(".") not in EXTENSIONS:
            return None
    else:
        filename = meta.get("file", "")
        if not re.fullmatch(r"video\.[a-z0-9]+", filename):
            return None
        path = DIR / upload_id / filename
    return path if path.is_file() else None
