# Adona Style Generator: share the app from this PC on the internet through a free
# Cloudflare quick tunnel. No account or card needed. The link works while this window is open.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $root "backend\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "The app isn't installed yet. Run start.bat once first, then try again." -ForegroundColor Yellow
    exit 1
}

# Cloudflare's tunnel client (official release), downloaded once.
$bin = Join-Path $root "bin"
New-Item -ItemType Directory -Force $bin | Out-Null
$cf = Join-Path $bin "cloudflared.exe"
if (-not (Test-Path $cf)) {
    Write-Host "Downloading Cloudflare Tunnel (one time)..."
    Invoke-WebRequest -UseBasicParsing -OutFile $cf `
        -Uri "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
}

# Public mode: visitors only see their own songs; uploads live in data-public (your own songs in
# data\ are untouched) and are removed after 7 days; Cloudflare's free tier caps uploads at 100 MB.
$env:LAK_PUBLIC = "1"
$env:LAK_RETENTION_DAYS = "7"
$env:LAK_DATA_DIR = Join-Path $root "data-public"
$env:LAK_MAX_UPLOAD_MB = "95"
$port = 8766
$logs = Join-Path $root "data-public\logs"
New-Item -ItemType Directory -Force $logs | Out-Null

$server = $null
$tunnel = $null
try {
    Write-Host "Starting the app..."
    $server = Start-Process -FilePath $py -WorkingDirectory $root -PassThru -NoNewWindow `
        -ArgumentList "-m", "uvicorn", "app.main:app", "--app-dir", "backend", "--host", "127.0.0.1", "--port", "$port" `
        -RedirectStandardOutput (Join-Path $logs "server.out.log") -RedirectStandardError (Join-Path $logs "server.err.log")
    $ok = $false
    for ($i = 0; $i -lt 60 -and -not $ok; $i++) {
        Start-Sleep -Milliseconds 500
        try { $ok = (Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$port/api/health" -TimeoutSec 2).StatusCode -eq 200 } catch { }
    }
    if (-not $ok) { throw "The app didn't start. See data-public\logs\server.err.log" }

    Write-Host "Opening the public tunnel..."
    $tunnelLog = Join-Path $logs "tunnel.log"
    if (Test-Path $tunnelLog) { Remove-Item $tunnelLog }
    $tunnel = Start-Process -FilePath $cf -PassThru -NoNewWindow `
        -ArgumentList "tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:$port", "--logfile", $tunnelLog `
        -RedirectStandardError (Join-Path $logs "tunnel.err.log") -RedirectStandardOutput (Join-Path $logs "tunnel.out.log")
    $url = $null
    for ($i = 0; $i -lt 90 -and -not $url; $i++) {
        Start-Sleep -Seconds 1
        foreach ($f in @($tunnelLog, (Join-Path $logs "tunnel.err.log"))) {
            if (Test-Path $f) {
                $m = Select-String -Path $f -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" | Select-Object -First 1
                if ($m) { $url = $m.Matches[0].Value }
            }
        }
    }
    if (-not $url) { throw "Cloudflare didn't return a link. Check your internet connection and try again." }

    try { Set-Clipboard $url } catch { }
    Write-Host ""
    Write-Host "  Your app is online:  $url" -ForegroundColor Green
    Write-Host "  (copied to the clipboard; share it with anyone)"
    Write-Host ""
    Write-Host "  Keep this window open. The link stops working when you close it or the PC sleeps,"
    Write-Host "  and you get a new link each time you run host.bat."
    Write-Host ""
    Start-Process $url
    Read-Host "Press Enter to stop sharing"
}
finally {
    foreach ($p in @($tunnel, $server)) {
        if ($p -and -not $p.HasExited) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue }
    }
    Write-Host "Sharing stopped."
}
