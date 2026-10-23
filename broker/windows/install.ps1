# Install browser-broker on Windows as a Task Scheduler task that runs at logon,
# in your interactive session, and keeps proxy.py alive. Run from anywhere:
#   powershell -NoProfile -ExecutionPolicy Bypass -File windows\install.ps1
$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$TaskName = if ($env:BROKER_TASK) { $env:BROKER_TASK } else { "browser-broker" }

# Pick a Python 3.10+ and install the two deps into its user site.
$python = $env:BROKER_PYTHON
if (-not $python) { try { $python = (& py -3 -c "import sys; print(sys.executable)").Trim() } catch { } }
if (-not $python) { $python = (Get-Command python.exe -ErrorAction Stop).Source }
$pythonw = Join-Path (Split-Path -Parent $python) "pythonw.exe"
if (-not (Test-Path $pythonw)) { throw "no pythonw.exe next to $python" }
& $python -m pip install --user -q --disable-pip-version-check websockets websocket-client
Set-Content -Path (Join-Path $Here "pythonw.txt") -Value $pythonw -Encoding ASCII

$user = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
  -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$(Join-Path $Here 'run-broker.ps1')`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
  -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal `
  -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Write-Output "task '$TaskName' registered and started"

for ($i = 0; $i -lt 40; $i++) {
  Start-Sleep -Seconds 1
  try { $h = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 "http://127.0.0.1:9223/health"; Write-Output $h.Content; exit 0 } catch { }
}
Write-Output "broker did not answer on 9223 yet; see launcher.log and proxy.err"; exit 1
