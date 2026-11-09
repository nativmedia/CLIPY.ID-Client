"""Download public HTTPS audio without allowing access to private network addresses."""
import http.client
import ipaddress
import re
import tempfile
import yt_dlp
import socket
import ssl
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit
from . import media

MAX_BYTES = 100 * 1024 * 1024


YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be", "youtube-nocookie.com", "www.youtube-nocookie.com"}


def _youtube_id(parts):
    host = (parts.hostname or "").lower()
    if host not in YOUTUBE_HOSTS:
        return None
    if host == "youtu.be":
        candidate = parts.path.strip("/").split("/")[0]
    else:
        candidate = parse_qs(parts.query).get("v", [""])[0]
        if not candidate:
            match = re.match(r"^/(?:shorts|embed|live|v)/([^/?#]+)", parts.path)
            candidate = match.group(1) if match else ""
    return candidate if re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate) else None


def validate_url(url: str):
    try:
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.port not in (None, 443):
            raise ValueError
    except ValueError as e:
        raise ValueError("Gunakan tautan HTTPS langsung ke file audio publik") from e
    if (parts.hostname or "").lower() in YOUTUBE_HOSTS and not _youtube_id(parts):
        raise ValueError("Gunakan tautan YouTube ke satu video, bukan playlist")
    return parts


def _download_youtube(video_id: str, folder: Path) -> Path:
    # Never pass a user-controlled host, path or playlist to the extractor.
    canonical = f"https://www.youtube.com/watch?v={video_id}"
    def limit(progress):
        if (progress.get("downloaded_bytes") or 0) > MAX_BYTES:
            raise yt_dlp.utils.DownloadError("Ukuran musik maksimal 100 MB")
    with tempfile.TemporaryDirectory(prefix="clipy-yt-music-", dir=folder) as tmp:
        temporary = Path(tmp)
        options = {
            "format": "bestaudio[ext=m4a]/bestaudio",
            "outtmpl": str(temporary / "music.%(ext)s"),
            "noplaylist": True, "quiet": True, "no_warnings": True,
            "cachedir": False, "max_filesize": MAX_BYTES, "socket_timeout": 30,
            "retries": 2, "fragment_retries": 2, "progress_hooks": [limit],
        }
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(canonical, download=False)
                if not info or info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
                    raise ValueError("Siaran langsung tidak dapat digunakan sebagai musik")
                duration = info.get("duration")
                if duration is not None and duration > 4 * 3600:
                    raise ValueError("Durasi musik maksimal 4 jam")
                ydl.download([canonical])
        except yt_dlp.utils.DownloadError as exc:
            raise ValueError("Audio YouTube tidak dapat diambil. Pastikan video publik dan dapat diakses.") from exc
        candidates = [p for p in temporary.glob("music.*") if p.is_file() and p.suffix.lower() in (".m4a", ".webm", ".mp3", ".ogg", ".wav", ".flac")]
        if len(candidates) != 1 or candidates[0].stat().st_size > MAX_BYTES or not media.has_audio(candidates[0]):
            raise ValueError("Audio YouTube tidak tersedia atau melebihi batas 100 MB")
        result = folder / f"background_music{candidates[0].suffix.lower()}"
        candidates[0].replace(result)
        return result


class PinnedConnection(http.client.HTTPSConnection):
    def __init__(self, host, address):
        super().__init__(host, timeout=30, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        raw = socket.create_connection((self.address, 443), self.timeout)
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


def download(url: str, folder: Path) -> Path:
    parts = validate_url(url)
    video_id = _youtube_id(parts)
    if video_id:
        return _download_youtube(video_id, folder)
    for _ in range(4):
        parts = validate_url(url)
        try:
            addresses = {item[4][0] for item in socket.getaddrinfo(parts.hostname, 443, type=socket.SOCK_STREAM)}
        except OSError as e:
            raise ValueError("Alamat musik tidak dapat ditemukan") from e
        if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
            raise ValueError("Alamat musik harus berasal dari internet publik")
        conn = PinnedConnection(parts.hostname, next(iter(addresses)))
        try:
            target = (parts.path or "/") + (("?" + parts.query) if parts.query else "")
            conn.request("GET", target, headers={"User-Agent": "CLIPY/1.0", "Accept": "audio/*,application/octet-stream"})
            response = conn.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader("Location")
                if not location:
                    raise ValueError("Tautan musik tidak valid")
                url = urljoin(url, location)
                continue
            if response.status != 200:
                raise ValueError(f"Tautan musik gagal diunduh (HTTP {response.status})")
            content_type = (response.getheader("Content-Type") or "").split(";")[0].lower()
            if content_type and not (content_type.startswith("audio/") or content_type in ("application/octet-stream", "video/mp4")):
                raise ValueError("Gunakan tautan langsung ke file audio, bukan halaman web")
            size = response.getheader("Content-Length")
            if size and int(size) > MAX_BYTES:
                raise ValueError("Ukuran musik maksimal 100 MB")
            suffix = Path(parts.path).suffix.lower()
            if suffix not in (".mp3", ".wav", ".m4a", ".ogg", ".flac"):
                suffix = {"audio/wav": ".wav", "audio/x-wav": ".wav", "audio/mp4": ".m4a", "audio/ogg": ".ogg", "audio/flac": ".flac"}.get(content_type, ".mp3")
            path = folder / f"background_music{suffix}"
            try:
                total = 0
                with path.open("wb") as target_file:
                    while chunk := response.read(1024 * 1024):
                        total += len(chunk)
                        if total > MAX_BYTES:
                            raise ValueError("Ukuran musik maksimal 100 MB")
                        target_file.write(chunk)
                if not total or not media.has_audio(path):
                    raise ValueError("Berkas musik tidak memiliki audio")
            except Exception:
                path.unlink(missing_ok=True)
                raise
            return path
        finally:
            conn.close()
    raise ValueError("Terlalu banyak pengalihan tautan musik")
