# Windows equivalent of launch-browser.sh: start Brave (or Chrome) on your REAL
# profile with the flags browser-broker needs. If the browser already answers
# CDP on the upstream port, do nothing. If it is running WITHOUT CDP, close its
# windows gracefully (so the session is saved), then relaunch with the flags.
# See launch-browser.sh for why the --disable-*backgrounding* flags matter.
$ErrorActionPreference = "Continue"
$Port = if ($env:BROKER_UPSTREAM_PORT) { [int]$env:BROKER_UPSTREAM_PORT } else { 9229 }
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Log = Join-Path (Split-Path -Parent $Here) "launcher.log"
function Say($m) { $l = "[{0}] launch-browser: {1}" -f (Get-Date -Format s), $m; Write-Output $l; Add-Content -Path $Log -Value $l }

function Cdp-Up {
  try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 "http://127.0.0.1:$Port/json/version" | Out-Null; return $true } catch { return $false }
}

if (Cdp-Up) { Say "browser already answering CDP on $Port"; exit 0 }

$exe = $env:BROWSER_EXE
if (-not $exe) {
  $cands = @(
    "$env:LOCALAPPDATA\BraveSoftware\Brave-Browser\Application\brave.exe",
    "$env:ProgramFiles\BraveSoftware\Brave-Browser\Application\brave.exe",
    "${env:ProgramFiles(x86)}\BraveSoftware\Brave-Browser\Application\brave.exe",
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe")
  $exe = $cands | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
}
if (-not $exe) { Say "no brave.exe/chrome.exe found; set BROWSER_EXE"; exit 1 }
$name = [IO.Path]::GetFileNameWithoutExtension($exe)

# Running without CDP: close windows gracefully first, then whatever lingers.
$procs = Get-Process -Name $name -ErrorAction SilentlyContinue
if ($procs) {
  Say "$name is running without CDP; closing it to relaunch with the flags"
  for ($i = 0; $i -lt 20; $i++) {
    $win = Get-Process -Name $name -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 }
    if (-not $win) { break }
    $win | ForEach-Object { [void]$_.CloseMainWindow() }
    Start-Sleep -Milliseconds 750
  }
  Start-Sleep -Seconds 2
  Get-Process -Name $name -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
  Start-Sleep -Seconds 2
}

$flags = @("--remote-debugging-port=$Port", "--remote-allow-origins=*", "--restore-last-session",
           "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding",
           "--disable-background-timer-throttling")
Start-Process -FilePath $exe -ArgumentList $flags
for ($i = 0; $i -lt 30; $i++) {
  Start-Sleep -Seconds 1
  if (Cdp-Up) { Say "browser up with CDP on $Port ($exe)"; exit 0 }
}
Say "browser did not come up on $Port"; exit 1
