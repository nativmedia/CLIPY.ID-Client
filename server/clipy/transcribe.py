"""
Speech → words with timestamps, using faster-whisper (Whisper large-v3) on the local GPU.
Falls back to the CPU when CUDA isn't available, which works but is much slower.
"""

from __future__ import annotations

import os
import json
import queue
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from .captions import Word

MODEL = os.environ.get("CLIPY_WHISPER_MODEL", "large-v3")
_DLL_DIRS = []  # Keep Windows AddDllDirectory handles alive for the process.

# Hugging Face's Xet downloader buffers big chunks in memory and aborts the whole process
# when an allocation fails; plain HTTP streams the model to disk instead.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


def _add_cuda_dlls() -> None:
    """On Windows the pip CUDA wheels ship their DLLs under site-packages/nvidia/*/bin."""
    if sys.platform != "win32":
        return
    for base in dict.fromkeys([*sys.path, getattr(sys, '_MEIPASS', '')]):
        if not base:
            continue
        nvidia = Path(base) / "nvidia"
        if not nvidia.is_dir():
            continue
        for lib in nvidia.iterdir():
            bin_dir = lib / "bin"
            if bin_dir.is_dir():
                _DLL_DIRS.append(os.add_dll_directory(str(bin_dir)))
                os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")


_add_cuda_dlls()

from faster_whisper import WhisperModel  # noqa: E402  (needs the DLL paths above)
import ctranslate2  # noqa: E402
import numpy as np  # noqa: E402
import wave  # noqa: E402

_model: WhisperModel | None = None
_model_device: str | None = None
_lock = threading.Lock()


def device() -> str:
    # Desktop CPU bundles intentionally omit the multi-gigabyte CUDA libraries.
    if os.environ.get("CLIPY_WHISPER_DEVICE", "").lower() == "cpu":
        return "cpu"
    try:
        return "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    except Exception:
        return "cpu"


def get_model(dev: str | None = None) -> WhisperModel:
    global _model, _model_device
    with _lock:
        dev = dev or device()
        if _model is None or _model_device != dev:
            _model = WhisperModel(MODEL, device=dev, compute_type="float16" if dev == "cuda" else "int8")
            _model_device = dev
        return _model


def load_wav(path: str) -> np.ndarray:
    """
    Read the 16 kHz mono 16-bit WAV that media.extract_audio writes. Handing Whisper the
    samples directly skips its PyAV decoder, whose API keeps shifting between releases.
    """
    with wave.open(path, "rb") as w:
        if w.getframerate() != 16000 or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError(f"{path}: expected 16 kHz mono 16-bit WAV")
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    return pcm.astype(np.float32) / 32768.0


def _transcribe_direct(audio_path: str, language: str | None = None, on_progress=None, *, dev: str) -> tuple[list[Word], str]:
    model = get_model(dev)
    segments, info = model.transcribe(
        load_wav(audio_path),
        language=language,
        beam_size=5,
        word_timestamps=True,
        # No VAD: Silero drops speech and singing over background music, which shorts are full of.
        vad_filter=False,
        # Each segment stands alone: stops Whisper from looping a phrase it saw earlier.
        condition_on_previous_text=False,
    )
    words: list[Word] = []
    for seg in segments:
        # Whisper's own "this was probably not speech" test, to keep silence free of made-up text.
        if seg.no_speech_prob > 0.6 and seg.avg_logprob < -1.0:
            continue
        for w in seg.words or []:
            text = w.word.strip()
            if text:
                words.append(Word(text=text, start=float(w.start), end=float(w.end)))
        if on_progress and info.duration:
            on_progress(min(1.0, seg.end / info.duration))
    return words, info.language


