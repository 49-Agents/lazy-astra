# Install Lazy Astra

Lazy Astra is the repository name. The compatible plugin/package identifier is
`astra-critic-luna-actor`; the command and invocation remain `acla` and `/ACLA`.

## Requirements

Use Linux or macOS with Python 3.10+, Git and tmux. Windows requires a compatible
Linux environment such as WSL. Authenticate Codex and check `codex queue --help`;
this integration requires that command. Authenticate Claude Code separately if
using the Claude executor. Model availability depends on your account/provider.
Workers run with full filesystem/network access and without approval prompts.
Only assign work in directories and repositories you trust.

## Source and standalone CLI

```sh
git clone https://github.com/49-Agents/lazy-astra.git
cd lazy-astra
python3 acla_cli.py --help
```

For an isolated Python installation:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/acla --help
```

The Python package includes the launcher helper used by Workers and watchers.
It does not install Codex skills; use plugin setup below for `/ACLA`. Python
installation alone does not authenticate either executor or start any Workers.

## Codex plugin

Register the checkout in your personal marketplace at
`~/.agents/plugins/marketplace.json`. Preserve existing entries. For a new
marketplace the complete file is:

```json
{
  "name": "personal",
  "interface": {"displayName": "Personal"},
  "plugins": [{
    "name": "astra-critic-luna-actor",
    "source": {"source": "local", "path": "./plugins/astra-critic-luna-actor"},
    "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
    "category": "Productivity"
  }]
}
```

For the personal marketplace, this source path resolves under your home directory.
From your checkout, create the source link (if one already exists, inspect it
instead of overwriting it):

```sh
mkdir -p ~/plugins
ln -s "$PWD" ~/plugins/astra-critic-luna-actor
codex plugin add astra-critic-luna-actor@personal
```

If your marketplace already has another name, use that name instead of
`personal`. If ACLA already exists there, reuse its configured source location.
Start a new Codex task and say `run default ACLA loop for this task`.
Manager writes the plan, launches the Worker, and yields until a completion or
blocker notification arrives. Do not close the Worker or watcher tmux sessions.

## Configure your own defaults

Store private settings in `~/.astra-critic-luna-actor/local.json` or point
`ACLA_LOCAL_CONFIG` at a JSON file. For example, use your normal Codex model
with no internal self-review:

```json
{"defaults": {"executor_backend": "codex", "review_loop": false}}
```

Without local overrides, the package selects Claude Code Sonnet 5.5 at xhigh
and enables two Sonnet reviewers. Override the model with `--worker-model`
if your provider uses a different identifier; there is no silent fallback.
Existing Workers preserve their saved settings. Consult the README for explicit
backend/model overrides, watching terminals, stopping Workers and recovery.

## Upgrade

Update your source checkout, then reinstall using the same marketplace selector.
For a development reinstall, give `.codex-plugin/plugin.json` a fresh build suffix
(e.g. `0.1.0+local.2`) so the installed cache is refreshed. Start a new Manager task.
Already-running Workers/watchers retain old code until their documented controlled
restart. Preserve their state directory and native conversations.

## Privacy and limits

SQLite inboxes, handoffs, local settings and raw telemetry stay on your machine.
Never commit them. Same-user programs can access this state; it is not a security
boundary. Review prompts are instructions, not proof of correct implementation.
The project does not claim exactly-once execution, measured cost savings or
quality parity. See [verification](VERIFICATION.md) for the scope of earlier
runtime checks; those are historical evidence, not validation of every release.
