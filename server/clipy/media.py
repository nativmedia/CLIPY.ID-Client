"""
Getting media in and finished shorts out: yt-dlp for the YouTube audio and clip sections,
FFmpeg for the audio Whisper reads, the reframed render with captions, thumbnail frames
and the dubbed intro.
"""

from __future__ import annotations

import os
import re
import subprocess
from functools import lru_cache
from pathlib import Path

import yt_dlp
from yt_dlp.utils import download_range_func

FONTS_DIR = Path(__file__).resolve().parent.parent / "fonts"

# Output frame per aspect ratio.
RATIOS = {
    "9:16": (1080, 1920),
    "16:9": (1920, 1080),
    "1:1": (1080, 1080),
    "4:5": (1080, 1350),
    "5:4": (1350, 1080),
}
FITS = {"crop": "Crop to fill", "fit": "Fit (blurred background)"}

FPS = 30


def _hook(on_progress):
    def hook(d):
        if on_progress and d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                on_progress(min(1.0, d.get("downloaded_bytes", 0) / total))

    return hook


def video_info(url: str) -> dict:
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "noplaylist": True}) as ydl:
        return ydl.extract_info(url, download=False)


def download_audio(url: str, out_dir: Path, on_progress=None) -> Path:
    """Audio track of the whole video (much smaller than the video), for transcription."""
    opts = {
        "format": "bestaudio[ext=m4a]/bestaudio",
        "outtmpl": str(out_dir / "audio-src.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [_hook(on_progress)],
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.extract_info(url, download=True)
    found = sorted(p for p in out_dir.glob("audio-src.*") if not p.name.endswith(".part"))
    if not found:
        raise RuntimeError("audio download produced no file")
    return found[0]


def download_section(url: str, start: float, end: float, out: Path, on_progress=None) -> Path:
    """Download just [start, end] of the video as MP4."""
    opts = {
        "format": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/bv*[height<=1080]+ba/b",
        "merge_output_format": "mp4",
        "outtmpl": str(out.with_suffix(".%(ext)s")),
        "download_ranges": download_range_func(None, [(start, end)]),
        # Cut exactly where asked instead of at the nearest keyframe. That re-encodes the
        # section; a few threads keep x264's memory use modest.
        "force_keyframes_at_cuts": True,
        "external_downloader_args": {"ffmpeg_o": ["-threads", "4"]},
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [_hook(on_progress)],
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.extract_info(url, download=True)
    if not out.exists():
        found = sorted(p for p in out.parent.glob(out.stem + ".*") if p.suffix in (".mp4", ".mkv", ".webm"))
        if not found:
            raise RuntimeError("clip download produced no file")
        return found[0]
    return out


def cut_section(source: Path, start: float, end: float, out: Path, on_progress=None) -> Path:
    """Exactly [start, end] of a local video, re-encoded so it starts on a clean frame."""
    args = [
        "-ss", f"{start:.3f}", "-i", str(source), "-t", f"{end - start:.3f}",
        "-map", "0:v:0", "-map", "0:a:0?", "-pix_fmt", "yuv420p", *AUDIO,
        "-progress", "pipe:1", "-nostats", str(out),
    ]
    _run_ffmpeg(args, out.parent, end - start, on_progress)
    return out


def poster(source: Path, out: Path) -> Path:
    """A still from about a tenth of the way in, to stand for an uploaded video in the lists."""
    seek = probe_duration(source) * 0.1
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-ss", f"{seek:.2f}", "-i", str(source),
         "-frames:v", "1", "-vf", "scale=640:-2", "-q:v", "3", str(out)],
        check=True,
    )
    return out


def has_video(path: Path) -> bool:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    ).stdout
    return "video" in out


def extract_audio(source: Path, out: Path) -> Path:
    """16 kHz mono WAV: what Whisper wants, and quick to decode."""
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(source), "-vn", "-ac", "1", "-ar", "16000", str(out)],
        check=True,
    )
    return out


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return float(out or 0)


