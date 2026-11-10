"""
CLIPY.ID API.

    GET    /api/key                          is a key stored? (provider + masked key)
    POST   /api/key       {key}              check the key with its provider and store it
    DELETE /api/key                          forget the key
    GET    /api/options                      every choice the settings form offers
    POST   /api/uploads   form: file                 a video from this computer → {id, name, duration}
    GET    /api/uploads/{id}/video                   stream it back for the preview player
    GET    /api/sources                              videos clipped before (transcript kept), for clipping again
    POST   /api/projects  form: settings (JSON), url | upload_id (+ title) | reuse (earlier project id),
                          image? (custom thumbnail), logo? (branding)
    GET    /api/projects                     all projects, newest first
    GET    /api/projects/{id}                one project
    GET    /api/projects/{id}/files/{name}   generated clip assets
    POST   /api/projects/{id}/clips/{n}/retry
    GET    /api/projects/{id}/posts          titles/descriptions per clip and platform
    PUT    /api/projects/{id}/posts  {posts} save edits

Run:  .venv/Scripts/python -m uvicorn app:app --port 4748
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import mimetypes
from subprocess import TimeoutExpired
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Request
from fastapi.responses import FileResponse, Response
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, ValidationError, field_validator

from clipy import captions, dashboard_text, film_sequence, keys, media, projects, sequence_cover, social, thumbs, tts, uploads

@asynccontextmanager
async def lifespan(app: FastAPI):
    projects.start_worker()
    try:
        yield
    finally:
        projects.shutdown_worker()


app = FastAPI(title="CLIPY.ID", lifespan=lifespan)

COUNTS = list(range(1, 11))
DURATIONS = [15, 30, 45, 60, 90, 120]


class ClipSettings(BaseModel):
    ratio: str = Field(pattern="^(" + "|".join(re.escape(r) for r in media.RATIOS) + ")$")
    fit: str = Field(pattern="^(crop|fit)$")
    # New clips fill the chosen output ratio through the stable subject-framing pipeline.
    # Already-saved projects can still omit this field and retain their old behavior.
    clip_ratio: str = Field(default="auto", pattern=r"^(auto|none|blackbar|1:1|3:4|4:5|5:4|9:16|16:9)$")
    background: str = Field(default="black", pattern=r"^(blur|black|white)$")
    count: int = Field(ge=1, le=10)
    max_duration: int = Field(ge=10, le=180)
    dubbing_speed: float = Field(default=1.0, ge=1, le=2)


class CaptionSettings(BaseModel):
    text_style: str = Field(default="plain", pattern="^(outline|plain|shadow|box)$")
    font: str
    animation: str
    position: int | None = Field(default=None, ge=1, le=7)
    display: str | None = Field(default=None, pattern="^(word|sentence)$")
    max_lines: int = Field(default=2, ge=1, le=3)
    color: str = captions.DEFAULT_COLOR
    case: str


class ThumbSettings(BaseModel):
    source: str = Field(pattern="^(auto|upload)$")
    title_style: str
    text: str = Field(default="", max_length=120)
    text_mode: str | None = Field(default=None, pattern="^(auto|manual|none)$")
    numbered: bool = False
    sequence: str | None = Field(default=None, pattern="^(off|number|letter|episode|part|clip)$")
    position: str | None = Field(default=None, pattern="^[tmb][lcr]$")
    voice: str | None = None


class BrandSettings(BaseModel):
    position: str = Field(default="bc", pattern="^(" + "|".join(media.LOGO_POSITIONS) + ")$")
    # Text instead of (or beside) a logo, e.g. "@yourchannel".
    text: str = Field(default="", max_length=40)
    style: str = Field(default="plain", pattern="^(" + "|".join(thumbs.BRAND_STYLES) + ")$")
    opacity: int = Field(default=60)

    @field_validator("opacity")
    @classmethod
    def known_opacity(cls, v: int) -> int:
        if v not in thumbs.BRAND_OPACITIES:
            raise ValueError(f"opacity must be one of {thumbs.BRAND_OPACITIES}")
        return v


class FilmSettings(BaseModel):
    episodes: int = Field(default=4, ge=1, le=10)
    max_duration: int = Field(default=60, ge=10, le=180)
    story_mode: str = Field(default="standalone", pattern="^(serial|standalone)$")
    style: str = Field(default="informatif", max_length=500)
    additional_prompt: str = Field(default="", max_length=2000)
    voice: str = "id-ID-GadisNeural"
    source_type: str = Field(default="film", pattern="^(film|recap)$")
    source_ratio: str = Field(default="auto", pattern=r"^(auto|none|blackbar|1:1|3:4|4:5|5:4|9:16|16:9)$")
    background: str = Field(default="black", pattern="^(blur|black|white)$")


class Settings(BaseModel):
    mode: str = Field(default="clipping", pattern="^(clipping|filming)$")
    film: FilmSettings | None = None
    music_start: float = Field(default=0, ge=0, le=14400)
    music_length: float | None = Field(default=None, gt=0, le=180)
    mood: str = Field(default="auto", pattern="^(auto|senang|sedih|lucu|serius|seram|informatif|inspiratif|konyol)$")
    clip: ClipSettings
    caption: CaptionSettings
    thumb: ThumbSettings
    brand: BrandSettings = BrandSettings()


IMAGE_TYPES = ("jpg", "jpeg", "png", "webp")


async def save_image(p, upload: UploadFile, stem: str) -> str:
    ext = (upload.filename or "").rsplit(".", 1)[-1].lower()
    if ext not in IMAGE_TYPES:
        raise HTTPException(400, "images must be JPG, PNG or WebP")
    name = f"{stem}.{ext}"
    (p.dir / name).write_bytes(await upload.read())
    return name


def youtube_id(raw: str) -> str | None:
    """Same rules as the web app: watch, youtu.be, shorts, embed and live links."""
    url = urlparse(raw if re.match(r"^https?://", raw, re.I) else f"https://{raw}")
    host = re.sub(r"^(www|m|music)\.", "", url.hostname or "")
    vid = None
    if host == "youtu.be":
        vid = url.path.lstrip("/").split("/")[0]
    elif host in ("youtube.com", "youtube-nocookie.com"):
        vid = parse_qs(url.query).get("v", [None])[0]
        if not vid:
            m = re.match(r"^/(?:shorts|embed|live|v)/([^/?#]+)", url.path)
            vid = m.group(1) if m else None
    return vid if vid and re.fullmatch(r"[\w-]{11}", vid) else None


def require_local_browser(request: Request) -> None:
    """Native Windows dialogs and output folders are only safe from the local UI."""
    origin = request.headers.get("origin", "")
    if request.client is None or request.client.host not in ("127.0.0.1", "::1"):
        raise HTTPException(403, "Hanya tersedia di komputer server")
    desktop_origin = os.environ.get("CLIPY_DESKTOP_ORIGIN", "")
    allowed_origins = {"http://127.0.0.1:4747", "http://localhost:4747", "http://127.0.0.1:4757", "http://localhost:4757"}
    if origin not in allowed_origins and not (desktop_origin and origin == desktop_origin):
        raise HTTPException(403, "Hanya tersedia dari halaman lokal")
    allowed_hosts = {"127.0.0.1:4747", "localhost:4747", "127.0.0.1:4748", "localhost:4748", "127.0.0.1:4757", "localhost:4757", "127.0.0.1:4758", "localhost:4758", desktop_origin.removeprefix("http://")}
    if request.headers.get("host", "") not in allowed_hosts:
        raise HTTPException(403, "Host tidak diizinkan")


# ---------- key ----------

class KeyRequest(BaseModel):
    key: str


@app.get("/api/key")
def get_key():
    return keys.public(keys.load())


@app.post("/api/key")
def set_key(req: KeyRequest):
    try:
        return keys.public(keys.save(req.key))
    except keys.KeyProblem as e:
        raise HTTPException(400, str(e))


@app.delete("/api/key")
def delete_key():
    keys.clear()
    return keys.public(None)


# ---------- options ----------

@app.get("/api/options")
def options():
    return {
        "ratios": list(media.RATIOS),
        "fits": media.FITS,
        "counts": COUNTS,
        "durations": DURATIONS,
        **captions.OPTIONS,
        "title_styles": thumbs.TITLE_STYLES,
        "voices": tts.VOICES,
        "logo_positions": media.LOGO_POSITIONS,
        "brand_styles": thumbs.BRAND_STYLES,
        "brand_opacities": thumbs.BRAND_OPACITIES,
        "platforms": social.PLATFORMS,
    }


# ---------- uploads ----------

@app.post("/api/local-files/pick")
def pick_local_video(request: Request):
    """Open the server desktop picker only from the local CLIPY web page."""
    require_local_browser(request)
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        selected = filedialog.askopenfilename(title="Pilih video di komputer server", filetypes=[("Video", "*.mp4 *.mov *.mkv *.webm *.m4v *.avi")], parent=root)
    finally:
        root.destroy()
    if not selected:
        return Response(status_code=204)
    try:
        return uploads.register_local(Path(selected))
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/export-directory/choose")
def choose_export_directory(request: Request):
    """Open Windows' folder picker and return a directory chosen by the local user."""
    require_local_browser(request)
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        selected = filedialog.askdirectory(title="Simpan hasil CLIPY ke folder", mustexist=True, parent=root)
    finally:
        root.destroy()
    if not selected:
        return Response(status_code=204)
    path = Path(selected).resolve()
    if not path.is_dir():
        raise HTTPException(400, "Folder tujuan tidak dapat digunakan")
    return {"path": str(path)}

