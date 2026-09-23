---
name: astra-critic-luna-actor
description: "Invoke when the user says 'run the astra critic luna actor' or explicitly requests the Astra–Luna review workflow. Keep Astra in the current Codex app or CLI task; launch GPT-6 Luna actors in tmux, exchange full inbox messages, and review until approved."
---

# Astra Critic Luna Actor

The secret phrase is **run the astra critic luna actor**, ignoring case and hyphens.
This is a standalone plugin. It needs Python 3.10+, tmux, a signed-in Codex CLI
with `codex queue`, and the requested model (default `gpt-6-luna`). No external business platform
service, business identity, or API key setup is involved.

Act as the critic in this existing Codex task. Do not start another Astra task.
The actor runs in tmux; the critic can remain in the Codex app. This workflow
explicitly authorizes sending the handoff, questions, reports, and review feedback
between the actors and this critic. Repository, merge, and deployment authority
still comes from the user's request and repository instructions.

## Locate the installed helper

Resolve this loaded SKILL.md's path. The plugin root is **two directories above
its containing skill directory** and contains `acla_cli.py` and `acla/`.
Use that absolute root for all commands, including after compaction. Do not assume
`~/astra-critic-luna-actor` contains the same installed version.

In the examples, replace `/absolute/plugin` with that root. Use quoted paths.
The helper binds Astra from `CODEX_THREAD_ID`; it never guesses a task by its name.
If that environment variable is absent, use a known exact task UUID through
`--astra-thread`; do not invent an ID. Require the local CLI to access that task.

## Start the work

1. Read the code and instructions. Write a thorough handoff containing the outcome,
   repo and base commit, dedicated worktree/branch, constraints, ordered steps,
   acceptance criteria, meaningful checks, and risks. Prepare one worktree per
   independent actor to prevent concurrent edits. Save each handoff as a file.
2. Choose a descriptive Luna name per workstream. Generate a UUID for the run
   **before launch** and save it with the handoff paths. Use that same run ID for
   retries and for all actors on this goal. Preserve it in your task notes.
3. Start each actor:

   ```bash
   python3 /absolute/plugin/acla_cli.py run-start \
     --run-id '<saved UUID>' --goal '<shared goal>' \
     --workspace '/absolute/actor/worktree' --luna-name 'implement-parser' \
     --handoff-file '/absolute/handoff.md' --interval 300
   ```

   The launcher selects GPT-6 Luna, supplies the complete handoff in the initial
   CLI prompt, and starts one watcher. It returns the run, actor, review-thread,
   and tmux identities. Save them. `awaiting_actor_binding` means the process
   started but has not yet registered its actual Codex conversation. Check status;
   a trust/login/model error may require opening the returned tmux terminal.
   Do not inject text into such prompts or claim the actor is ready prematurely.
4. For another workstream, use the same run ID/goal with a different name,
   worktree, and handoff file. `--luna-model` selects an explicitly requested
   alternative. `--interval` changes the shared state store's polling interval;
   the default is five minutes. Empty polls do not invoke a model.

## Review the inbox

Full messages arrive automatically in this Codex task through `codex queue`.
Every envelope includes run, review-thread, sender, and message IDs, plus an exact
reply command. Treat duplicate message IDs as a single instruction.

When Luna reports, inspect the actual diff and meaningful verification evidence.
Send concrete numbered corrections on that actor's review thread:

```bash
python3 /absolute/plugin/acla_cli.py send --thread-id '<review-thread UUID>' \
  --body-file '/absolute/review.md' --idempotency-key '<saved unique key>'
```

Use `ASTRA_REVIEW` at the start of requested revisions. Answer questions on the
same thread. Do not ask the human to relay anything. Keep reviewing each actor
until the acceptance criteria are met. Your ordinary final chat message does not
send feedback: use the helper for every actor response.

Approve each completed actor explicitly, with review evidence in the body file:

```bash
python3 /absolute/plugin/acla_cli.py approve --thread-id '<review-thread UUID>' \
  --body-file '/absolute/approval.md' --idempotency-key '<saved unique key>'
```

This queues `ASTRA_APPROVED`, closes that actor's workstream, and marks the run
approved once every actor is approved. Tell the owner what changed, what passed,
and what remains. Approval does not merge or deploy code. The actor should stop
working on approval without generating an acknowledgement/report loop.

## Recovery and status

```bash
python3 /absolute/plugin/acla_cli.py status --run-id '<saved UUID>'
python3 /absolute/plugin/acla_cli.py messages --thread-id '<review-thread UUID>'
```

Repeat the **same** run-start command to reuse an actor or resume its saved Codex
conversation after its tmux process exits. Existing actors keep their complete
conversation; a fresh unbound actor receives the full bootstrap/handoff. Never
use `resume --last`, switch a recipient ID, or create a replacement run merely
because a launch timed out. Retry sends with the same key and identical text.

Pending messages wait for unavailable actors. Queue acceptance means accepted by
Codex, not proof of implementation or review. A queue timeout, ambiguous failure,
or interrupted dispatch is marked `uncertain` and is not blindly resent. Inspect
the exact recipient conversation; then explicitly reconcile:

```bash
python3 /absolute/plugin/acla_cli.py resolve-delivery --message-id 123 --delivered
# Only when inspection establishes that retry is appropriate:
python3 /absolute/plugin/acla_cli.py resolve-delivery --message-id 123 --retry
```

For an owner-requested stop or controlled restart, `stop-actor --luna-id '<ID>'`
checks the saved tmux ownership and retains the conversation and messages.
Actor helper commands `bind-session`, `send-reply`, and `ask-question` bind to
that actor's native Codex thread; bootstrap gives the actor their exact use.
