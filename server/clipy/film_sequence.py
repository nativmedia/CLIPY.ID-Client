"""Join completed Filming episodes without re-encoding or thumbnail intros."""
from __future__ import annotations

import json
import subprocess
import tempfile
import threading
from pathlib import Path

from . import projects

_lock = threading.Lock()
_jobs: dict[str, dict] = {}
_OUTPUT = "filming-sequence.mp4"
_METADATA = "filming-sequence.json"


def _sources(p) -> list[Path]:
    if p.settings.get("mode") != "filming":
        raise ValueError("Penggabungan ini hanya tersedia untuk pipeline Filming")
    if p.status != "done":
        raise ValueError("Selesaikan atau ulangi semua clip sebelum menggabungkan")
    clips = p.clips
    expected = (p.settings.get("film") or {}).get("episodes")
    if not isinstance(expected, int) or expected < 1 or len(clips) != expected:
        raise ValueError("Jumlah clip belum lengkap; selesaikan atau ulangi semua clip")
    if any(c.get("status") != "done" for c in clips):
        raise ValueError("Ada clip gagal atau belum selesai; selesaikan atau ulangi clip tersebut")
    if sorted(c.get("n") for c in clips if isinstance(c.get("n"), int)) != list(range(1, expected + 1)):
        raise ValueError("Urutan clip tidak lengkap; selesaikan atau ulangi semua clip")
    paths = []
    for clip in sorted(clips, key=lambda c: c["n"]):
        name = clip.get("video")
        if not isinstance(name, str) or Path(name).name != name:
            raise ValueError(f"Video clip {clip['n']} tidak tersedia; ulangi clip tersebut")
        path = p.dir / name
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Video clip {clip['n']} tidak tersedia; ulangi clip tersebut")
        paths.append(path)
    return paths


def _fingerprint(paths: list[Path]) -> tuple:
    return tuple((p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in paths)


def _recover(p) -> tuple | None:
    """Accept a saved result only if its output and every input still match."""
    try:
        meta = json.loads((p.dir / _METADATA).read_text(encoding="utf-8"))
        output = p.dir / _OUTPUT
        if not output.is_file() or output.is_symlink():
            return None
        output_stat = output.stat()
        if meta["output"] != [output_stat.st_size, output_stat.st_mtime_ns]:
            return None
        fingerprint = _fingerprint(_sources(p))
        return fingerprint if meta["sources"] == [list(item) for item in fingerprint] else None
    except (OSError, ValueError, TypeError, KeyError):
        return None


def _probe(path: Path) -> tuple[list[dict], float]:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-show_data_hash", "sha256", "-of", "json", str(path)],
            check=True, capture_output=True, text=True, timeout=30,
        )
        data = json.loads(result.stdout)
        duration = float(data["format"]["duration"])
        streams = data["streams"]
        if duration <= 0 or [s.get("codec_type") for s in streams] != ["video", "audio"]:
            raise ValueError("harus memiliki satu video dan satu audio narator")
        fields = ("codec_type", "codec_name", "codec_tag_string", "profile", "level", "width", "height", "pix_fmt",
                  "sample_aspect_ratio", "r_frame_rate", "time_base", "sample_fmt", "sample_rate", "channels",
                  "channel_layout", "extradata_hash")
        return [{key: s.get(key) for key in fields} for s in streams], duration
    except (OSError, subprocess.SubprocessError, KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Format {path.name} tidak dapat diperiksa: {exc}") from exc


def _update(project_id: str, **changes) -> None:
    with _lock:
        if project_id in _jobs:
            _jobs[project_id].update(changes)


def _run(project_id: str, paths: list[Path], fingerprint: tuple) -> None:
    try:
        specs, total = [], 0.0
        for path in paths:
            spec, duration = _probe(path)
            specs.append(spec)
            total += duration
        for index, spec in enumerate(specs[1:], 2):
            if spec != specs[0]:
                raise ValueError(f"Format video atau audio clip {index} berbeda dari clip 1, sehingga tidak aman digabung tanpa render ulang. Ulangi clip dengan pengaturan output yang sama.")
        _update(project_id, message="Menggabungkan clip tanpa render ulang")
        with tempfile.TemporaryDirectory(prefix=".filming-sequence-", dir=paths[0].parent) as folder:
            temp = Path(folder)
            manifest = temp / "clips.txt"
            manifest.write_text("".join(f"file '../{path.name}'\n" for path in paths), encoding="utf-8")
            output, error_path = temp / "result.mp4", temp / "ffmpeg.log"
            with error_path.open("w+", encoding="utf-8") as error_file:
                process = subprocess.Popen(
                    ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(manifest),
                     "-map", "0:v:0", "-map", "0:a:0", "-c", "copy", "-movflags", "+faststart",
                     "-progress", "pipe:1", "-nostats", str(output)],
                    stdout=subprocess.PIPE, stderr=error_file, text=True,
                )
                assert process.stdout is not None
                with process.stdout:
                    for line in process.stdout:
                        key, _, value = line.strip().partition("=")
                        if key == "out_time_us":
                            try:
                                progress = max(0.0, min(0.99, int(value) / 1_000_000 / total))
                                _update(project_id, progress=progress)
                            except ValueError:
                                pass
                code = process.wait()
                if code:
                    error_file.seek(0)
                    detail = error_file.read()[-700:].strip()
                    raise RuntimeError(f"FFmpeg gagal menggabungkan tanpa render ulang: {detail or 'kode ' + str(code)}")
            current = projects.get(project_id)
            if not current or _fingerprint(_sources(current)) != fingerprint:
                raise ValueError("Clip berubah saat penggabungan; ulangi proses")
            if not output.is_file() or output.stat().st_size == 0:
                raise RuntimeError("FFmpeg tidak menghasilkan video gabungan")
            final = current.dir / _OUTPUT
            output.replace(final)
            final_stat = final.stat()
            metadata = temp / "metadata.json"
            metadata.write_text(json.dumps({"sources": fingerprint, "output": [final_stat.st_size, final_stat.st_mtime_ns]}), encoding="utf-8")
            metadata.replace(current.dir / _METADATA)
        _update(project_id, status="done", progress=1.0, message="Video gabungan siap diunduh")
    except Exception as exc:
        _update(project_id, status="error", progress=0.0, message=str(exc) or "Penggabungan gagal")


def start(p) -> dict:
    paths = _sources(p)
    fingerprint = _fingerprint(paths)
    with _lock:
        if _jobs.get(p.id, {}).get("status") == "running":
            raise ValueError("Penggabungan sedang berjalan")
        _jobs[p.id] = {"status": "running", "progress": 0.0, "message": "Memeriksa format clip", "fingerprint": fingerprint}
    threading.Thread(target=_run, args=(p.id, paths, fingerprint), daemon=True).start()
    return status(p)


def status(p) -> dict:
    with _lock:
        existing = _jobs.get(p.id)
        job = dict(existing) if existing else {"status": "idle", "progress": 0.0, "message": ""}
    if existing is None:
        recovered = _recover(p)
        if recovered is not None:
            return {"status": "done", "progress": 1.0, "message": "Video gabungan siap diunduh"}
    fingerprint = job.pop("fingerprint", None)
    if job["status"] == "done":
        if fingerprint != _recover(p):
            return {"status": "idle", "progress": 0.0, "message": "Clip berubah; gabungkan kembali"}
    return job


def download(p) -> Path:
    if status(p)["status"] != "done":
        raise ValueError("Video gabungan belum siap; gabungkan semua clip terlebih dahulu")
    return p.dir / _OUTPUT