def concatenate_clips(parts: list[tuple[Path, float]], out: Path) -> Path:
    """Join clips in order, dropping any requested intro and filling missing audio with silence."""
    if not parts:
        raise ValueError("Tidak ada klip yang selesai")
    inputs, filters, streams = [], [], []
    for i, (path, start) in enumerate(parts):
        duration = probe_duration(path) - start
        if duration <= 0:
            raise ValueError(f"Durasi klip {path.name} tidak valid")
        audio = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=index",
             "-of", "csv=p=0", str(path)], check=True, capture_output=True, text=True,
        ).stdout.strip()
        inputs += ["-i", str(path)]
        filters.append(f"[{i}:v]trim=start={start:.3f},setpts=PTS-STARTPTS,fps={FPS},format=yuv420p,setsar=1[v{i}]")
        if audio:
            filters.append(f"[{i}:a]atrim=start={start:.3f},asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo,apad,atrim=duration={duration:.3f}[a{i}]")
        else:
            filters.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={duration:.3f},asetpts=PTS-STARTPTS[a{i}]")
        streams.append(f"[v{i}][a{i}]")
    filters.append("".join(streams) + f"concat=n={len(parts)}:v=1:a=1[v][a]")
    _run_ffmpeg([*inputs, "-filter_complex", ";".join(filters), "-map", "[v]", "-map", "[a]",
                 "-pix_fmt", "yuv420p", *AUDIO, "-movflags", "+faststart", str(out)], out.parent, 0)
    return out


def letterbox(path: Path) -> int:
    """Return the confirmed top/bottom inset in a 360-line analysis frame."""
    try:
        stat = path.stat()
        return _sample_letterbox(path, stat.st_mtime_ns, stat.st_size)
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0


@lru_cache(maxsize=64)
def _sample_letterbox(path: Path, mtime_ns: int, file_size: int) -> int:
    duration = probe_duration(path)
    if duration < .35:
        return 0
    fractions = (.18, .50, .82) if duration <= 6 else (.12, .30, .50, .70, .88)
    insets = []
    for fraction in fractions:
        sample = subprocess.run([
            "ffmpeg", "-hide_banner", "-nostats", "-ss", f"{duration * fraction:.3f}", "-i", str(path),
            "-map", "0:v:0", "-an", "-vf", "scale=320:360:flags=neighbor,cropdetect=limit=24:round=2:skip=0",
            "-frames:v", "4", "-f", "null", "-",
        ], capture_output=True, text=True, timeout=20, check=True)
        crops = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", sample.stderr)
        if not crops:
            continue
        width, height, x, top = map(int, crops[-1])
        bottom = 360 - height - top
        # Dark scenes and fades often have no reliable full-width measurement.
        if width != 320 or x != 0 or height < 180:
            continue
        # A reliable full-height or asymmetric frame vetoes a global crop.
        if abs(top - bottom) > 4 or not 6 <= min(top, bottom) <= 88:
            return 0
        insets.append(min(top, bottom))
    if len(insets) <= len(fractions) // 2 or max(insets) - min(insets) > 4:
        return 0
    return min(insets)


def letterbox_crop(inset: int) -> str:
    """Use even offsets on the full-resolution source before output framing."""
    offset = f"trunc(ih*{inset}/720+0.5)*2"
    return f"crop=iw:ih-2*{offset}:0:{offset}"


def black_bars(path: Path) -> tuple[int, int]:
    """Return confirmed symmetric side and top/bottom insets on a 320x360 sample."""
    try:
        stat = path.stat()
        return _sample_black_bars(path, stat.st_mtime_ns, stat.st_size)
    except (OSError, ValueError, subprocess.SubprocessError):
        return (0, 0)


def crop_insets(path: Path, mode: str | None):
    """Preserve the source for no crop; detect bars explicitly when requested."""
    if mode == "none":
        return 0
    return black_bars(path) if mode == "blackbar" else letterbox(path)


@lru_cache(maxsize=64)
def _sample_black_bars(path: Path, mtime_ns: int, file_size: int) -> tuple[int, int]:
    duration = probe_duration(path)
    if duration < .35:
        return (0, 0)
    fractions = (.18, .50, .82) if duration <= 6 else (.12, .30, .50, .70, .88)
    samples = []
    for fraction in fractions:
        sample = subprocess.run([
            "ffmpeg", "-hide_banner", "-nostats", "-ss", f"{duration * fraction:.3f}", "-i", str(path),
            "-map", "0:v:0", "-an", "-vf", "scale=320:360:flags=neighbor,cropdetect=limit=24:round=2:skip=0",
            "-frames:v", "4", "-f", "null", "-",
        ], capture_output=True, text=True, timeout=20, check=True)
        crops = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", sample.stderr)
        if not crops:
            continue
        width, height, left, top = map(int, crops[-1])
        right, bottom = 320 - width - left, 360 - height - top
        if width >= 160 and height >= 180 and min(left, right, top, bottom) >= 0:
            samples.append((left, right, top, bottom))
    if len(samples) <= len(fractions) // 2:
        return (0, 0)

    def confirmed(first: int, second: int, limit: int) -> int:
        sides = [(row[first], row[second]) for row in samples]
        if any(abs(a - b) > 4 or not 6 <= min(a, b) <= limit for a, b in sides):
            return 0
        values = [min(a, b) for a, b in sides]
        return min(values) if max(values) - min(values) <= 4 else 0

    return (confirmed(0, 1, 78), confirmed(2, 3, 88))


