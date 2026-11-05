"""Static checks on the installer and service files, so a typo there is caught
before it reaches someone's login items."""
import glob
import os
import plistlib
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHELL_SCRIPTS = sorted(glob.glob(os.path.join(ROOT, "*.sh"))) + [os.path.join(ROOT, n) for n in
                                                                  ("bb-show", "bb-close", "bb-peek")]
PLIST = os.path.join(ROOT, "launchd", "com.browser-broker.plist")
REAP_PLIST = os.path.join(ROOT, "launchd", "com.browser-broker.desktop-reap.plist")


@pytest.mark.skipif(not shutil.which("bash"), reason="needs bash")
@pytest.mark.parametrize("script", SHELL_SCRIPTS, ids=os.path.basename)
def test_shell_scripts_parse(script):
    p = subprocess.run(["bash", "-n", script], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr


@pytest.mark.parametrize("path", ["bb-show", "bb-close", "bb-peek", "bb-click", "bb-desktop", "get.sh", "install.sh", "install-desktop-reap.sh", "launch-browser.sh",
                                  "brave-cdp-guard.sh", "uninstall.sh"])
def test_scripts_are_executable_in_git(path):
    # ROOT may be a subfolder of the checkout (broker/ inside browser-agent), so ask git
    # instead of looking for ROOT/.git.
    if not shutil.which("git") or subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=ROOT,
                                                  capture_output=True, text=True).stdout.strip() != "true":
        pytest.skip("not a git checkout")
    out = subprocess.run(["git", "ls-files", "-s", path], cwd=ROOT, capture_output=True,
                         text=True).stdout
    assert out.startswith("100755"), out


def test_launchd_plist_is_well_formed():
    with open(PLIST, "rb") as f:
        p = plistlib.load(f)
    assert p["Label"] == "__LABEL__"   # install.sh fills in BROKER_LABEL (default com.browser-broker)
    assert any(a.endswith("proxy.py") for a in p["ProgramArguments"])


@pytest.mark.skipif(sys.platform != "darwin", reason="plutil and launchd are macOS only")
@pytest.mark.parametrize("plist", [PLIST, REAP_PLIST], ids=os.path.basename)
def test_launchd_plist_lints_on_macos(plist):
    p = subprocess.run(["plutil", "-lint", plist], capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr


def test_desktop_reap_plist_runs_reap_every_5_minutes():
    with open(REAP_PLIST, "rb") as f:
        p = plistlib.load(f)
    assert p["ProgramArguments"][-2:] == ["__BROKER_DIR__/bb-desktop", "reap"]
    assert p["StartInterval"] == 300
