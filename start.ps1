param(
    [switch]$Install,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$server = Join-Path $PSScriptRoot 'server'
$web = Join-Path $PSScriptRoot 'web'
$python = Join-Path $server '.venv\Scripts\python.exe'
$node = (Get-Command node.exe -ErrorAction Stop).Source
$apiUrl = 'http://127.0.0.1:4748'
$webUrl = 'http://127.0.0.1:4747'

if ($Install) {
    $npm = (Get-Command npm.cmd -ErrorAction Stop).Source
    Get-Command ffmpeg.exe,ffprobe.exe -ErrorAction Stop | Out-Null
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        & python -m venv (Join-Path $server '.venv')
        if ($LASTEXITCODE -ne 0) { throw 'Gagal menyiapkan Python. Pastikan Python 3.12 terpasang.' }
    }

    & $python -m pip install -r (Join-Path $server 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Gagal memasang kebutuhan server.' }

    $models = Join-Path $server 'models'
    New-Item -ItemType Directory -Path $models -Force | Out-Null
    $modelUrls = @{
        'face_landmarker.task' = 'https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task'
        'face_detection_yunet_2023mar.onnx' = 'https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx'
    }
    foreach ($name in $modelUrls.Keys) {
        $destination = Join-Path $models $name
        if (Test-Path -LiteralPath $destination -PathType Leaf) { continue }
        $download = Join-Path $models ('.download-' + [guid]::NewGuid())
        try {
            Invoke-WebRequest -UseBasicParsing -Uri $modelUrls[$name] -OutFile $download
            Move-Item -LiteralPath $download -Destination $destination
        } finally {
            Remove-Item -LiteralPath $download -Force -ErrorAction SilentlyContinue
        }
    }

    & $npm --prefix $web ci
    if ($LASTEXITCODE -ne 0) { throw 'Gagal memasang kebutuhan tampilan web.' }
}

$vite = '.\node_modules\vite\bin\vite.js'
if (-not (Test-Path -LiteralPath $python -PathType Leaf) -or
    -not (Test-Path -LiteralPath (Join-Path $web $vite) -PathType Leaf)) {
    throw 'Jalankan kembali start.ps1 dengan -Install untuk menyiapkan CLIPY.ID.'
}

if (Get-NetTCPConnection -LocalPort 4747,4748 -State Listen -ErrorAction SilentlyContinue) {
    throw 'Port 4747 atau 4748 sedang digunakan. Hentikan sesi CLIPY.ID yang lama sebelum memulai sesi baru.'
}

$api = $null
$frontend = $null
try {
    $api = Start-Process -FilePath $python -WorkingDirectory $server -NoNewWindow -PassThru -ArgumentList '-m uvicorn app:app --host 127.0.0.1 --port 4748'
    $frontend = Start-Process -FilePath $node -WorkingDirectory $web -NoNewWindow -PassThru -ArgumentList "$vite --port 4747 --strictPort"

    $deadline = [DateTime]::UtcNow.AddMinutes(2)
    while ($true) {
        if ($api.HasExited -or $frontend.HasExited) {
            throw 'Salah satu proses CLIPY.ID berhenti. Periksa pesan di terminal.'
        }
        try {
            $schema = Invoke-RestMethod -Uri "$apiUrl/openapi.json" -TimeoutSec 2
            $page = Invoke-WebRequest -UseBasicParsing -Uri $webUrl -TimeoutSec 2
            if ($schema.info.title -eq 'CLIPY.ID' -and $page.Content -match '<title>CLIPY\.ID</title>') { break }
        } catch {
            # Both services can need a moment before accepting requests.
        }
        if ([DateTime]::UtcNow -ge $deadline) {
            throw 'CLIPY.ID belum siap setelah dua menit. Periksa pesan di terminal.'
        }
        Start-Sleep -Milliseconds 500
    }

    if (-not $NoBrowser) { Start-Process $webUrl }
    Write-Host "CLIPY.ID siap di $webUrl. Tekan Ctrl+C setelah pekerjaan video selesai."
    while (-not $api.HasExited -and -not $frontend.HasExited) { Start-Sleep -Seconds 1 }
    throw 'Salah satu proses CLIPY.ID berhenti. Periksa pesan di terminal.'
} finally {
    foreach ($process in @($frontend, $api)) {
        if ($process) {
            if (-not $process.WaitForExit(2000)) {
                Stop-Process -InputObject $process -ErrorAction SilentlyContinue
            }
            $process.Dispose()
        }
    }
}