def blackbar_crop(insets: tuple[int, int]) -> str:
    """Crop confirmed bars using even offsets at the source resolution."""
    x, y = insets
    if not 0 <= x < 160 or not 0 <= y < 180:
        raise ValueError("Invalid black bar insets")
    left = f"trunc(iw*{x}/640+0.5)*2" if x else "0"
    top = f"trunc(ih*{y}/720+0.5)*2" if y else "0"
    return f"crop=iw-2*{left}:ih-2*{top}:{left}:{top}"


def remove_letterbox(source: Path, out: Path, inset: int | tuple[int, int], on_progress=None) -> Path:
    """Create a clean intermediate so framing and thumbnail sampling see the same image."""
    pending = out.with_suffix(".pending.mp4")
    try:
        _run_ffmpeg([
            "-i", str(source), "-vf", blackbar_crop(inset) if isinstance(inset, tuple) else letterbox_crop(inset), "-map", "0:v:0", "-map", "0:a:0?",
            "-pix_fmt", "yuv420p", "-c:a", "copy", "-progress", "pipe:1", "-nostats", str(pending),
        ], out.parent, probe_duration(source), on_progress)
        pending.replace(out)
    finally:
        pending.unlink(missing_ok=True)
    return out


NVENC = ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "21", "-b:v", "0"]
X264 = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-threads", "4"]
AUDIO = ["-c:a", "aac", "-b:a", "160k", "-ar", "48000"]


@lru_cache(maxsize=1)
def video_encoders() -> list[list[str]]:
    """Encoders to try in order: NVENC if the GPU encoder is present, then x264."""
    probe = subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.1", "-c:v", "h264_nvenc", "-f", "null", "-"],
        capture_output=True,
    )
    return [NVENC, X264] if probe.returncode == 0 else [X264]