@app.post("/api/uploads")
async def upload_video(file: UploadFile = File(...)):
    """A video from the user's computer, streamed to disk; a project can then use its id."""
    try:
        upload_id, path = uploads.new(file.filename or "")
    except ValueError as e:
        raise HTTPException(400, str(e))
    with path.open("wb") as out:
        while chunk := await file.read(1 << 20):
            out.write(chunk)
    try:
        return uploads.finish(upload_id, path, file.filename or path.name)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/uploads/{upload_id}/video")
def uploaded_video(upload_id: str):
    """Stream an uploaded video back, for the preview player."""
    path = uploads.path_of(upload_id)
    if not path:
        raise HTTPException(404, "no such upload")
    return FileResponse(path)


@app.get("/api/uploads/{upload_id}/poster")
def uploaded_poster(upload_id: str):
    poster = uploads.poster_of(upload_id)
    if not poster:
        raise HTTPException(404, "no such upload")
    return FileResponse(poster, media_type="image/jpeg")


# ---------- sources (videos clipped before) ----------

@app.get("/api/sources")
def list_sources():
    """Videos already clipped whose transcript is kept, for clipping again with new moments."""
    return projects.sources()


# ---------- projects ----------

@app.post("/api/music/preview")
async def preview_music(url: str = Form(...)):
    """Download a bounded public music source and return audio for local preview."""
    from clipy import music as music_source
    url = url.strip()
    try:
        music_source.validate_url(url)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    directory = Path(tempfile.mkdtemp(prefix="clipy-music-preview-"))
    try:
        path = await run_in_threadpool(music_source.download, url, directory)
    except Exception as e:
        shutil.rmtree(directory, ignore_errors=True)
        raise HTTPException(400, f"Tidak dapat mengambil musik: {e}") from e
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type,
                        background=BackgroundTask(shutil.rmtree, directory, ignore_errors=True))


