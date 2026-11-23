# Send AT commands to the board and/or watch its console.
# The firmware's AT parser needs each line to end in exactly CR+LF and to arrive as one
# quick burst. This script sends one character every few milliseconds, then CR+LF.
#
#   Connect to Wi-Fi and print the IP:
#     powershell -NoProfile -ExecutionPolicy Bypass -File scripts\at.ps1 -Port COM23 -Command "AT+WJAP=MyWifi,MyPassword","AT+WJAP?"
#   Just watch the console for 20 seconds (press RST after starting):
#     powershell -NoProfile -ExecutionPolicy Bypass -File scripts\at.ps1 -Port COM23 -WatchSeconds 20
#
# Wi-Fi name and password must not contain a comma. Console speed is 115200 after the board.c change.
param(
    [Parameter(Mandatory = $true)][string]$Port,
    [int]$Baud = 115200,
    [string[]]$Command = @(),
    [int]$WatchSeconds = 0,
    [int]$CharDelayMs = 3
)

$sp = New-Object System.IO.Ports.SerialPort $Port, $Baud, 'None', 8, 'One'
try {
    $sp.Open()

    foreach ($cmd in $Command) {
        Write-Host ">> $cmd" -ForegroundColor Cyan
        foreach ($ch in ($cmd + "`r`n").ToCharArray()) {
            $sp.Write([string]$ch)
            Start-Sleep -Milliseconds $CharDelayMs
        }
        Start-Sleep -Milliseconds 1500
        Write-Host $sp.ReadExisting()
    }

    if ($WatchSeconds -gt 0) {
        Write-Host "Watching $Port for $WatchSeconds s..." -ForegroundColor Cyan
        $end = (Get-Date).AddSeconds($WatchSeconds)
        while ((Get-Date) -lt $end) {
            $text = $sp.ReadExisting()
            if ($text) { Write-Host -NoNewline $text }
            Start-Sleep -Milliseconds 100
        }
        Write-Host ""
    }
}
finally {
    if ($sp.IsOpen) { $sp.Close() }
}
