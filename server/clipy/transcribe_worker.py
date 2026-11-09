"""Run CUDA inference in a separate process so native failures can fall back."""
import json
import sys
from dataclasses import asdict


def emit(event):
    print(json.dumps(event, ensure_ascii=False), flush=True)


def main():
    from . import transcribe
    audio, language, dev = sys.argv[1:4]
    if dev not in ('cuda', 'cpu', 'vulkan'):
        raise ValueError('Backend transkripsi tidak valid')
    try:
        def progress(value):
            emit({'type': 'heartbeat'} if value is None else {'type': 'progress', 'value': value})
        if dev == 'vulkan':
            from . import whisper_vulkan
            words, detected = whisper_vulkan.transcribe(audio, language or None, progress, int(sys.argv[4]), sys.argv[5])
        else:
            words, detected = transcribe._transcribe_direct(audio, language or None, progress, dev=dev)
        emit({'type': 'done', 'words': [asdict(word) for word in words], 'language': detected})
    except Exception as exc:
        message = str(exc).lower()
        reason = (str(exc) if dev == 'vulkan' else
                  ('memori GPU tidak mencukupi' if 'out of memory' in message else 'GPU atau pustaka CUDA tidak kompatibel')
                  if dev == 'cuda' else 'CPU gagal menjalankan model transkripsi')
        emit({'type': 'error', 'message': reason})
        raise SystemExit(1)


if __name__ == '__main__':
    main()
