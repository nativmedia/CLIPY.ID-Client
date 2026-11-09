# CLIPY.ID Client

Aplikasi web CLIPY.ID untuk membuat clip dari video YouTube atau video lokal. Repository ini berisi antarmuka dan backend klien; alat admin lisensi, arsip serial, dan kunci privat tidak disertakan.

## Menjalankan di Windows

Diperlukan Python, Node.js, FFmpeg dan FFprobe di PATH. Sediakan API key sendiri untuk layanan AI yang Anda gunakan.

1. Di terminal pertama, jalankan backend:

   ```powershell
   cd server
   python -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   .\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 4748
   ```

2. Ikuti petunjuk model framing di `server/README.md`. Di terminal kedua jalankan:

   ```powershell
   cd web
   npm ci
   npm run dev
   ```

3. Buka http://127.0.0.1:4747, masukkan API key dan pilih sumber video. Transkripsi lokal memerlukan model Whisper; unduhan pertama dapat memakan ruang dan waktu.

Ini adalah kode aplikasi yang dijalankan sendiri, bukan layanan web yang sudah di-host. Jangan membuka backend ke internet sebelum autentikasi, isolasi pelanggan, dan keamanan data disiapkan. Data di `server/data/` serta API key di komputer lokal tidak disertakan dalam repository.

## Pengakuan

Sebagian animasi diadaptasi dari ascii-sharks; lihat `third-party/ascii-sharks-LICENSE.txt`.