# Supervisor the logon task runs: make sure the browser is up with CDP, then
# keep proxy.py alive (restart 10s after any exit, like launchd's KeepAlive).
# Runs under pythonw so no console window appears.
# Every 10s it also checks for a Brave that was opened the normal way (taskbar icon, a link from another
# app, an update) and so came up without remote control. If that Brave is under 90s old it's reopened
# with control on and its tabs restored; an older one is left alone so a window in use is never yanked.
$ErrorActionPreference = "Continue"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Dir = Split-Path -Parent $Here
$Log = Join-Path $Dir "launcher.log"
function Say($m) { Add-Content -Path $Log -Value ("[{0}] run-broker: {1}" -f (Get-Date -Format s), $m) }

$py = $env:BROKER_PYTHONW
if (-not $py) { $py = Get-Content (Join-Path $Here "pythonw.txt") -ErrorAction SilentlyContinue | Select-Object -First 1 }
if (-not $py) { $py = "pythonw.exe" }

$script:lastFix = [datetime]::MinValue
function Test-Cdp { try { Invoke-WebRequest "http://127.0.0.1:9229/json/version" -UseBasicParsing -TimeoutSec 2 | Out-Null; $true } catch { $false } }
function Guard-Browser {
  $main = Get-CimInstance Win32_Process -Filter "Name='brave.exe'" -ErrorAction SilentlyContinue |
          Where-Object { $_.CommandLine -notmatch '--type=' } | Sort-Object CreationDate -Descending | Select-Object -First 1
  if (-not $main -or (Test-Cdp)) { return }
  $age = ((Get-Date) - $main.CreationDate).TotalSeconds
  if ($age -gt 90 -or ((Get-Date) - $script:lastFix).TotalSeconds -lt 300) { return }
  $script:lastFix = Get-Date
  Say ("Brave opened without control ({0:N0}s old): reopening with it on" -f $age)
  & (Join-Path $Here "launch-browser.ps1") | Out-Null
}

& (Join-Path $Here "launch-browser.ps1") | Out-Null
while ($true) {
  Say "starting proxy.py with $py"
  $p = Start-Process -FilePath $py -ArgumentList "`"$(Join-Path $Dir 'proxy.py')`"" -WorkingDirectory $Dir `
        -RedirectStandardError (Join-Path $Dir "proxy.err") -PassThru -WindowStyle Hidden
  while (-not $p.HasExited) { Start-Sleep -Seconds 10; try { Guard-Browser } catch { Say "guard: $_" } }
  Say "proxy.py exited with code $($p.ExitCode); restarting in 10s"
  Start-Sleep -Seconds 10
}
