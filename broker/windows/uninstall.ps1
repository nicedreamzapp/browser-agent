# browser-broker - Windows uninstaller.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File broker\windows\uninstall.ps1 [-Purge]
#
# Stops and removes the "browser-broker" task ($env:BROKER_TASK) and stops the run-broker loop and
# proxy.py started from THIS folder. Your browser keeps running as it is (it just stops being
# brokered; CDP stays on 9229 until you restart it). The checkout is kept unless you pass -Purge,
# which deletes the whole browser-agent folder this script lives in.
param([switch]$Purge)
$ErrorActionPreference = "Continue"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Broker = Split-Path -Parent $Here
$Checkout = Split-Path -Parent $Broker
$TaskName = if ($env:BROKER_TASK) { $env:BROKER_TASK } else { "browser-broker" }

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
  Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
  Write-Output "removed task '$TaskName'"
} else { Write-Output "no task '$TaskName'" }

# The supervisor loop first (so it can't restart the proxy), then proxy.py, both matched by path.
$runner = Join-Path $Here "run-broker.ps1"
$proxy = Join-Path $Broker "proxy.py"
$procs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -and $_.ProcessId -ne $PID }
$n = 0
foreach ($target in @($runner, $proxy)) {
  $procs | Where-Object { $_.CommandLine.IndexOf($target, [StringComparison]::OrdinalIgnoreCase) -ge 0 } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; $n++ }
}
Write-Output "stopped $n broker process(es)"
Remove-Item (Join-Path $Here "pythonw.txt") -Force -ErrorAction SilentlyContinue

if ($Purge) {
  if (Test-Path (Join-Path $Checkout ".git")) {
    Set-Location $env:USERPROFILE
    Remove-Item $Checkout -Recurse -Force
    Write-Output "deleted $Checkout"
  } else { Write-Output "not a browser-agent checkout, left $Checkout alone" }
} else {
  Write-Output "kept $Checkout (run with -Purge to delete it)"
}
