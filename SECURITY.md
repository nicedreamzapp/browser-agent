# Security policy

## Reporting a vulnerability

Please do not open a public issue for security problems.

Report them privately through GitHub: go to the **Security** tab of this repo and click **Report a vulnerability** ([direct link](https://github.com/nicedreamzapp/browser-agent/security/advisories/new)). Only the maintainer sees the report.

Helpful details: what you found, how to reproduce it, which version or commit, your OS, browser and browser version, and whether it is in the agent or the broker.

I aim to reply within 3 days and to ship a fix or a clear plan within 30 days, depending on severity. You will be credited in the advisory unless you ask not to be.

## Supported versions

Fixes go into the latest code on `main`. Please update before reporting if you are on an older version.

## What matters most here

Both halves work inside your real, logged-in browser, so the parts where a mistake could hurt someone:

- the broker proxy in `broker/proxy.py`, which listens on `127.0.0.1` ports 9222 and 9223 and keeps agents out of your tabs and each other's
- `broker/launch-browser.sh` and `broker/windows/launch-browser.ps1`, which start your browser with remote debugging on, and `broker/get.sh`, which people run with one `curl | bash` line
- the installers `broker/install.sh` and `broker/windows/install.ps1`, which set up a LaunchAgent or a scheduled task
- the prompt injection guard (`guard_check`) in `agent.py`, which decides when page text may lead to shell, file, click and phone actions
- the `shell`, `write_file` and `js` tools in `agent.py`