@app.post("/api/projects")
async def create_project(
    request: Request,
    settings: str = Form(...),
    url: str = Form(""),
    upload_id: str = Form(""),
    title: str = Form(""),
    reuse: str = Form(""),
    image: UploadFile | None = File(None),
    logo: UploadFile | None = File(None),
    subtitle: UploadFile | None = File(None),
    music: UploadFile | None = File(None),
    music_url: str = Form(""),
    export_dir: str = Form(""),
):
    vid = ""
    base = None
    if reuse:
        # Clipping a video done before: the source comes from that project.
        base = projects.get(reuse)
        if not base or not (base.dir / "words.json").exists():
            raise HTTPException(400, "that earlier project is gone; pick the video again")
        if base.upload_id and not base.local_video:
            raise HTTPException(400, "that uploaded video is gone; upload it again")
    elif upload_id:
        if not uploads.path_of(upload_id):
            raise HTTPException(400, "that upload is missing; choose the video again")
    else:
        vid = youtube_id(url.strip()) or ""
        if not vid:
            raise HTTPException(400, "not a YouTube link")
    if not keys.load():
        raise HTTPException(400, "insert an API key first")
    try:
        s = Settings.model_validate(json.loads(settings))
    except (json.JSONDecodeError, ValidationError) as e:
        raise HTTPException(400, f"bad settings: {e}")
    c, t = s.caption, s.thumb
    if s.mode == "filming" and (not s.film or s.film.voice not in tts.VOICES):
        raise HTTPException(400, "choose valid filming settings and a narrator voice")
    if c.font not in captions.FONTS or (c.animation not in captions.ANIMATIONS and c.animation != "word") or c.color not in captions.COLORS or c.case not in captions.CASES:
        raise HTTPException(400, "unknown caption option")
    if t.title_style not in thumbs.TITLE_STYLES or (t.voice and t.voice not in tts.VOICES):
        raise HTTPException(400, "unknown thumbnail option")
    if t.source == "upload" and not image:
        raise HTTPException(400, "choose an image to upload")

    export_path = None
    if export_dir.strip():
        require_local_browser(request)
        try:
            export_path = Path(export_dir).expanduser().resolve(strict=True)
        except OSError as exc:
            raise HTTPException(400, "Folder Save As tidak tersedia") from exc
        if not export_path.is_dir():
            raise HTTPException(400, "Folder Save As tidak valid")

    for f in (image, logo):
        if f and (f.filename or "").rsplit(".", 1)[-1].lower() not in IMAGE_TYPES:
            raise HTTPException(400, "images must be JPG, PNG or WebP")

    music_url = music_url.strip()
    if music and music_url:
        raise HTTPException(400, "Pilih tautan atau file musik")
    if music_url:
        from clipy.music import validate_url
        try:
            validate_url(music_url)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
    if music and (music.filename or "").rsplit(".", 1)[-1].lower() not in ("mp3", "wav", "m4a", "ogg", "flac", "webm"):
        raise HTTPException(400, "Musik harus berupa MP3, WAV, M4A, OGG, atau FLAC")

    subtitle_bytes = None
    if subtitle:
        from clipy import subtitles
        if s.mode != "filming":
            raise HTTPException(400, "Unggah subtitle hanya tersedia untuk mode filming")
        if not (subtitle.filename or "").lower().endswith(".srt"):
            raise HTTPException(400, "Subtitle harus berupa file .srt")
        subtitle_bytes = await subtitle.read(subtitles.MAX_BYTES + 1)
        try:
            subtitles.parse_srt(subtitle_bytes)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    if base:
        p = projects.reclip(base, s.model_dump(), export_dir=str(export_path) if export_path else None)
    elif upload_id:
        p = projects.create("", "", s.model_dump(), upload_id=upload_id, title=title.strip()[:200] or "uploaded video",
                            export_dir=str(export_path) if export_path else None)
    else:
        p = projects.create(f"https://www.youtube.com/watch?v={vid}", vid, s.model_dump(),
                            export_dir=str(export_path) if export_path else None)
    if t.source == "upload" and image:
        p.upload = await save_image(p, image, "upload")
    if logo:
        p.logo = await save_image(p, logo, "logo")
    if music:
        ext = (music.filename or "").rsplit(".", 1)[-1].lower()
        path = p.dir / f"background_music.{ext}"
        size = 0
        with path.open("wb") as target:
            while chunk := await music.read(1024 * 1024):
                size += len(chunk)
                if size > 100 * 1024 * 1024:
                    break
                target.write(chunk)
        if size > 100 * 1024 * 1024:
            path.unlink(missing_ok=True)
            raise HTTPException(400, "Ukuran musik maksimal 100 MB")
        try:
            if not size or not media.has_audio(path):
                raise ValueError("Berkas tidak memiliki audio")
        except Exception as exc:
            path.unlink(missing_ok=True)
            raise HTTPException(400, f"Berkas musik tidak dapat dibaca: {exc}") from exc
        p.music = path.name
    if music_url:
        p.music_url = music_url
    if subtitle_bytes is not None:
        (p.dir / "source.srt").write_bytes(subtitle_bytes)
        p.subtitle = "source.srt"
    p.save()
    projects.enqueue(p)
    return p.public()


