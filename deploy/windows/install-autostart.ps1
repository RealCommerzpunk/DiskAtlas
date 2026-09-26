# Richtet DiskAtlas unter Windows als geplante Aufgabe ein, die bei der Anmeldung
# mit Administratorrechten startet (nötig für SMART über smartctl).
# Aufruf in einer Administrator-PowerShell im Projektverzeichnis:
#   powershell -ExecutionPolicy Bypass -File deploy\windows\install-autostart.ps1
$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$exe = Join-Path $root ".venv\Scripts\diskatlas.exe"
if (-not (Test-Path $exe)) { throw "Nicht gefunden: $exe - bitte zuerst installieren (siehe README)." }

$action = New-ScheduledTaskAction -Execute $exe -Argument "run" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -RunLevel Highest -LogonType Interactive
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName "DiskAtlas" -Action $action -Trigger $trigger `
  -Principal $principal -Settings $settings -Force | Out-Null
Write-Host "Aufgabe 'DiskAtlas' angelegt. Start jetzt mit: Start-ScheduledTask -TaskName DiskAtlas"
Write-Host "Dashboard: http://127.0.0.1:8765"
