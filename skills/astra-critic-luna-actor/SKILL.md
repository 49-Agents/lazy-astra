---
name: "astra-critic-luna-actor"
description: "Run the Astra critic and Luna actor workflow when the user says ‘run the astra critic luna actor’. Uses this repository's private SQLite inbox, one-Astra/one-Luna mappings, tmux launcher, and five-minute message delivery."
metadata:
  short-description: "Coordinate Astra reviews and Luna work locally."
---

# Astra Critic Luna Actor

Invoke this skill when the user says **run the astra critic luna actor**. Match
the phrase case-insensitively. This skill is self-contained and does not use
external business platform, external business platform profiles, business roles, or a server.

The repository root is the directory containing this skill. Set:

```bash
ACLA_ROOT="${ACLA_ROOT:-$HOME/astra-critic-luna-actor}"
ACLA="python3 $ACLA_ROOT/acla_cli.py"
```

The local store defaults to `~/.astra-critic-luna-actor/state.sqlite3`. Keep it
private. The delivery interval defaults to 300 seconds and is configurable.

## Workflow

1. Act as Astra. Turn the user's issue into a thorough handoff: goal, non-goals,
   repository/worktree and base branch, constraints, ordered steps, acceptance
   criteria, verification commands, risks, and recovery notes.
2. Split into independent workstreams only when their files and workspaces do not
   conflict. Give each stream a stable slug and Luna name.
3. Start one Luna per stream. Each Luna must have exactly one Astra mapping and
   exactly one thread. Run this from Astra's tmux session, or set
   `ACLA_ASTRA_SESSION` and `ACLA_ASTRA_TMUX_SOCKET` first. Use the local launcher:

   ```bash
   $ACLA run-start --goal "<short goal>" --workspace "<luna workspace>" \
     --astra-name "Astra Critic" --luna-name "<stream Luna name>"
   ```

   Save the returned `run_id`, `astra_id`, `luna_id`, `thread_id`, and
   `tmux_session`, `luna_model`, and `watcher`. The default Luna command is
   `codex --model gpt-5.6-sol`; pass `--luna-model` or `--command` when needed.
   Startup is idempotent for a supplied `--run-id`; it reuses the saved Astra,
   Luna, thread, and unique session instead of creating replacements.
4. Send the complete handoff as a full message using the returned thread ID:

   ```bash
   printf '%s\n' '<handoff>' | $ACLA send --thread-id '<thread>' \
     --sender-id '<astra-id>' --body-file - --idempotency-key '<stable-key>'
   ```

   Do not call an inbox ID an attachment. The local thread is the mapping
   boundary. A Luna has its own tmux session and local agent ID.
5. The launcher starts and supervises one watcher for the state store. It polls
   every five minutes by default and delivers the complete message to the owned
   Astra or Luna tmux session. It refuses idle shells, foreign session metadata,
   and duplicate watcher processes. A stopped recipient leaves its message
   pending until the session is available.
6. When Luna replies, inspect the complete message and the repository diff.
   Read the thread with `$ACLA messages --thread-id '<thread>'` when the reply
   is not already visible in the current terminal.
   Reply on the same thread with numbered findings and concrete acceptance
   conditions. Send `ASTRA_REVIEW` while changes remain.
7. When the work is verified, reply with exactly `ASTRA_APPROVED` and summarize
   evidence. Only then report the result to the user. Do not merge, publish,
   deploy, or claim approval of external actions.

## Message boundaries

Initial handoff:

```text
You are Luna, the implementation actor for this review. Work only in the named
workspace and stated scope. Report changed files, evidence, verification,
remaining risks, and blockers. Do not merge or deploy. Reply on this thread when
the implementation report is ready.
```

Revision:

```text
ASTRA_REVIEW
Stream: <slug>
Status: needs_revision
1. [P0/P1/P2] <finding>
   Evidence: <file or command>
   Required change: <specific correction>
Reply with the updated report and verification evidence.
```

Approval:

```text
ASTRA_APPROVED
Stream: <slug>
Evidence: <verified summary>
Remaining owner actions: <none or exact actions>
```

## Recovery

On resume, run `$ACLA status --run-id '<run-id>'`, inspect the tmux session, and
read the local database state before sending anything. Reuse the same
`--idempotency-key` after an interrupted send. Do not create a second Luna or
thread because a command timed out. If a session disappeared, relaunch the same
registered local Luna session and keep its existing thread.
