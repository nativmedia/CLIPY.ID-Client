# CLIPY.ID

CLIPY.ID adalah aplikasi web untuk membuat clip pendek atau rangkuman cerita dari video YouTube dan video di komputer. Aplikasi dijalankan pada komputer Windows Anda dan dibuka melalui browser.

Panduan ini menjelaskan cara memasang CLIPY.ID dari awal, termasuk jika Anda belum pernah memakai Git atau terminal. Ikuti urutannya dan jalankan perintah satu per satu.

## Sebelum mulai

Siapkan komputer Windows 64-bit, koneksi internet, ruang penyimpanan yang cukup, dan API key dari penyedia AI yang didukung. Pemasangan dan pengunduhan model dapat memerlukan beberapa gigabyte dan waktu yang cukup lama.

Anda memerlukan empat program berikut:

1. **Git** untuk mengunduh kode dari GitHub. Unduh dan instal [Git for Windows](https://git-scm.com/install/windows); pilihan bawaan instalasi dapat digunakan.
2. **Python 3.12 64-bit** untuk menjalankan pemrosesan video. Versi yang telah digunakan pada komputer pengembangan adalah [Python 3.12.10](https://www.python.org/downloads/release/python-31210/); pada bagian **Files**, pilih **Windows installer (64-bit)**. Saat memasang, centang **Add Python to PATH** jika pilihan tersebut ditampilkan, kemudian lanjutkan instalasi.
3. **Node.js**, disarankan versi **24.15 atau lebih baru dalam seri 24**, untuk menjalankan tampilan web. Unduh installer Windows 64-bit dari [halaman unduhan Node.js seri 24](https://nodejs.org/en/download/archive/v24.18.0). Node.js juga memasang npm.
4. **FFmpeg dan FFprobe**, untuk memproses video. Pilih build Windows yang menyertakan **libass**, yang diperlukan untuk caption. Tautan ke penyedia build Windows tersedia pada [halaman unduhan FFmpeg](https://ffmpeg.org/download.html#build-windows).

Untuk memasang FFmpeg melalui Windows Package Manager, buka PowerShell dan jalankan:

```powershell
winget install --id Gyan.FFmpeg --exact
```

Jika muncul permintaan persetujuan dari Windows Package Manager, baca dan ikuti petunjuknya. Setelah pemasangan program selesai, **tutup terminal lalu buka terminal baru** agar perintah yang baru terpasang dapat dikenali. Bila `winget` tidak tersedia, gunakan build Windows dari tautan FFmpeg di atas dan tambahkan folder `bin` yang berisi `ffmpeg.exe` dan `ffprobe.exe` ke PATH Windows.

### Periksa program sudah terpasang

Tekan tombol **Windows**, ketik **PowerShell**, lalu buka aplikasi tersebut. Ketik setiap baris berikut, kemudian tekan **Enter** setelah tiap baris:

```powershell
git --version
python --version
node --version
npm.cmd --version
ffmpeg -version
ffprobe -version
```

Setiap perintah seharusnya menampilkan informasi versi. Pastikan `python --version` menunjukkan Python 3.12 dan `node --version` menunjukkan versi yang memenuhi syarat di atas. Jika suatu perintah tidak ditemukan, periksa pemasangannya dan buka ulang PowerShell sebelum melanjutkan.

Untuk memastikan FFmpeg memiliki filter caption `ass`, jalankan:

```powershell
ffmpeg -filters | Select-String ' ass '
```

Jika tidak ada hasil, gunakan build FFmpeg yang menyertakan libass.

## 1. Salin alamat repository

1. Buka [halaman GitHub CLIPY.ID-Client](https://github.com/nativmedia/CLIPY.ID-Client).
2. Tekan tombol hijau **Code** di atas daftar file.
3. Pilih tab **Local**, lalu pilih **HTTPS** jika belum terpilih.
4. Tekan ikon **salin** di sebelah alamat yang ditampilkan.

Alamat HTTPS repository ini adalah:

```text
https://github.com/nativmedia/CLIPY.ID-Client.git
```

## 2. Pilih folder dan jalankan `git clone`

1. Buka **File Explorer** dan masuk ke lokasi tempat Anda ingin menyimpan CLIPY.ID, misalnya **Desktop** atau folder lain pilihan Anda.
2. Klik **bilah alamat** di bagian atas File Explorer, ketik `powershell`, lalu tekan **Enter**. Terminal PowerShell akan terbuka pada folder yang sedang dipilih.
3. Ketik `git clone`, beri satu spasi, lalu tempel alamat HTTPS yang disalin tadi. Anda juga dapat menyalin perintah lengkap berikut:

   ```powershell
   git clone https://github.com/nativmedia/CLIPY.ID-Client.git
   ```

4. Tekan **Enter** dan tunggu sampai proses selesai. Git akan membuat folder baru bernama **CLIPY.ID-Client** di lokasi yang Anda pilih.
5. Masuk ke folder tersebut dengan perintah:

   ```powershell
   cd .\CLIPY.ID-Client
   ```

   Lokasi sekarang adalah folder utama aplikasi. Di dalamnya terdapat folder `server` dan `web`. Jika Anda sudah pernah melakukan clone di lokasi ini, gunakan folder yang sudah ada dan ikuti bagian **Memperbarui aplikasi**; tidak perlu melakukan clone ulang.

> GitHub juga menyediakan [panduan resmi menyalin repository](https://docs.github.com/en/repositories/creating-and-managing-repositories/cloning-a-repository).

## 3. Siapkan server dan model pendukung

Langkah ini dilakukan **sekali saat pemasangan pertama**, dari PowerShell yang sudah berada di folder `CLIPY.ID-Client`.

1. Masuk ke folder server:

   ```powershell
   cd .\server
   ```

2. Buat lingkungan Python khusus untuk CLIPY.ID:

   ```powershell
   python -m venv .venv
   ```

3. Pasang kebutuhan server:

   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

   Tunggu hingga proses selesai. Paket yang diunduh berukuran besar, termasuk pustaka untuk GPU NVIDIA, sehingga bisa memakan waktu walaupun Anda memakai CPU. Biarkan terminal tetap terbuka selama pemasangan.

4. Siapkan folder untuk model pendeteksi wajah dan unduh dua file pendukung crop berikut. Jalankan setiap baris dari folder `server`:

   ```powershell
   New-Item -ItemType Directory -Force -Path models | Out-Null
   Invoke-WebRequest -Uri 'https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task' -OutFile 'models/face_landmarker.task'
   Invoke-WebRequest -Uri 'https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx' -OutFile 'models/face_detection_yunet_2023mar.onnx'
   ```

   Tunggu setiap unduhan selesai sebelum menjalankan baris berikutnya. Kedua model membantu crop mengikuti pembicara. Jika unduhan gagal, periksa koneksi dan jalankan kembali perintah yang gagal.

5. Jalankan server:

   ```powershell
   .\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 4748
   ```

   Tunggu pesan bahwa server berjalan pada `http://127.0.0.1:4748`. **Biarkan jendela ini terbuka.**

Jika aplikasi membutuhkan transkripsi lokal, model transkripsi dapat diunduh otomatis saat pertama digunakan. Model bawaan berukuran sekitar 3 GB; siapkan koneksi dan ruang kosong tambahan. Untuk video YouTube, aplikasi dapat menggunakan subtitle yang tersedia terlebih dahulu.

## 4. Siapkan dan jalankan tampilan web

Server pada langkah sebelumnya harus tetap berjalan. Sekarang buka **jendela PowerShell kedua**:

1. Buka folder **CLIPY.ID-Client** di File Explorer. Klik bilah alamat, ketik `powershell`, lalu tekan **Enter**.
2. Masuk ke folder tampilan web:

   ```powershell
   cd .\web
   ```

3. Pada pemasangan pertama, unduh kebutuhan tampilan:

   ```powershell
   npm.cmd ci
   ```

4. Setelah selesai, jalankan tampilan:

   ```powershell
   npm.cmd run dev
   ```

5. Tunggu sampai terminal menunjukkan alamat `http://127.0.0.1:4747`. **Biarkan jendela kedua ini juga tetap terbuka.**
6. Buka Chrome atau browser lain di komputer yang sama, lalu kunjungi **[http://127.0.0.1:4747](http://127.0.0.1:4747)**.
7. Masukkan API key Anda di aplikasi, pilih sumber video, atur clip, dan mulai proses.

Terminal pertama menjalankan pemrosesan video pada port **4748**. Terminal kedua menjalankan tampilan pada port **4747**. Jika salah satu ditutup, bagian aplikasi tersebut berhenti.

## Membuka kembali setelah komputer dinyalakan

Anda tidak perlu mengulangi `git clone` atau membuat lingkungan Python setiap kali. Buka folder **CLIPY.ID-Client**, lalu:

1. Buka PowerShell di dalam folder `server` dan jalankan:

   ```powershell
   .\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 4748
   ```

2. Buka PowerShell kedua di dalam folder `web` dan jalankan:

   ```powershell
   npm.cmd run dev
   ```

3. Buka [http://127.0.0.1:4747](http://127.0.0.1:4747) di browser. Biarkan kedua terminal tetap terbuka.

Untuk menghentikan aplikasi, pastikan proses video selesai terlebih dahulu, lalu tekan **Ctrl+C** di setiap terminal. Jika diminta konfirmasi, ikuti petunjuk pada terminal.

## Memperbarui aplikasi

Pembaruan dilakukan melalui terminal pada folder yang sudah pernah di-clone:

1. Tunggu seluruh pekerjaan video selesai, lalu hentikan server dan tampilan dengan **Ctrl+C** pada kedua terminal.
2. Buka folder utama **CLIPY.ID-Client** di File Explorer. Klik bilah alamat, ketik `powershell`, lalu tekan **Enter**.
3. Ambil pembaruan:

   ```powershell
   git pull --ff-only
   ```

4. Perbarui kebutuhan server, kemudian kembali ke folder utama:

   ```powershell
   cd .\server
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   cd ..
   ```

5. Perbarui kebutuhan tampilan web:

   ```powershell
   cd .\web
   npm.cmd ci
   ```

6. Jalankan kembali server dan tampilan sesuai bagian **Membuka kembali setelah komputer dinyalakan**.

Simpan folder `server/data` karena berisi data proyek dan pengaturan lokal. Jangan membagikan folder tersebut atau API key Anda. Model dan data lokal tidak perlu dihapus saat memperbarui. Jika `git pull` menolak karena ada perubahan lokal, jangan menghapusnya; periksa perubahan tersebut terlebih dahulu.

## Menggabungkan clip Filming

Setelah semua clip Filming selesai, tekan **Gabungkan clip** pada hasil proyek. Clip disusun berdasarkan nomor, dengan narasi dan caption yang sudah ada, tanpa thumbnail. Hasil gabungan dapat diunduh dan clip satuan tetap tersedia.

Penggabungan memakai FFmpeg stream copy agar tidak merender ulang. Jika ada clip gagal atau format clip tidak cocok, aplikasi menampilkan keterangan; selesaikan atau ulangi clip yang bermasalah, lalu coba lagi.

Pada **Project Dashboard**, setiap proyek memiliki satu baris video tersendiri. Gunakan tombol panah kiri dan kanan pada baris untuk melihat clip lain dan video gabungannya. Pada layar lebar, hingga lima video terlihat sekaligus.

Pada kartu **video gabungan**, judul dapat diubah lalu diterapkan sebagai thumbnail dengan tombol **apply**. Tombol **download** tetap dapat digunakan tanpa apply. Setelah thumbnail diterapkan, video yang diunduh menyertakan gambar sampul tanpa mengubah isi video dan audio; tautan **download thumbnail** juga tersedia jika ingin mengunggah gambarnya secara terpisah. Tampilan sampul yang tertanam dapat berbeda antara pemutar atau platform.

Setiap clip memiliki deskripsi pendek, sedang, dan panjang yang dapat diedit dan disalin. Pembuatan awal memakai API key AI Anda dan dapat memerlukan waktu serta biaya sesuai penyedia. Video gabungan yang sudah dibuat juga tampil di dashboard beserta usulan judul dan deskripsinya. Teks baru tersimpan di proyek; deskripsi lama tidak dihapus.

Pada thumbnail yang dibuat berikutnya, pilihan teks otomatis menggunakan judul clip yang sama. Thumbnail lama tetap seperti semula sampai clip dibuat ulang; pilihan judul manual dan tanpa judul tetap tersedia.

## Jika ada kendala

- **`git`, `python`, `node`, `npm.cmd`, `ffmpeg`, atau `ffprobe` tidak dikenali:** pastikan program terkait terpasang, tutup PowerShell, lalu buka lagi. Periksa PATH jika masalah berlanjut.
- **Tidak menemukan folder `server` atau `web`:** pastikan terminal dibuka di dalam folder `CLIPY.ID-Client`, bukan di folder induknya.
- **Alamat 4747 tidak terbuka atau API tidak terhubung:** pastikan kedua terminal masih berjalan dan periksa pesan kesalahan di masing-masing terminal.
- **Port sudah digunakan:** ada aplikasi lain yang memakai port tersebut. Tutup aplikasi itu jika memang Anda mengenalinya, lalu jalankan kembali CLIPY.ID.
- **Pemasangan paket atau model gagal:** periksa koneksi internet dan ruang penyimpanan, lalu ulangi perintah yang gagal.
- **Proses pertama lama:** pengunduhan model dan transkripsi lokal dapat membutuhkan waktu; periksa status proyek dan pesan pada terminal.

Repository ini berisi kode aplikasi client. Alat administrasi lisensi, daftar serial, dan kunci privat tidak disertakan.
