# Stops CryptoSignal AI (backend on port 8000, dashboard on port 3000). Telegram alerts stop too.
# Run:  powershell -ExecutionPolicy Bypass -File D:\botass\stop.ps1
foreach ($port in 8000, 3000) {
    $c = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($c) {
        Stop-Process -Id $c.OwningProcess -Force -Confirm:$false
        Write-Host "Stopped process on port $port"
    }
}
Get-CimInstance Win32_Process -Filter "Name='node.exe'" |
    Where-Object { $_.CommandLine -like "*$PSScriptRoot\frontend*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -Confirm:$false -ErrorAction SilentlyContinue }
Write-Host "CryptoSignal AI stopped."
