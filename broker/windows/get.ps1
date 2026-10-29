# browser-broker - Windows one-line installer.
#
#   irm https://raw.githubusercontent.com/nicedreamzapp/browser-agent/main/broker/windows/get.ps1 | iex
#
# Clones (or updates) github.com/nicedreamzapp/browser-agent into %USERPROFILE%\browser-agent
# (or $env:BROWSER_BROKER_DIR), then runs broker\windows\install.ps1 from it. That installs the two
# Python deps and the "browser-broker" logon task, which relaunches your browser with CDP on 9229
# and owns 9222. If a broker already answers on 9223 ($env:BROKER_PORT), nothing is touched.
# Uninstall: broker\windows\uninstall.ps1 in that folder.

function Install-BrowserBroker {
  $ErrorActionPreference = "Stop"
  $dir = if ($env:BROWSER_BROKER_DIR) { $env:BROWSER_BROKER_DIR } else { Join-Path $env:USERPROFILE "browser-agent" }
  $port = if ($env:BROKER_PORT) { $env:BROKER_PORT } else { "9223" }
  Write-Host ""
  Write-Host "== browser-broker - Windows installer =="

  # Never stand up a second broker on top of a working one.
  try {
    $h = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 "http://127.0.0.1:$port/health"
    Write-Host "-> A broker already answers on $port; nothing to do."
    Write-Host $h.Content
    return
  } catch { }

  if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw "git not found. Install Git for Windows from https://git-scm.com/download/win" }
  if (Test-Path (Join-Path $dir ".git")) {
    Write-Host "-> Updating $dir"
    git -C $dir pull --ff-only --quiet
    if ($LASTEXITCODE -ne 0) { Write-Host "   (local changes kept, skipping the pull)" }
  } elseif (Test-Path $dir) {
    throw "$dir exists and is not a git checkout. Move it aside or set BROWSER_BROKER_DIR."
  } else {
    Write-Host "-> Cloning into $dir"
    git clone --quiet --depth 1 https://github.com/nicedreamzapp/browser-agent $dir
    if ($LASTEXITCODE -ne 0) { throw "git clone failed" }
  }

  # A child PowerShell, so the installer's own "exit" can never close this window.
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $dir "broker\windows\install.ps1")
  if ($LASTEXITCODE -ne 0) { Write-Host "-> install.ps1 exited $LASTEXITCODE (see broker\launcher.log)" }
  Write-Host "Uninstall: powershell -ExecutionPolicy Bypass -File `"$(Join-Path $dir 'broker\windows\uninstall.ps1')`""
}

Install-BrowserBroker