@app.post("/api/projects/{project_id}/clips/{n}/retry")
def retry_clip(project_id: str, n: int):
    p = projects.get(project_id)
    if not p or not any(c["n"] == n for c in p.clips):
        raise HTTPException(404, "no such clip")
    if p.status not in ("done", "error") or not (p.dir / "words.json").exists():
        raise HTTPException(409, "this project can't retry clips right now")
    try:
        projects.retry(p, n)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return p.public()


@app.post("/api/projects/{project_id}/stop")
def stop_project(project_id: str):
    p = projects.get(project_id)
    if not p:
        raise HTTPException(404, "project not found")
    try:
        return projects.stop(p).public()
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    except (OSError, TimeoutExpired) as e:
        raise HTTPException(503, "Proses belum bisa dihentikan. Coba lagi.") from e


@app.post("/api/projects/{project_id}/restart")
def restart_project(project_id: str):
    p = projects.get(project_id)
    if not p:
        raise HTTPException(404, "project not found")
    try:
        return projects.restart_unstarted(p).public()
    except ValueError as e:
        raise HTTPException(409, str(e)) from e


@app.delete("/api/projects/{project_id}")
def delete_stopped_project(project_id: str):
    p = projects.get(project_id)
    if not p:
        raise HTTPException(404, "project not found")
    try:
        projects.delete_stopped(p)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True}


