# Starts CryptoSignal AI automatically one minute after you log in to Windows.
# Install:  powershell -ExecutionPolicy Bypass -File D:\botass\install-autostart.ps1
# Remove:   powershell -ExecutionPolicy Bypass -File D:\botass\install-autostart.ps1 -Remove
param([switch]$Remove)

$name = "CryptoSignal AI"
if ($Remove) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Autostart removed. The running application was not stopped."
    return
}

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$PSScriptRoot\start.ps1`"" `
    -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$trigger.Delay = "PT1M"  # lets the network come up first
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
    -Description "Starts the CryptoSignal AI backend and dashboard at login. Paper trading and alerts only." -Force | Out-Null
Write-Host "Autostart installed: '$name' runs one minute after login."