def start_time(path: Path) -> float:
    """First video timestamp; crop expressions use the stream's own clock."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=start_time",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True,
    ).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


def reframe(width: int, height: int, fit: str, plan=None, offset: float = 0.0, still_at: float | None = None) -> str:
    """
    Filter graph from [0:v] to [r]: the source reframed to width×height.

    With a framing plan (crop mode) the source is cut into the plan's segments, each with its
    own still crop (position and zoom) on the speaker, then joined back up: the framing jumps
    between segments and never moves within one. `offset` is the source's first timestamp, so
    the joined video keeps the same clock as its audio. `still_at` uses just the crop in force
    at that moment (for a single frame).
    """
    if fit == "crop" and plan is not None:
        to_frame = f"scale={width}:{height}:flags=lanczos,setsar=1"
        segs = [plan.at(still_at)] if still_at is not None else plan.segments

        def segment_graph(s, incoming: str, outgoing: str, tag: str) -> str:
            if s.wide:
                foreground_h = round(width * s.h / s.w) // 2 * 2
                return (
                    f"{incoming}split=2[{tag}bgsrc][{tag}fgsrc];"
                    f"[{tag}bgsrc]scale={width}:{height}:force_original_aspect_ratio=increase,"
                    f"crop={width}:{height},boxblur=24:2,eq=brightness=-0.12,setsar=1[{tag}bg];"
                    f"[{tag}fgsrc]crop={s.w}:{s.h}:{s.x}:{s.y},"
                    f"scale={width}:{foreground_h}:flags=lanczos,setsar=1[{tag}fg];"
                    f"[{tag}bg][{tag}fg]overlay=(W-w)/2:(H-h)/2,setsar=1{outgoing}"
                )
            if s.fit:
                # Each fallback needs unique labels when several shots are concatenated.
                graph = reframe(width, height, "fit")
                for old, new in (("[0:v]", incoming), ("[r]", outgoing),
                                 ("[a]", f"[{tag}a]"), ("[b]", f"[{tag}b]"),
                                 ("[bg]", f"[{tag}bg]"), ("[fg]", f"[{tag}fg]")):
                    graph = graph.replace(old, new)
                return graph
            if s.secondary is not None:
                crops = [(s.x, s.y, s.w, s.h), s.secondary]
                if s.tertiary is not None:
                    crops.append(s.tertiary)
                count = len(crops)
                panel_h = height // (count * 2) * 2
                sizes = [panel_h] * (count - 1) + [height - panel_h * (count - 1)]
                graph = [f"{incoming}split={count}" + "".join(f"[{tag}src{j}]" for j in range(count))]
                for j, ((x, y, w, h), output_h) in enumerate(zip(crops, sizes)):
                    graph.append(f"[{tag}src{j}]crop={w}:{h}:{x}:{y},"
                                 f"scale={width}:{output_h}:flags=lanczos,setsar=1[{tag}panel{j}]")
                graph.append("".join(f"[{tag}panel{j}]" for j in range(count)) + f"vstack=inputs={count}{outgoing}")
                return ";".join(graph)
            return f"{incoming}crop={s.w}:{s.h}:{s.x}:{s.y},{to_frame}{outgoing}"

        if len(segs) == 1:
            return segment_graph(segs[0], "[0:v]", "[r]", "one")
        k = len(segs)
        parts = ["[0:v]split=" + str(k) + "".join(f"[s{i}]" for i in range(k))]
        for i, s in enumerate(segs):
            end = f":end={segs[i + 1].start + offset:.3f}" if i + 1 < k else ""
            parts.append(
                f"[s{i}]trim=start={s.start + offset:.3f}{end},setpts=PTS-STARTPTS[q{i}];"
                + segment_graph(s, f"[q{i}]", f"[p{i}]", f"seg{i}")
            )
        parts.append("".join(f"[p{i}]" for i in range(k)) + f"concat=n={k}:v=1:a=0,setpts=PTS+{offset:.3f}/TB[r]")
        return ";".join(parts)
    if fit == "fit":
        return (
            "[0:v]split[a][b];"
            f"[a]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},"
            "boxblur=24:2,eq=brightness=-0.12[bg];"
            f"[b]scale={width}:{height}:force_original_aspect_ratio=decrease[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[r]"
        )
    return f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1[r]"


def _run_ffmpeg(args: list[str], cwd: Path, duration: float, on_progress=None) -> None:
    """Run an encode, trying NVENC first and falling back to x264 (NVENC can fail when memory is tight)."""
    err = ""
    for encoder in video_encoders():
        cmd = ["ffmpeg", "-y", "-v", "error", *args[:-1], *encoder, *args[-1:]]
        proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        assert proc.stdout
        try:
            for line in proc.stdout:
                m = re.match(r"out_time_us=(\d+)", line)
                if m and on_progress and duration:
                    on_progress(min(1.0, int(m.group(1)) / 1e6 / duration))
        except BaseException:
            proc.kill()
            proc.communicate()
            raise
        _, err = proc.communicate()
        if proc.returncode == 0:
            return
    raise RuntimeError(f"ffmpeg failed: {err.strip()[-400:]}")


LOGO_POSITIONS = ["tl", "tc", "tr", "ml", "mc", "mr", "bl", "bc", "br"]


def brand_margin(width: int, height: int) -> int:
    """Distance of the branding mark from the frame edge, in output pixels."""
    return round(min(width, height) * 0.045)


def brand_overlay_xy(position: str, margin: int) -> tuple[str, str]:
    """FFmpeg overlay expressions for one of the nine anchor points (t/m/b × l/c/r)."""
    row, col = position[0], position[1]
    x = {"l": f"{margin}", "c": "(W-w)/2", "r": f"W-w-{margin}"}[col]
    y = {"t": f"{margin}", "m": "(H-h)/2", "b": f"H-h-{margin}"}[row]
    return x, y


TITLE_SECONDS = 3.0


def render_clip(source: Path, ass: Path, out: Path, width: int, height: int, fit: str, on_progress=None,
                brand: Path | None = None, brand_position: str = "bc", plan=None,
                title: Path | None = None) -> Path:
    """
    Reframe the clip (on the speaker when there's a plan), burn in the captions, stamp the
    branding mark (already built at its final size and opacity, see thumbs.build_brand), and
    show the title image (see thumbs.title_overlay) over the first TITLE_SECONDS while the
    clip keeps playing underneath.
    """
    work = out.parent
    # libass paths go inside a filter string, where ':' and '\' need escaping on Windows;
    # relative paths from the work folder sidestep all of that.
    fonts = os.path.relpath(FONTS_DIR, work).replace("\\", "/")
    rel_src = os.path.relpath(source, work)
    offset = start_time(source)
    inputs = ["-i", rel_src]
    render_ass = ass
    if fit == "crop" and plan and any(s.secondary is not None for s in plan.segments):
        # Tight lower-panel portraits put faces near the usual caption band.
        # Keep a stable lower caption baseline throughout clips using this layout.
        from .captions import for_stacked_panels
        render_ass = work / f"{ass.stem}.stacked.ass"
        render_ass.write_text(for_stacked_panels(ass.read_text(encoding="utf-8"), height), encoding="utf-8")
    graph = reframe(width, height, fit, plan, offset if plan else 0.0) + f";[r]ass={render_ass.name}:fontsdir={fonts}[c0]"
    last = "c0"
    if brand:
        x, y = brand_overlay_xy(brand_position, brand_margin(width, height))
        inputs += ["-i", os.path.relpath(brand, work)]
        graph += f";[{len(inputs) // 2 - 1}:v]format=rgba[lg];[{last}][lg]overlay={x}:{y}:format=auto[c1]"
        last = "c1"
    if title:
        # A still held for the opening seconds, fading in fast and out gently.
        inputs += ["-loop", "1", "-t", f"{TITLE_SECONDS:.2f}", "-i", os.path.relpath(title, work)]
        n = sum(1 for a in inputs if a == "-i") - 1
        graph += (
            f";[{n}:v]format=rgba,fade=t=in:st=0:d=0.25:alpha=1,"
            f"fade=t=out:st={TITLE_SECONDS - 0.5:.2f}:d=0.5:alpha=1,setpts=PTS+{offset:.3f}/TB[tt]"
            f";[{last}][tt]overlay=0:0:format=auto:eof_action=pass[c2]"
        )
        last = "c2"
    graph += f";[{last}]null[v]"
    args = [
        *inputs, "-filter_complex", graph, "-map", "[v]", "-map", "0:a?",
        "-pix_fmt", "yuv420p", "-r", str(FPS), *AUDIO, "-movflags", "+faststart",
        "-progress", "pipe:1", "-nostats", out.name,
    ]
    _run_ffmpeg(args, work, probe_duration(source), on_progress)
    return out


def grab_frame(source: Path, out: Path, width: int, height: int, fit: str, plan=None) -> Path:
    """A representative frame of the clip, reframed like the short (no captions), for the thumbnail."""
    duration = probe_duration(source)
    # Skip the very first moments, which are often mid-cut.
    seek = min(1.5, duration / 4)
    graph = reframe(width, height, fit, plan, still_at=seek) + ";[r]thumbnail=30[v]"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-ss", f"{seek:.2f}", "-i", str(source),
         "-filter_complex", graph, "-map", "[v]", "-frames:v", "1", "-q:v", "2", str(out)],
        check=True,
    )
    return out


def add_intro(clip: Path, image: Path, voice: Path, out: Path, width: int, height: int) -> Path:
    """Prepend the thumbnail as a still with the spoken title, then the clip."""
    intro = probe_duration(voice) + 0.4
    graph = (
        f"[0:v]scale={width}:{height},setsar=1,fps={FPS},format=yuv420p,"
        f"fade=t=out:st={intro - 0.25:.2f}:d=0.25[iv];"
        f"[1:a]aresample=48000,aformat=channel_layouts=stereo,apad=whole_dur={intro:.2f}[ia];"
        f"[2:v]fps={FPS},format=yuv420p,setsar=1[cv];"
        "[2:a]aresample=48000,aformat=channel_layouts=stereo[ca];"
        "[iv][ia][cv][ca]concat=n=2:v=1:a=1[v][a]"
    )
    args = [
        "-loop", "1", "-t", f"{intro:.2f}", "-i", str(image), "-i", str(voice), "-i", str(clip),
        "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
        "-pix_fmt", "yuv420p", *AUDIO, "-movflags", "+faststart",
        "-progress", "pipe:1", "-nostats", str(out),
    ]
    _run_ffmpeg(args, out.parent, intro + probe_duration(clip))
    return out
