<#
.SYNOPSIS
    One-command launcher for EduVoice AI:
    1) Reads Wi-Fi credentials from local gitignored wifi_local.json
    2) Connects AiPi-CAM-D200 over COM port, verifies it gets a real IP, sets AT+HOST & AT+VOL
    3) Starts the FastAPI host (bridge.py) on port 8000 if not already running
#>
param(
    [string]$Port = "",
    [int]$Volume = 0,
    [switch]$ConnectOnly
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ConfigPath = Join-Path $ScriptDir "wifi_local.json"
$AiDir = "D:\G12_Capstone\ai"

if (-not (Test-Path $ConfigPath)) {
    Write-Host "ERROR: Missing $ConfigPath" -ForegroundColor Red
    Write-Host 'Create it with: {"ssid": "YourWifi", "password": "YourPassword", "port": "COM15", "host_port": 8000, "volume": 20}'
    exit 1
}

$cfg = Get-Content $ConfigPath -Raw | ConvertFrom-Json
$ssid = [string]$cfg.ssid
$pass = [string]$cfg.password
$comPort = if ($Port) { $Port } elseif ($cfg.port) { [string]$cfg.port } else { "" }
$hostPort = if ($cfg.host_port) { [int]$cfg.host_port } else { 8000 }
$vol = if ($Volume -gt 0) { $Volume } elseif ($cfg.volume) { [int]$cfg.volume } else { 20 }

# 1. Auto-detect available COM port if configured one isn't present
$availPorts = [System.IO.Ports.SerialPort]::GetPortNames()
if (-not $availPorts -or $availPorts.Count -eq 0) {
    Write-Host "ERROR: No COM ports found. Plug in the AiPi-CAM-D200 USB cable and try again." -ForegroundColor Red
    exit 1
}
if (-not $comPort -or ($availPorts -notcontains $comPort)) {
    $comPort = $availPorts[0]
    Write-Host "Using detected COM port: $comPort" -ForegroundColor Yellow
}

# 2. Detect laptop's active Wi-Fi/LAN IPv4 address (prefers the default route interface)
$laptopIp = ""
try {
    $sock = New-Object System.Net.Sockets.Socket(
        [System.Net.Sockets.AddressFamily]::InterNetwork,
        [System.Net.Sockets.SocketType]::Dgram,
        [System.Net.Sockets.ProtocolType]::Udp
    )
    $sock.Connect("8.8.8.8", 80)
    $laptopIp = $sock.LocalEndPoint.Address.ToString()
    $sock.Close()
} catch {
    $laptopIp = (Get-NetIPAddress -AddressFamily IPv4 |
        Where-Object { $_.IPAddress -like "192.168.*" -or $_.IPAddress -like "10.*" } |
        Select-Object -First 1 -ExpandProperty IPAddress)
}
if (-not $laptopIp) {
    Write-Host "ERROR: Could not detect laptop Wi-Fi IPv4 address. Make sure laptop Wi-Fi is connected." -ForegroundColor Red
    exit 1
}
Write-Host "Laptop host IP: ${laptopIp}:${hostPort}" -ForegroundColor Green

function Send-BoardAT {
    param(
        [System.IO.Ports.SerialPort]$Serial,
        [string]$Cmd,
        [int]$WaitMs = 1500
    )
    $Serial.ReadExisting() | Out-Null
    foreach ($ch in ($Cmd + "`r`n").ToCharArray()) {
        $Serial.Write([string]$ch)
        Start-Sleep -Milliseconds 3
    }
    Start-Sleep -Milliseconds $WaitMs
    return $Serial.ReadExisting()
}

# 3. Connect board to Wi-Fi and configure AT+HOST & AT+VOL
$boardIp = ""
$sp = New-Object System.IO.Ports.SerialPort $comPort, 115200, 'None', 8, 'One'
try {
    $sp.Open()
    Write-Host "Connected to $comPort. Checking current Wi-Fi IP..." -ForegroundColor Cyan
    $resp = Send-BoardAT -Serial $sp -Cmd "AT+WJAP?" -WaitMs 800
    if ($resp -match "(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})" -and $Matches[1] -ne "0.0.0.0") {
        $boardIp = $Matches[1]
        Write-Host "Board already on Wi-Fi: $boardIp" -ForegroundColor Green
    } else {
        Write-Host "Joining Wi-Fi '$ssid'..." -ForegroundColor Cyan
        $joinOut = Send-BoardAT -Serial $sp -Cmd "AT+WJAP=$ssid,$pass" -WaitMs 2500
        if ($joinOut.Trim()) { Write-Host $joinOut.Trim() -ForegroundColor DarkGray }

        for ($i = 1; $i -le 8; $i++) {
            Start-Sleep -Milliseconds 1500
            $asyncLog = $sp.ReadExisting()
            if ($asyncLog -match "Bouffalolab|Starting wifi") {
                Write-Host "ERROR: Board REBOOTED (power brownout) when Wi-Fi turned on!" -ForegroundColor Red
                Write-Host "Power the board from a strong 5V USB-C cable / 5V 2A supply (not only the CP2102 5V pin)." -ForegroundColor Yellow
                exit 1
            }
            $check = Send-BoardAT -Serial $sp -Cmd "AT+WJAP?" -WaitMs 700
            if ($check -match "(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})" -and $Matches[1] -ne "0.0.0.0") {
                $boardIp = $Matches[1]
                break
            }
            Write-Host "  waiting for IP ($i/8)..." -ForegroundColor DarkGray
        }
    }

    if (-not $boardIp) {
        Write-Host "ERROR: Board did not get a Wi-Fi IP (still 0.0.0.0)." -ForegroundColor Red
        Write-Host "Check that '$ssid' is a 2.4 GHz network and the password in wifi_local.json is exact." -ForegroundColor Yellow
        exit 1
    }
    Write-Host "Board Wi-Fi connected! IP = $boardIp" -ForegroundColor Green

    $hostResp = Send-BoardAT -Serial $sp -Cmd "AT+HOST=$laptopIp,$hostPort" -WaitMs 800
    Write-Host "Set board host -> ${laptopIp}:${hostPort}" -ForegroundColor Green
    if ($hostResp.Trim()) { Write-Host $hostResp.Trim() -ForegroundColor DarkGray }

    $volResp = Send-BoardAT -Serial $sp -Cmd "AT+VOL=$vol" -WaitMs 800
    Write-Host "Set board volume -> $vol%" -ForegroundColor Green
    if ($volResp.Trim()) { Write-Host $volResp.Trim() -ForegroundColor DarkGray }
}
finally {
    if ($sp.IsOpen) { $sp.Close() }
}

# Save board IP for ai/camera.py too
$camCfg = Join-Path $AiDir "camera_config.json"
if (Test-Path $AiDir) {
    @{ ip = $boardIp } | ConvertTo-Json | Set-Content -Path $camCfg -Encoding UTF8
}

if ($ConnectOnly) {
    Write-Host "Done (ConnectOnly mode)." -ForegroundColor Green
    exit 0
}

# 4. Start FastAPI host (bridge.py) if port 8000 is not already listening
$listening = Get-NetTCPConnection -LocalPort $hostPort -State Listen -ErrorAction SilentlyContinue
if ($listening) {
    Write-Host ""
    Write-Host "EduVoice host is ALREADY running on http://${laptopIp}:${hostPort}" -ForegroundColor Green
    Write-Host "Prototype is READY! Press and HOLD the button while speaking, then release." -ForegroundColor Cyan
    exit 0
}

Write-Host ""
Write-Host "Starting EduVoice AI host on http://${laptopIp}:${hostPort} ..." -ForegroundColor Cyan
Write-Host "(Wait for 'Application startup complete', then press and HOLD the button!)" -ForegroundColor Yellow
Set-Location $AiDir
python -m uvicorn bridge:app --host 0.0.0.0 --port $hostPort
