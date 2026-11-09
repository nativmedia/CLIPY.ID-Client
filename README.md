# CLIPY.ID

Aplikasi web **CLIPY.ID** untuk membuat clip pendek dan film cerita dari video YouTube maupun video lokal, lengkap dengan pemilihan momen otomatis, framing pembicara, subtitle & animasi caption, preset video, musik latar, thumbnail otomatis, dan branding.

Repository ini khusus untuk **Client** (antarmuka web dan backend pemrosesan video). Alat admin lisensi, daftar serial key, dan kunci privat tidak disertakan di repository ini.

## Menjalankan di Windows

Diperlukan Python, Node.js, serta FFmpeg dan FFprobe di `PATH`.

1. Jalankan backend API dari folder `server` (port **4748**):

   ```powershell
   cd server
   python -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   .\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 4748
   ```

2. Di terminal kedua, jalankan antarmuka web dari folder `web` (port **4747**):

   ```powershell
   cd web
   npm ci
   npm run dev
   ```

3. Buka **http://127.0.0.1:4747**, masukkan API key, dan pilih sumber video.
