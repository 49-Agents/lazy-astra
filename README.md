# Astra Critic Luna Actor

Standalone local coordination for an Astra critic and one or more Luna actor
sessions. It does not require external business platform.

The quick version stores runs, agent mappings, threads, and full message bodies
in a private SQLite file at `~/.astra-critic-luna-actor/state.sqlite3`. Luna
sessions run in a dedicated tmux socket (`acla` by default). A background watcher
delivers pending messages every five minutes by default.

## Quick start

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
acla init
acla run-start --goal "Implement the requested change" --workspace "$PWD" --luna-name Luna-1
```

The Luna command defaults to `codex`; override it
with `--command 'codex'` or `ACLA_LUNA_COMMAND`.

Send a handoff or review message with `acla send --thread-id ... --sender-id ...`
and run `acla watch` in a long-lived terminal. Set `--interval` to change the
polling interval in seconds.

The Codex plugin is in `.codex-plugin/` and has one skill under `skills/`.