@app.get("/api/projects/{project_id}/posts")
def get_posts(project_id: str):
    """Titles and descriptions per clip and platform, written on first request if missing."""
    p = projects.get(project_id)
    if not p:
        raise HTTPException(404, "no such project")
    try:
        return projects.ensure_posts(p)
    except keys.KeyProblem as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"couldn't write the posts: {e}")


@app.get("/api/projects/{project_id}/dashboard-text")
def get_dashboard_text(project_id: str):
    p = projects.get(project_id)
    if not p:
        raise HTTPException(404, "project not found")
    try:
        include_sequence = p.settings.get("mode") == "filming" and film_sequence.status(p)["status"] == "done"
        return dashboard_text.ensure(p, include_sequence=include_sequence)
    except keys.KeyProblem as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(502, f"Deskripsi belum dapat dibuat: {e}") from e


class DashboardTextUpdate(BaseModel):
    item: str = Field(pattern=r"^(sequence|[1-9][0-9]*)$")
    field: str = Field(pattern=r"^(title|pendek|sedang|panjang)$")
    text: str = Field(max_length=600)


@app.put("/api/projects/{project_id}/dashboard-text")
def put_dashboard_text(project_id: str, req: DashboardTextUpdate):
    p = projects.get(project_id)
    if not p:
        raise HTTPException(404, "project not found")
    if req.item == "sequence" and p.settings.get("mode") != "filming":
        raise HTTPException(409, "Video gabungan hanya tersedia untuk Filming")
    try:
        return dashboard_text.update(p, req.item, req.field, req.text)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


class PostsUpdate(BaseModel):
    posts: dict[str, dict[str, dict[str, str]]]


@app.put("/api/projects/{project_id}/posts")
def put_posts(project_id: str, req: PostsUpdate):
    p = projects.get(project_id)
    if not p:
        raise HTTPException(404, "no such project")
    data = social.load(p.dir) or {}
    for n, platforms in req.posts.items():
        for platform, fields in platforms.items():
            spec = social.PLATFORMS.get(platform)
            if not spec:
                continue
            target = data.setdefault(n, {}).setdefault(platform, {})
            for name, text in fields.items():
                if name in spec["fields"]:
                    target[name] = text[: spec["fields"][name]]
    return social.save(p.dir, data)


