# Build the AiPi-CAM-D200 firmware.
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build.ps1          (normal build)
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build.ps1 -Clean    (clean first)
param([switch]$Clean)

. "$PSScriptRoot\env.ps1"
Set-Location $App   # the Makefile uses paths relative to this folder

if ($Clean) {
    & ..\bouffalo_sdk\tools\make\make.exe clean
    if ($LASTEXITCODE -ne 0) { throw "make clean failed (exit code $LASTEXITCODE)" }
}

& ..\bouffalo_sdk\tools\make\make.exe -j4
if ($LASTEXITCODE -ne 0) { throw "Build failed (exit code $LASTEXITCODE). Read the first 'error:' line above." }

$bin = Join-Path $App 'build\build_out\AiPi-CAM-D200_bl616.bin'
if (Test-Path $bin) {
    $item = Get-Item $bin
    Write-Host ""
    Write-Host "BUILD OK: $($item.FullName)  ($($item.Length) bytes, $($item.LastWriteTime))" -ForegroundColor Green
} else {
    throw "Build finished but $bin was not created."
}