def _backend_process(audio_path: str, language: str | None, on_progress, *, dev: str, stall_timeout: int, device_index: int = 0, device_name: str = ''):
    command = ([sys.executable, '--transcription-worker'] if getattr(sys, 'frozen', False)
               else [sys.executable, '-m', 'clipy.transcribe_worker'])
    events = queue.Queue()
    with tempfile.TemporaryFile(mode='w+t', encoding='utf-8', errors='replace') as errors:
        process = subprocess.Popen([*command, audio_path, language or '', dev, str(device_index), device_name], stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=errors, text=True, encoding='utf-8',
                                   errors='replace', cwd=Path(__file__).resolve().parent.parent,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        def read_events():
            try:
                for line in process.stdout:
                    events.put(line)
            finally:
                events.put(None)
        threading.Thread(target=read_events, daemon=True).start()
        last_event = time.monotonic()
        try:
            while True:
                try:
                    line = events.get(timeout=1)
                except queue.Empty:
                    if time.monotonic() - last_event > stall_timeout:
                        raise RuntimeError(f'{"GPU" if dev in ("cuda", "vulkan") else "CPU"} tidak merespons selama {stall_timeout // 60} menit')
                    continue
                if line is None:
                    errors.seek(0)
                    details = errors.read()[-4096:].lower()
                    if 'out of memory' in details or 'cuda_error_out_of_memory' in details:
                        raise RuntimeError('memori GPU tidak mencukupi')
                    raise RuntimeError('Proses transkripsi GPU gagal dijalankan' if dev in ('cuda', 'vulkan') else 'Proses transkripsi CPU gagal dijalankan')
                try:
                    event = json.loads(line)
                except (ValueError, TypeError):
                    continue
                last_event = time.monotonic()
                if event.get('type') == 'progress' and on_progress:
                    on_progress(float(event['value']))
                elif event.get('type') == 'done':
                    result = [Word(**word) for word in event['words']]
                    if process.wait(timeout=10) != 0:
                        raise RuntimeError('proses transkripsi berhenti sebelum selesai')
                    return result, event['language']
                elif event.get('type') == 'error':
                    raise RuntimeError(event.get('message') or 'Transkripsi gagal dijalankan')
        finally:
            if process.poll() is None:
                if os.name == 'nt':
                    try:
                        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], stdin=subprocess.DEVNULL,
                                       capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
                    except (OSError, subprocess.SubprocessError):
                        pass
                if process.poll() is None:
                    process.kill()
            process.wait(timeout=10)
            if process.stdout:
                process.stdout.close()


def _gpu_process(audio_path: str, language: str | None, on_progress):
    return _backend_process(audio_path, language, on_progress, dev='cuda', stall_timeout=180)


def transcribe(audio_path: str, language: str | None = None, on_progress=None, on_status=None) -> tuple[list[Word], str]:
    """Use CUDA in an isolated child; fall back to CPU if it fails or stalls."""
    reason = ('CPU dipilih melalui pengaturan lingkungan.' if os.environ.get('CLIPY_WHISPER_DEVICE', '').lower() == 'cpu'
              else 'GPU CUDA atau Vulkan yang kompatibel tidak terdeteksi.')
    if device() == 'cuda':
        if on_status:
            on_status('GPU NVIDIA · faster-whisper (CUDA)', '')
        try:
            return _gpu_process(audio_path, language, on_progress)
        except (OSError, RuntimeError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as exc:
            reason = f'GPU NVIDIA tidak dapat digunakan: {exc}.'
    elif os.environ.get('CLIPY_WHISPER_DEVICE', '').lower() != 'cpu':
        from . import whisper_vulkan
        vulkan = whisper_vulkan.detect_device()
        if vulkan:
            index, vendor, name = vulkan
            if on_status:
                on_status(f'GPU {vendor} · whisper.cpp Vulkan', 'Model Vulkan sekitar 3 GB diunduh sekali jika belum tersedia.')
            try:
                return _backend_process(audio_path, language, on_progress, dev='vulkan', stall_timeout=180,
                                        device_index=index, device_name=name)
            except (OSError, RuntimeError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as exc:
                reason = f'GPU {vendor} Vulkan tidak dapat digunakan: {exc}.'
    if on_status:
        on_status('CPU · faster-whisper', reason)
    return _backend_process(audio_path, language, on_progress, dev='cpu', stall_timeout=600)