@app.get("/api/projects")
def list_projects():
    return [p.public() for p in projects.all_projects()]


@app.get("/api/projects/{project_id}")
def get_project(project_id: str):
    p = projects.get(project_id)
    if not p:
        raise HTTPException(404, "no such project")
    return p.public()


@app.post("/api/projects/{project_id}/output/open")
def open_project_output(project_id: str, request: Request):
    """Reveal copied deliverables in Windows Explorer."""
    require_local_browser(request)
    p = projects.get(project_id)
    if not p:
        raise HTTPException(404, "project not found")
    output = p.export_output_dir if p.export_output_dir and p.export_output_dir.is_dir() else p.dir
    if not output.is_dir():
        raise HTTPException(409, "Folder hasil belum tersedia")
    if os.name != "nt":
        raise HTTPException(501, "Buka folder hanya tersedia di Windows")
    try:
        os.startfile(str(output))
    except OSError as exc:
        raise HTTPException(500, f"Tidak dapat membuka folder: {exc}") from exc
    return {"ok": True}


@app.post("/api/projects/{project_id}/clips/{clip_number}/open")
def open_clip_output(project_id: str, clip_number: int, request: Request):
    """Open Explorer at the rendered clip and select that exact MP4 file."""
    require_local_browser(request)
    p = projects.get(project_id)
    if not p:
        raise HTTPException(404, "project not found")
    clip = next((item for item in p.clips if item.get("n") == clip_number), None)
    if not clip or not isinstance(clip.get("video"), str):
        raise HTTPException(404, "Clip belum tersedia")
    name = Path(clip["video"]).name
    if name != clip["video"]:
        raise HTTPException(400, "Nama file clip tidak valid")
    export = p.export_output_dir if p.export_output_dir and p.export_output_dir.is_dir() else None
    output = export / name if export and (export / name).is_file() else p.dir / name
    if not output.is_file():
        raise HTTPException(404, "File clip tidak ditemukan")
    if os.name != "nt":
        raise HTTPException(501, "Buka folder hanya tersedia di Windows")
    try:
        # Separate arguments preserve spaces and commas in the selected filename.
        subprocess.Popen(["explorer.exe", "/select,", str(output.resolve())])
    except OSError as exc:
        raise HTTPException(500, f"Tidak dapat membuka file clip: {exc}") from exc
    return {"ok": True, "file": name}


@app.post("/api/projects/{project_id}/source/open")
def open_project_source(project_id: str, request: Request):
    """Reveal the original local video folder for uploaded projects."""
    require_local_browser(request)
    p = projects.get(project_id)
    if not p:
        raise HTTPException(404, "project not found")
    source = p.local_video
    if source is None:
        raise HTTPException(409, "Video sumber lokal tidak tersedia")
    if os.name != "nt":
        raise HTTPException(501, "Buka folder hanya tersedia di Windows")
    try:
        os.startfile(str(source.parent))
    except OSError as exc:
        raise HTTPException(500, f"Tidak dapat membuka folder sumber: {exc}") from exc
    return {"ok": True}


@app.get("/api/projects/{project_id}/files/{name}")
def project_file(project_id: str, name: str):
    p = projects.get(project_id)
    allowed = {"poster.jpg"}
    if p:
        allowed.update(
            asset for clip in p.clips for asset in (clip.get("video"), clip.get("thumb"), clip.get("srt"), clip.get("ass"))
            if isinstance(asset, str) and Path(asset).name == asset
        )
    if not p or name not in allowed or not (p.dir / name).is_file():
        raise HTTPException(404, "not found")
    media_type = {"mp4": "video/mp4", "srt": "application/x-subrip", "jpg": "image/jpeg", "ass": "text/x-ssa"}.get(name.rsplit(".", 1)[-1].lower(), "application/octet-stream")
    return FileResponse(p.dir / name, media_type=media_type, filename=name)


