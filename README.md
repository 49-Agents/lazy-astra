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
# Run this from Astra's tmux session, or set ACLA_ASTRA_SESSION explicitly.
acla run-start --goal "Implement the requested change" --workspace "$PWD" --luna-name Luna-1
```

The Luna command defaults to `codex --model gpt-5.6-sol`; override it with
`--luna-model` or an explicit `--command`.

`run-start` binds the current Astra tmux session, creates a unique Luna session,
starts the watcher automatically, and bootstraps Luna with its IDs plus the
working `send-reply` and `ask-question` commands. Send a handoff with
`acla send --thread-id ... --sender-id ...`; Luna replies with
`acla send-reply --luna-id ...`. Set `--interval` to change polling seconds.

The Codex plugin is in `.codex-plugin/` and has one skill under `skills/`.
