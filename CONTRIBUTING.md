# Contributing

Thanks for taking a look. The agent and the broker run against real sites and real browsers I
can't all test, so a clear bug report is useful even if you never send code.

## Reporting a problem

Open an [issue](https://github.com/nicedreamzapp/browser-agent/issues/new/choose). The things
that make it quick to fix:

- which half: the agent (`agent.py`) or the broker (`broker/`)
- your OS, browser and browser version, and Python version
- for the agent, the model the MLX server was running and the task you gave it
- for the broker, the tool you pointed at it (Playwright, chrome-devtools-mcp, your own script)
  and what `curl localhost:9223/status` prints
- the site, and the last lines of output

## Running it locally

The broker on macOS, by hand:

```bash
git clone https://github.com/nicedreamzapp/browser-agent && cd browser-agent/broker
pip3 install websockets websocket-client
./launch-browser.sh   # your real profile, CDP on :9229
./install.sh          # LaunchAgent for proxy.py: :9222 for tools, :9223 lease API
curl localhost:9223/status
```

The agent needs Apple Silicon and the MLX server from
[claude-code-local](https://github.com/nicedreamzapp/claude-code-local):

```bash
pip install websockets
python agent.py
```

Windows and Linux steps for the broker are in the README.

## Tests

Two suites, one per half. Neither needs a browser, a model or the network.

```bash
pip install -r requirements-dev.txt && python -m pytest                  # the agent
cd broker && pip install -r requirements-dev.txt && python -m pytest     # the broker
```

CI runs both. Neither drives a real Chrome, so for changes to how tabs are shown, closed or
watched for human input, also try it by hand against your real browser.

## Pull requests

Small and focused is easiest to review. For big changes, open an issue first so we can talk it
through. A change to one of the five rules should come with a test in `tests/test_five_rules.py`
or `broker/tests/test_five_rules.py`. Say which OS and browser you tested on.

Please follow the [code of conduct](CODE_OF_CONDUCT.md).