@app.get("/api/projects/{project_id}/clip-sequence")
def clip_sequence(project_id: str):
    p = projects.get(project_id)
    if not p:
        raise HTTPException(404, "project not found")
    directory = None
    try:
        parts = projects.sequence_parts(p)
        directory = Path(tempfile.mkdtemp(prefix="clipy-sequence-"))
        output = media.concatenate_clips(parts, directory / "clip-sequence.mp4")
        return FileResponse(output, media_type="video/mp4", filename=f"clipy-{p.id}-clip-sequence.mp4",
                            background=BackgroundTask(shutil.rmtree, directory, ignore_errors=True))
    except ValueError as e:
        if directory:
            shutil.rmtree(directory, ignore_errors=True)
        raise HTTPException(409, str(e)) from e
    except Exception as e:
        if directory:
            shutil.rmtree(directory, ignore_errors=True)
        raise HTTPException(500, f"Gagal membuat clip sequence: {e}") from e


@app.post("/api/projects/{project_id}/film-sequence")
def start_film_sequence(project_id: str):
    p = projects.get(project_id)
    if not p:
        raise HTTPException(404, "project not found")
    try:
        return film_sequence.start(p)
    except (ValueError, OSError) as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/projects/{project_id}/film-sequence")
def get_film_sequence(project_id: str):
    p = projects.get(project_id)
    if not p:
        raise HTTPException(404, "project not found")
    return film_sequence.status(p)


@app.get("/api/projects/{project_id}/film-sequence/download")
def download_film_sequence(project_id: str):
    p = projects.get(project_id)
    if not p:
        raise HTTPException(404, "project not found")
    try:
        output = sequence_cover.video(p)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return FileResponse(output, media_type="video/mp4", filename=f"clipy-{p.id}-gabungan.mp4")


@app.get("/api/projects/{project_id}/film-sequence/video")
def preview_film_sequence(project_id: str):
    p = projects.get(project_id)
    if not p:
        raise HTTPException(404, "project not found")
    try:
        output = sequence_cover.video(p)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return FileResponse(output, media_type="video/mp4", filename=f"clipy-{p.id}-gabungan.mp4",
                        content_disposition_type="inline")


class SequenceCoverRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)


@app.post("/api/projects/{project_id}/film-sequence/cover")
def apply_sequence_cover(project_id: str, req: SequenceCoverRequest):
    p = projects.get(project_id)
    if not p:
        raise HTTPException(404, "project not found")
    try:
        return sequence_cover.start(p, req.title)
    except (ValueError, OSError) as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/projects/{project_id}/film-sequence/cover")
def sequence_cover_status(project_id: str):
    p = projects.get(project_id)
    if not p:
        raise HTTPException(404, "project not found")
    return sequence_cover.status(p)


@app.get("/api/projects/{project_id}/film-sequence/cover/image")
def sequence_cover_image(project_id: str):
    p = projects.get(project_id)
    if not p:
        raise HTTPException(404, "project not found")
    try:
        image = sequence_cover.image(p)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return FileResponse(image, media_type="image/jpeg", filename=f"clipy-{p.id}-thumbnail.jpg",
                        content_disposition_type="inline")


@app.get("/api/projects/{project_id}/assets/{kind}")
def saved_project_asset(project_id: str, kind: str):
    """Original logo/thumbnail upload, for restoring an earlier project's form."""
    p = projects.get(project_id)
    name = ({"logo": p.logo, "image": p.upload, "music": p.music}.get(kind)) if p else None
    if not p or not name or Path(name).name != name or not (p.dir / name).is_file():
        raise HTTPException(404, "saved asset not found")
    content_type = {".png": "image/png", ".webp": "image/webp", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4", ".ogg": "audio/ogg", ".flac": "audio/flac", ".webm": "audio/webm"}.get(Path(name).suffix.lower())
    if not content_type:
        raise HTTPException(404, "saved asset not found")
    return FileResponse(p.dir / name, media_type=content_type)
