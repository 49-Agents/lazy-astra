# Astra Critic Luna Actor

A standalone Codex plugin for an Astra critic and GPT-6 Luna actors. Invoke the
single skill by saying **run the astra critic luna actor** after discussing your
issue with Astra. Astra prepares the handoff, launches actors in tmux, reviews
reports, and sends corrections until it approves the work.

Astra stays in the current Codex app or CLI task. Actors use tmux on the same
host. Full messages travel through `codex queue` to exact Codex task UUIDs in both
directions, with sender, run, review-thread, and message IDs. No external business platform service or
credentials are needed. There is no terminal-paste delivery.

## Requirements

- Python 3.10+, tmux, and an authenticated Codex CLI with `codex queue` support
  (developed against CLI 0.155.1).
- `gpt-6-luna` access, or an explicitly selected `--luna-model`.
- Astra and actors must be reachable by the same host's Codex queue facility.
  This version is not a cross-machine inbox service.

The actor uses workspace-write with on-request approvals and write access to the
private inbox directory. Login, workspace trust, or other approval prompts can
require owner attention. The plugin does not bypass them.

## CLI example

Run from the Astra Codex task. `CODEX_THREAD_ID` supplies its real identity. Use
`--astra-thread UUID` only when you already know the exact critic task ID.

```bash
# Generate and save this once before launch; keep it for retries.
python3 -c 'import uuid; print(uuid.uuid4())'
python3 /absolute/plugin/acla_cli.py run-start \
  --run-id '<saved UUID>' --goal 'Implement the parser change' \
  --workspace '/absolute/actor/worktree' --luna-name 'parser' \
  --handoff-file '/absolute/handoff.md' --interval 300
```

The handoff is part of the initial CLI prompt. The actor registers its own native
Codex thread using `bind-session`, then works and uses `send-reply` or
`ask-question` to contact only its bound critic. A process being launched is not
proof of readiness: inspect `status` and the returned tmux session if binding
has not completed.

The launcher starts one watcher per SQLite store. The default interval is 300
seconds; a later run-start updates the shared interval. Multiple independent
actors use the same run UUID and goal, different names and worktrees. Their
session names contain complete run and actor UUIDs.

```bash
python3 /absolute/plugin/acla_cli.py send --thread-id '<review thread>' \
  --body-file /absolute/review.md --idempotency-key '<saved key>'
python3 /absolute/plugin/acla_cli.py approve --thread-id '<review thread>' \
  --body-file /absolute/approval.md --idempotency-key '<saved key>'
python3 /absolute/plugin/acla_cli.py status --run-id '<saved UUID>'
python3 /absolute/plugin/acla_cli.py messages --thread-id '<review thread>'
```

Approval queues `ASTRA_APPROVED`. The run becomes approved after every actor's
workstream is approved. Nothing is automatically merged or deployed.

## Persistence and recovery

State defaults to `~/.astra-critic-luna-actor/state.sqlite3`, configurable through
`--state` or `ACLA_STATE`. The store is private local coordination, not a security
boundary between programs running as the same OS user. It includes full handoffs
and message bodies; keep it out of Git and shared folders.

Repeat the same run-start arguments to reuse the saved identities, restart the
watcher if absent, and resume the actor's **exact saved Codex conversation**.
Changed handoffs, models, or destinations under the same actor are rejected.
The original unbound actor receives bootstrap again if its process disappeared.
`stop-actor --luna-id ID` stops only that actor's owned tmux session and retains
its conversation. The optional `ACLA_TMUX_SOCKET` selects the isolated tmux server
(default `acla`).

Delivery claims are transactional, and the delivery worker is locked per store.
A successful queue call records transport acceptance, not model completion.
Ambiguous queue failures and interrupted dispatches become `uncertain`. Inspect
the exact destination before `resolve-delivery --message-id ID --delivered` or
`--retry`; automatic retries could duplicate work. Messages carry stable IDs so
agents can also recognize a repeated message. Offline/unbound actors keep their
messages pending. Watcher diagnostics are visible in its tmux session.

## Installation and checks

This repository is an installable Codex plugin with one skill in `skills/`.
Use the Codex Plugin Creator personal-marketplace workflow; after reinstalling,
start a new task to load the new skill. The skill resolves helpers from its own
installed plugin directory, so it works independently of a source checkout.

```bash
python3 -m unittest discover -s tests -v
python3 /path/to/plugin-creator/scripts/validate_plugin.py .
```

See `docs/VERIFICATION.md` for the evidence and remaining runtime limits from the
latest end-to-end check.
