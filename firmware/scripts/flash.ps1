# Flash the built firmware. You must put the board in download mode by hand.
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\flash.ps1 -Port COM23
# Use -Baud 115200 if 460800 fails. A CP2102 adapter cannot do the SDK's default 2000000.
param(
    [Parameter(Mandatory = $true)][string]$Port,
    [int]$Baud = 460800
)

. "$PSScriptRoot\env.ps1"
Set-Location $App

$bin = Join-Path $App 'build\build_out\AiPi-CAM-D200_bl616.bin'
if (-not (Test-Path $bin)) { throw "No firmware at $bin. Run build.ps1 first." }

Write-Host ""
Write-Host "Close PuTTY and every other program that uses $Port." -ForegroundColor Yellow
Write-Host "Put the board in download mode:" -ForegroundColor Yellow
Write-Host "  1. Hold Burn."
Write-Host "  2. Press and release RST while still holding Burn."
Write-Host "  3. Keep holding Burn for 2 more seconds, then release."
Read-Host "Then press Enter here to start flashing"

$log = Join-Path $env:TEMP 'aipi-flash.log'
& ..\bouffalo_sdk\tools\bflb_tools\bouffalo_flash_cube\BLFlashCommand.exe `
    --interface=uart --baudrate=$Baud --port=$Port --chipname=bl616 --cpu_id= --config=flash_prog_cfg.ini |
    Tee-Object -FilePath $log

if (Select-String -Path $log -SimpleMatch '[All Success]' -Quiet) {
    Write-Host ""
    Write-Host "FLASH OK. Press RST once (without Burn) to start the new firmware." -ForegroundColor Green
} else {
    Write-Host ""
    Write-Host "FLASH FAILED. If you saw 'shake hand fail': repeat the download-mode steps, then retry." -ForegroundColor Red
    Write-Host "Still failing? Try -Baud 115200, check TX/RX crossing, GND, and that nothing else holds $Port."
    exit 1
}
