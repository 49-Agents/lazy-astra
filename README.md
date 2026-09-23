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

Every actor launch and resume explicitly uses `service_tier="default"` (normal
speed, not fast/priority), overriding inherited speed preferences.

Every actor launch and resume uses `danger-full-access` with approval policy
`never`, as required by the owner. This gives Luna filesystem and network access
without command approval prompts, including access to shared Git metadata.
Actor launches disable the interactive CLI update check so unattended startup
does not stop at an update menu. ACLA trusts the selected actor workspace for each launch
and resume by default, using a Codex CLI configuration override. This applies to
ACLA launches in any directory, including newly created worktrees. Trust allows
Codex to load that workspace's project configuration and instructions.

The override is scoped to the actor invocation; it does not edit your Codex
configuration or trust other directories for unrelated Codex sessions. Use
`--workspace-trust configured` to use your existing Codex trust settings and
interactive prompts instead. Login and hook trust remain separate and can
require owner attention. Full access does not authorize merges, deployments, or
other actions outside the assigned task.

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

Luna sends no interim progress reports or periodic updates. It sends one complete
report at the end of the assignment, and one at the end of each requested revision
round, then waits for Astra's review. Blockers, planning input, ambiguity, and
decisions go to Astra through `ask-question`; Luna waits for direction before
doing the affected work and does not decide independently. Final approval needs
no acknowledgement. The launcher includes this policy on initial launch and resume.

The launcher starts one watcher per SQLite store. The default interval is 300
seconds; a later run-start updates the shared interval. Multiple independent
actors use the same run UUID and goal, different names and worktrees. Their
session names contain complete run and actor UUIDs.

```bash
python3 /absolute/plugin/acla_cli.py send --thread-id '<review thread>' \
  --body-file /absolute/review.md --idempotency-key '<saved key>' [--reply-to ID]
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

An already running actor keeps its original permissions and trust settings.
After upgrading, the bound Astra can use `stop-actor --luna-id ID`, then repeat
the original `run-start` command using the updated helper. The launcher applies
full access and workspace trust before starting Codex; no terminal keystrokes are needed. Keep
the same state path, run ID, actor name, workspace, model, and handoff. A bound
actor resumes its saved conversation; an unbound actor receives bootstrap again.

Messages remain in read-only thread history. Delivery queues one small wake-up per
recipient across review threads; it directs the bound task to `inbox next`, where
full bodies are returned. Several pending messages coalesce into one wake-up.
Claims are recipient-bound to the current Codex thread and home, limited to 1–100
messages (default 20), and expire after 15 minutes. Acknowledge only processed IDs
with the returned token. A reply can use `--reply-to ID` to mark that incoming
message handled in the same transaction as sending; approval never consumes other
messages implicitly. Empty or delayed wake-ups must be silent.

Delivery claims are transactional, and the delivery worker is locked per store.
A successful queue call records transport acceptance, not model completion.
Ambiguous queue failures and interrupted dispatches become `uncertain`. Inspect
the exact destination before `resolve-notification --recipient-id ID --delivered`
or `--retry`, or `resolve-delivery --message-id ID --delivered` / `--retry` for
legacy message deliveries; automatic retries could duplicate work. Messages carry stable IDs so
agents can also recognize a repeated message. Offline/unbound actors keep their
messages pending. Legacy messages already in existing stores are marked legacy
during migration and are not automatically renotified, so migration cannot flood
old history. Reconcile an old queued envelope by ID with `inbox next --message-id ID`,
then acknowledge it normally. Legacy pending or safely claimed-but-not-dispatched
transport rows become eligible for coalesced wake-ups. Legacy uncertain rows remain
quarantined; `resolve-delivery --message-id ID --retry` explicitly makes one eligible.
No history or Codex queue rows are deleted.
Watcher diagnostics are visible in its tmux session.

```bash
python3 /absolute/plugin/acla_cli.py inbox next --limit 20
python3 /absolute/plugin/acla_cli.py inbox acknowledge --token '<claim token>' --message-id 123
```

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
