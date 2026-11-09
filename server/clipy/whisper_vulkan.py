"""Windows whisper.cpp Vulkan integration; model and GPU are verified before use."""
from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import wave
from pathlib import Path

from .captions import Word

MODEL_SIZE = 3095033483
MODEL_SHA256 = '64d182b440b98d5203c4f9bd541544d84c605196c4f7b845dfa11fb23594d1e2'
MODEL_URL = ('https://huggingface.co/ggerganov/whisper.cpp/resolve/'
             '5359861c739e955e79d9a303bcbc70fb988958b1/ggml-large-v3.bin')


def binary_dir() -> Path:
    override = os.environ.get('CLIPY_VULKAN_BIN')
    if override:
        return Path(override)
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent / 'bin' / 'vulkan'
    return Path(__file__).resolve().parents[2] / 'build/vulkan-tooling/wout/bin/Release'


def parse_devices(output: str):
    found = []
    headers = list(re.finditer(r'(?m)^GPU(\d+):\s*$', output))
    for i, header in enumerate(headers):
        block = output[header.end():headers[i + 1].start() if i + 1 < len(headers) else len(output)]
        vendor = re.search(r'(?m)^\s*vendorID\s*=\s*0x([0-9a-f]+)', block, re.I)
        name = re.search(r'(?m)^\s*deviceName\s*=\s*(.+)', block)
        kind = re.search(r'(?m)^\s*deviceType\s*=\s*(.+)', block)
        if not vendor or not name or not kind:
            continue
        vendor_id = int(vendor.group(1), 16)
        label = {0x1002: 'AMD', 0x8086: 'Intel', 0x10de: 'NVIDIA'}.get(vendor_id)
        if label and 'CPU' not in kind.group(1):
            # The ordinal is the position in Vulkan's physical-device list.
            found.append((int(header.group(1)), label, name.group(1).strip()))
    return found


def detect_device():
    if os.name != 'nt':
        return None
    folder = binary_dir()
    if not (folder / 'whisper-cli.exe').is_file() or not (folder / 'vulkaninfoSDK.exe').is_file():
        return None
    try:
        result = subprocess.run([str(folder / 'vulkaninfoSDK.exe'), '--summary'], stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=20,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            return None
        devices = parse_devices(result.stdout)
        return next((device for device in devices if device[1] in ('AMD', 'Intel')), None) or next(iter(devices), None)
    except (OSError, subprocess.SubprocessError):
        return None


def _verified(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size != MODEL_SIZE:
        return False
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(4 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest() == MODEL_SHA256


def ensure_model(on_progress=None) -> Path:
    override = os.environ.get('CLIPY_VULKAN_MODEL')
    if override:
        model = Path(override)
        if not _verified(model):
            raise RuntimeError('model Vulkan tidak cocok atau belum lengkap')
        return model
    root = Path(os.environ.get('CLIPY_DATA_DIR') or (Path.home() / '.cache/clipy'))
    folder = root / 'models/whisper-vulkan'
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / 'ggml-large-v3.bin'
    if _verified(target):
        return target
    if shutil.disk_usage(folder).free < MODEL_SIZE + 512 * 1024 * 1024:
        raise RuntimeError('ruang kosong tidak cukup untuk model Vulkan sekitar 3 GB')
    temp = None
    try:
        with tempfile.NamedTemporaryFile(prefix='download-', suffix='.part', dir=folder, delete=False) as out:
            temp = Path(out.name)
            digest = hashlib.sha256()
            size = 0
            last_report = 0.0
            request = urllib.request.Request(MODEL_URL, headers={'User-Agent': 'CLIPY.ID/2'})
            with urllib.request.urlopen(request, timeout=30) as response:
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    size += len(block)
                    if size > MODEL_SIZE:
                        raise RuntimeError('ukuran model Vulkan tidak valid')
                    digest.update(block)
                    out.write(block)
                    if on_progress and time.monotonic() - last_report >= 2:
                        on_progress(min(0.2, 0.2 * size / MODEL_SIZE))
                        last_report = time.monotonic()
        if size != MODEL_SIZE or digest.hexdigest() != MODEL_SHA256:
            raise RuntimeError('verifikasi model Vulkan gagal')
        os.replace(temp, target)
        return target
    finally:
        if temp:
            temp.unlink(missing_ok=True)


def transcribe(audio_path: str, language: str | None, on_progress=None, device_index: int = 0, device_name: str = ''):
    model = ensure_model(on_progress).resolve()
    folder = binary_dir().resolve()
    audio_path = str(Path(audio_path).resolve())
    with wave.open(audio_path, 'rb') as wav:
        duration = wav.getnframes() / wav.getframerate()
    with tempfile.TemporaryDirectory(prefix='clipy-vulkan-') as temp:
        output = Path(temp) / 'words'
        cmd = [str(folder / 'whisper-cli.exe'), '-m', str(model), '-f', str(audio_path),
               '-l', language or 'auto', '-dtw', 'large.v3', '-ml', '1', '-sow', '-oj', '-of', str(output), '-pp']
        env = os.environ.copy()
        env['GGML_VK_VISIBLE_DEVICES'] = str(device_index)
        process = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                   cwd=folder, env=env, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        chunks = queue.Queue()
        def read_log():
            try:
                while chunk := os.read(process.stderr.fileno(), 4096):
                    chunks.put(chunk)
            finally:
                chunks.put(None)
        threading.Thread(target=read_log, daemon=True).start()
        log = bytearray()
        last_report = time.monotonic()
        try:
            while True:
                try:
                    chunk = chunks.get(timeout=1)
                except queue.Empty:
                    continue
                if chunk is None:
                    break
                log.extend(chunk)
                if len(log) > 131072:
                    del log[16384:-32768]
                if on_progress and (b'\n' in chunk or b'\r' in chunk or time.monotonic() - last_report >= 5):
                    line = bytes(log[-512:]).decode('utf-8', errors='replace')
                    matches = re.findall(r'progress\s*=\s*(\d+)%', line)
                    if matches:
                        on_progress(0.2 + 0.8 * min(100, int(matches[-1])) / 100)
                    else:
                        on_progress(None)
                    last_report = time.monotonic()
            returncode = process.wait(timeout=10)
            if returncode or b'using Vulkan0 backend' not in log or (device_name and device_name.casefold() not in log.decode('utf-8', errors='replace').casefold()):
                detail = log.decode('utf-8', errors='replace').lower()
                raise RuntimeError('memori GPU Vulkan tidak cukup' if 'out of memory' in detail else
                                   f'GPU Vulkan tidak kompatibel atau gagal menjalankan model (kode {returncode})')
            data = json.loads(output.with_suffix('.json').read_text(encoding='utf-8'))
            words = []
            for seg in data.get('transcription', []):
                text = str(seg.get('text', '')).strip()
                if not text:
                    continue
                if len(text.split()) != 1:
                    raise RuntimeError('timestamp kata Vulkan tidak lengkap')
                start = float(seg['offsets']['from']) / 1000
                end = min(duration, float(seg['offsets']['to']) / 1000)
                if start < 0 or end <= start or (words and start < words[-1].start):
                    raise RuntimeError('timestamp kata Vulkan tidak valid')
                words.append(Word(text, start, end))
            return words, data.get('result', {}).get('language') or language or 'unknown'
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
