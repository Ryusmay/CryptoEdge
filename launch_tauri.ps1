$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$terminal = Join-Path $root "CryptoEdge_Terminal.exe"
$statusLog = Join-Path $root "logs\launcher_status.log"
$engine = $null
$terminalProcess = $null

function Write-LaunchStatus([string]$message) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $statusLog) | Out-Null
    Add-Content -LiteralPath $statusLog -Value "$(Get-Date -Format o) $message" -Encoding UTF8
}

function Test-CryptoEdgeApi {
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:47821/api/health" -TimeoutSec 1
        return ($health -and ($health.app -eq "CryptoEdge" -or $health.ok -eq $true))
    } catch {
        return $false
    }
}

Write-LaunchStatus "native tauri launcher start"
if (-not (Test-Path -LiteralPath $terminal)) {
    throw "Brak CryptoEdge_Terminal.exe. Przebuduj frontend Tauri."
}

try {
    if (-not (Test-CryptoEdgeApi)) {
        $python = Join-Path $root ".venv\Scripts\pythonw.exe"
        if (-not (Test-Path -LiteralPath $python)) {
            $python = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
        }
        if (-not $python) {
            $python = (Get-Command python.exe -ErrorAction Stop).Source
        }
        $engine = Start-Process -FilePath $python `
            -ArgumentList @((Join-Path $root "app.py"), "--web-ui") `
            -WorkingDirectory $root -WindowStyle Hidden -PassThru

        $ready = $false
        for ($attempt = 0; $attempt -lt 240; $attempt++) {
            Start-Sleep -Milliseconds 250
            if (Test-CryptoEdgeApi) {
                $ready = $true
                break
            }
            if ($engine.HasExited) { break }
        }
        if (-not $ready) {
            throw "Silnik CryptoEdge nie uruchomił lokalnego API w ciągu 60 sekund."
        }
        Write-LaunchStatus "backend ready pid=$($engine.Id)"
    } else {
        Write-LaunchStatus "existing backend accepted"
    }

    $terminalProcess = Start-Process -FilePath $terminal -WorkingDirectory $root -PassThru
    Write-LaunchStatus "tauri native pid=$($terminalProcess.Id)"
    Wait-Process -Id $terminalProcess.Id
    Write-LaunchStatus "tauri native closed"
} finally {
    if ($engine -and -not $engine.HasExited) {
        Stop-Process -Id $engine.Id -ErrorAction SilentlyContinue
        Write-LaunchStatus "owned backend stopped"
    }
}
