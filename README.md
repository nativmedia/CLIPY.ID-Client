# CLIPY.ID

Aplikasi web **CLIPY.ID** untuk membuat clip pendek dan film cerita dari video YouTube maupun video lokal, lengkap dengan pemilihan momen otomatis, framing pembicara, subtitle & animasi caption, preset video, musik latar, thumbnail otomatis, dan branding.

Repository ini khusus untuk **Client** (antarmuka web dan backend pemrosesan video). Alat admin lisensi, daftar serial key, dan kunci privat tidak disertakan di repository ini.

## Menjalankan di Windows

Diperlukan Python, Node.js 24.15+ atau 26+, serta FFmpeg dan FFprobe di `PATH`.

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

Untuk memeriksa komponen frontend, jalankan `npm test` dan `npm run typecheck` di folder `web`.

## Memperbarui PWA melalui terminal

Hentikan proses frontend dan backend setelah pekerjaan video selesai. Dari folder
repository client, ambil pembaruan dengan `git pull --ff-only`, lalu jalankan
`npm ci` di folder `web` dan `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`
di folder `server`. Setelah selesai, jalankan kembali backend dan frontend seperti
petunjuk di atas. Data lokal, model, pengaturan, dan lisensi tidak perlu dihapus.

## Gabungkan hasil Filming

Setelah seluruh clip Filming selesai, gunakan **Gabungkan clip** pada hasil
proyek. Aplikasi menyusun clip berdasarkan nomor dengan audio dan caption yang
ada pada masing-masing video, tanpa thumbnail. Hasil gabungan dapat diunduh dan
clip satuan tetap tersedia. Penggabungan menggunakan FFmpeg stream copy agar
tidak merender ulang; jika format antar-clip tidak kompatibel, aplikasi akan
menjelaskan penyebabnya dan clip perlu diperbaiki sebelum dicoba kembali.
