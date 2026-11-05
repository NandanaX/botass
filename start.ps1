# Starts CryptoSignal AI in the background (hidden windows). Logs: D:\botass-backend.log, D:\botass-frontend.log
# Run:  powershell -ExecutionPolicy Bypass -File D:\botass\start.ps1
$root = $PSScriptRoot
$logs = Split-Path $root -Parent
if (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) {
    Write-Host "Backend already running on port 8000"
} else {
    # Keep the previous run's logs: redirecting output overwrites them, which would erase the evidence of a crash.
    foreach ($log in "botass-backend.log", "botass-backend.err.log") {
        if (Test-Path "$logs\$log") { Move-Item "$logs\$log" "$logs\$log.prev" -Force -ErrorAction SilentlyContinue }
    }
    Start-Process -FilePath "$root\backend\.venv\Scripts\python.exe" `
        -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000" `
        -WorkingDirectory "$root\backend" -WindowStyle Hidden `
        -RedirectStandardOutput "$logs\botass-backend.log" -RedirectStandardError "$logs\botass-backend.err.log"
    Write-Host "Backend started  -> http://localhost:8000 (scanner + Telegram alerts)"
}
if (Get-NetTCPConnection -LocalPort 3000 -State Listen -ErrorAction SilentlyContinue) {
    Write-Host "Dashboard already running on port 3000"
} else {
    Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "npm run dev" -WorkingDirectory "$root\frontend" `
        -WindowStyle Hidden -RedirectStandardOutput "$logs\botass-frontend.log" `
        -RedirectStandardError "$logs\botass-frontend.err.log"
    Write-Host "Dashboard started -> http://localhost:3000"
}
