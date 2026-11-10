$ErrorActionPreference = 'Stop'

$repository = 'https://github.com/nativmedia/CLIPY.ID-Client.git'
$destination = Join-Path $env:LOCALAPPDATA 'CLIPY.ID-Client'
$git = (Get-Command git.exe -ErrorAction Stop).Source

if (-not (Test-Path -LiteralPath $destination)) {
    & $git clone -- $repository $destination
    if ($LASTEXITCODE -ne 0) { throw 'Gagal mengambil repo CLIPY.ID-Client.' }
} else {
    $origin = & $git -C $destination remote get-url origin
    if ($LASTEXITCODE -ne 0 -or $origin -ne $repository) {
        throw "Folder $destination sudah digunakan. Gunakan git clone untuk memilih folder lain."
    }
    & $git -C $destination pull --ff-only
    if ($LASTEXITCODE -ne 0) { throw "Gagal memperbarui CLIPY.ID. Periksa folder $destination." }
}

& powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File (Join-Path $destination 'start.ps1') -Install
if ($LASTEXITCODE -ne 0) { throw 'CLIPY.ID gagal dibuka. Periksa pesan di terminal.' }
