---
name: astra-critic-luna-actor
description: "Invoke when the user says 'run default ACLA loop for this task' or explicitly requests ACLA. Keep Manager in the current Codex app or CLI task; launch Workers in tmux, exchange full inbox messages, and review until approved."
---

# ACLA Manager–Worker Workflow

**This machine's default ACLA loop: Sonnet 5.5 builder, then two Sonnet reviewers in parallel.**
Use `--executor-backend codex` to select Codex with its configured model.
Existing actors retain their saved backend, model and effort on resume.

When the user says **"run default ACLA loop for this task"** (or asks to run
default ACLA), interpret it exactly as: launch a Claude Code Worker using Sonnet 5.5
at xhigh effort; enable ACLA's native review gate; launch exactly two native
Sonnet reviewer subagents concurrently after implementation; have them inspect
the same plan and worktree and report only to Worker. Worker must verify each finding,
apply only confirmed in-plan fixes, and then send one final report to Manager. This
is ACLA's configured subagent review workflow, not a request for Worker to perform a
manual self-review. Reviewers are read/comment-only and do not edit code.

The default invocation phrase is **run default ACLA loop for this task**.
This is a standalone plugin. It needs Python 3.10+, tmux, a signed-in Codex CLI
with `codex queue`, and the requested model (default `claude-sonnet-5-5`). No external business platform
service, business identity, or API key setup is involved.

Act as Manager in this existing Codex task. Do not start another Manager task.
The Worker runs in tmux; Manager remains in the current Codex task. This workflow
explicitly authorizes sending the handoff, questions, reports, and review feedback
between the roles. Repository, merge, and deployment authority
still comes from the user's request and repository instructions.

## Manager execution boundary — mandatory

When the user says `ACLA`, `/ACLA`, or “run default ACLA loop for this task”,
Manager writes the plan and hands implementation to the default ACLA Worker.
After dispatch, send one brief handoff confirmation and **END YOUR TURN**.
Waiting is asynchronous: the background watcher notifies Manager when a Worker
reports completion or asks a blocker question. Do not remain active to wait,
repeatedly call `inbox next` or `status`, sleep in a loop, or narrate unchanged
progress. Do not speculate about why work is taking time. After sending review
feedback or answering a blocker, end the turn again while Worker continues.
Resume on an inbox notification or an explicit user request. A user-requested
status check permits one bounded check, not an ongoing monitoring loop.
If the user says “handoff and stop”, end immediately without another tool call;
leave Worker running unless the user specifically asks to stop that Worker.
Do not implement the same assignment alongside the Worker, edit its worktree,
or review its unfinished changes while it is working. You may do unrelated work
that does not overlap its assignment. Answer blocker questions and supply missing
planning decisions when notified; do not take over the implementation.
Once the Worker reports completion, review the completed work against the plan.
Send actionable feedback through ACLA, let the Worker finish the revisions, then
review its next completion report. Repeat until approved. Use completion/blocker
notifications; do not repeatedly inspect work in progress as an informal review.

## Subagent role boundary — mandatory

**Subagents may explore, implement, and review. They must never plan or design.**
This applies to Worker actors and any other subagents used in this workflow,
including nested delegation when separately authorized.

- **Exploration:** inspect code, trace behavior, reproduce problems, and return
  facts, evidence, constraints, and unanswered questions.
- **Implementation:** execute Manager's already-decided plan and design within the
  supplied scope and acceptance criteria.
- **Review:** inspect an existing implementation or Manager-authored plan/design
  against supplied requirements; report defects, risks, and supporting evidence.
  Reviewing a plan does not authorize writing or redesigning one.
- **Manager only:** author or revise plans, designs, architecture, task breakdowns,
  implementation sequences, scope, acceptance criteria, and tradeoff decisions.
  Manager also owns final approval and escalates owner decisions to the user.

Never assign "create an implementation plan", "design the solution", "choose an
architecture", or equivalent work to a subagent, even under an exploration or
review label. If exploration/review exposes a planning or design gap, the subagent
returns the finding or asks Manager a question and pauses affected work. Manager
supplies the decision before implementation continues. Include the allowed role,
expected output, and this boundary in every handoff. Do not delegate planning or
design to another subagent to bypass the restriction.

## Locate the installed helper

Resolve this loaded SKILL.md's path. The plugin root is **two directories above
its containing skill directory** and contains `acla_cli.py` and `acla/`.
Use that absolute root for all commands, including after compaction. Do not assume
`~/astra-critic-luna-actor` contains the same installed version.
In the examples, replace `/absolute/plugin` with that root. Use quoted paths.
The helper binds Manager from `CODEX_THREAD_ID`; it never guesses a task by its name.
If that environment variable is absent, use a known exact task UUID through
`--manager-thread`; do not invent an ID. Require the local CLI to access that task.

## Start the work

1. Read the code and instructions. Manager authors the plan/design and a thorough
   handoff naming the actor's role (exploration, implementation, or review), outcome,
   repo and base commit, dedicated worktree/branch, constraints, ordered steps,
   acceptance criteria, meaningful checks, and risks. Prepare one worktree per
   independent actor to prevent concurrent edits. Save each handoff as a file.
2. Choose a descriptive Worker name per workstream. Generate a UUID for the run
   **before launch** and save it with the handoff paths. Use that same run ID for
   retries and for all actors on this goal. Preserve it in your task notes.
3. Start each actor:

   ```bash
   python3 /absolute/plugin/acla_cli.py run-start \
     --run-id '<saved UUID>' --goal '<shared goal>' \
     --workspace '/absolute/actor/worktree' --worker-name 'implement-parser' \
     --handoff-file '/absolute/handoff.md' --interval 300
   ```

   The launcher selects Claude Code with Sonnet 5.5 at xhigh effort, supplies the complete handoff in the initial
   CLI prompt, and starts one watcher. It returns the run, actor, review-thread,
   and tmux identities. Save them. `awaiting_actor_binding` means the process
   started but has not yet registered its actual Codex conversation. At most one
   immediate status check may clarify launch state; do not poll until binding;
   login, hook trust, or model errors may require opening the returned terminal.
   Do not claim the actor is ready before it binds. Report “launched; binding
   pending” if needed and end the turn. Investigate concrete launch errors only.
4. For another workstream, use the same run ID/goal with a different name,
   worktree, and handoff file. `--worker-model` selects an explicitly requested
   alternative. `--interval` changes the shared state store's polling interval;
   the default is five minutes. Empty polls do not invoke a model.

### Choose the executor backend

`--executor-backend codex|claude-code` selects the CLI harness independently of
`--worker-model`. New Codex actors use the Codex-configured model unless local config
or `--worker-model` selects another. New Claude Code actors default to
`claude-sonnet-5-5` with `--worker-effort xhigh`. Saved backend,
model, executable and native session identity are retained on resume; switching
backends requires a new actor and a reconciled handoff, never conversion in place.

For Claude Code, add `--executor-backend claude-code` (Sonnet 5.5, xhigh effort).
`--claude-command /absolute/executable` overrides the configured Claude executable.
A native Claude model can be explicitly selected with `--worker-model sonnet` and
uses `claude` by default. Never silently fall back to a different model/provider.
Claude authentication/gateway setup must already work.

Claude actors run serial noninteractive turns in a persistent tmux runner,
resuming an exact saved Claude session. Their own inbox is polled locally every
2 seconds while idle; this does not invoke a model when empty. Manager notifications
still use the shared Codex watcher interval. Claude actors use `bypassPermissions`,
sandbox disabled and fast mode disabled on every turn. The Codex-specific launch
flags described below apply only to Codex. Claude requires workspace trust
`trusted`; `configured` is rejected because this runner cannot present a trust UI.

Claude inbox helpers validate the saved session/actor environment and Codex home,
within the same-OS-user coordination boundary. They do not require CODEX_THREAD_ID.
The review gate uses native Claude Code Agent children, using the saved reviewer
model and inheriting executor permissions; identical-input/read-only/500-word rules remain mandatory.
If delegation is unavailable, ask Manager instead of skipping review.

Inspect `status` for `executor_backend`, `claude_session_id`, `claude_initialized`
and `runtime_error`. A reserved session ID is not proof of a ready actor.
Watch stream output with `tmux -L acla attach -t '<returned session>'`.
Failed Claude turns stop the runner. Stream logs survive under
`<state directory>/claude-runtime/<actor UUID>.jsonl` (private files). Inspect its terminal/native transcript and
outstanding claims before repeating run-start; do not blindly replay a plan.
A resume pulls outstanding inbox work without reissuing the initial handoff.
Claude session history stays in the launcher's configured Claude directory.

### Model speed, permissions, and workspace trust

**Use Sonnet 5.5 with xhigh effort for new Claude Code actors.** Codex is an
explicit alternate backend and uses the model selected by `--worker-model`, the
private local config, or the Codex user's configured default. Provider-specific
model names, router URLs, launcher paths, and capability exceptions belong in
`~/.astra-critic-luna-actor/local.json` or the file named by `ACLA_LOCAL_CONFIG`.
Never copy machine-specific routing details into this public repository. Missing
provider configuration is a blocker; do not silently switch providers or models.

For models whose reasoning effort is not mapped by their provider, omit effort and
report the provider default. `reasoning_effort_supported=false` means ACLA does not
set a supported effort; it does not claim the model performs no reasoning.

Keep the configured effort fixed. Manager and Workers must not raise or lower it
based on task complexity, plan length, or their own judgment. Only an explicit
owner request authorizes an effort override. New Claude Workers default to xhigh.
For explicitly selected GPT actors, retain the configured effort (minimum high).
The launcher pins `model_reasoning_effort` on both launch and resume. New GPT actors
default to high; omitting the flag on resume preserves the last saved launch
effort, including xhigh/max. Normal service speed remains mandatory at all levels.

An existing terminal does not change configuration when `run-start` reuses it.
Check `effort_restart_required`: true means a controlled stop/resume is needed.
Arrange that at a safe boundary with the bound Manager, preserving the exact run,
actor, workspace, handoff and native task. Repeat `run-start` with the chosen
`--worker-effort` after `stop-actor`. `worker_launch_effort` records the launcher setting,
not proof of the latest model turn; inspect runtime telemetry to confirm adoption.

Worker must use normal speed, never fast mode. The launcher explicitly sets
`service_tier="default"` on every launch and resume. Do not enable fast/priority
mode. Existing sessions need a controlled resume to adopt changed launch settings.

The owner requires full access for every Worker: the launcher always passes
`--sandbox danger-full-access --ask-for-approval never` on launch and resume.
Use the launcher rather than constructing a restricted Codex command. These
permissions do not expand the handoff's merge, deployment, or external-send scope.

The owner authorizes automatic workspace trust for actors launched with this
workflow. `run-start` defaults to `--workspace-trust trusted` for any selected
workspace, including new worktrees, on both initial launch and resume. It passes
an exact-workspace Codex configuration override for that invocation, allowing
project configuration and instructions to load. Do not request trust confirmation
again for each ACLA launch. The launcher applies these settings per invocation
and does not change the user's global Codex configuration.
Use `--workspace-trust configured` if the owner requests normal Codex trust
settings/prompts. Login and hook trust are separate.

For an existing actor using old permissions or stalled at a directory-trust prompt,
use the updated helper's `stop-actor --worker-id '<ID>'`, then repeat its original
`run-start` with the same state, identities, model, workspace, and handoff. This
controlled restart applies full access and the trust override while retaining
any bound conversation.
Do not inject keystrokes into interactive prompts. If a trust prompt persists
after one restart with the updated launcher, report the exact blocker.

## Review the inbox

### Optional executor review loop

`run-start --reviewLoop true --n_reviewers 2` enables the executor's native
review loop. `reviewLoop` is a boolean (new-actor default **true**);
`n_reviewers` is a positive integer (this machine's default **2**). Both are saved per actor,
shown by run-start/status, and preserved when omitted on resume. The equivalent
kebab-case flags are `--review-loop` and `--n-reviewers`. Stop/resume the same
actor before changing these settings for an existing live terminal.

When enabled, after implementation and before reporting to Manager, the executor
creates exactly `n_reviewers` **native subagents of its executor backend**, never tmux/ACLA actors.
Codex uses its native subagent tools; Claude Code uses its native Agent tool.
They use the saved reviewer model and full-access/never-approval configuration.
Start all reviewers concurrently in one batch when the backend allows it. On this
machine, Claude Code executors use Sonnet 5.5 and two Sonnet reviewers by default.
`--reviewLoop false` explicitly opts out. Use it when the user requests ACLA
“without review” or “without self-review”. This does not change the handoff/yield
rule: end the turn after dispatch. Honor an explicit request to skip Manager
review too; do not substitute active monitoring for disabled reviews.
Use the default native agent role, not a restricted/custom review role. If native
delegation or the required permission inheritance is unavailable, ask Manager;
do not silently bypass the configured review. Limited slots allow sequential
batches, not a reduced total count. This explicitly authorizes these review-only
children; it does not authorize planning/design delegation or recursive reviews.

Give all reviewers **identical self-contained input**: the full current
Manager-authored plan, including explicit amendments, and the absolute implementation
worktree path. Do not fork the executor's conversation or provide different areas,
security/performance roles, leading conclusions, or other reviewers' findings.
Keep the worktree unchanged while reviews run. Their only question is whether
the code executes the plan exactly: missing requirements, implementation
discrepancies, or behavior outside the plan. No unrelated efficiency/style review,
alternative designs, edits, tests/builds, file writes, or further subagents.
Each reviewer replies only to the executor, in **at most 500 words**, citing
plan requirements and file/line evidence. No findings is valid; uncertainty must
be labeled. Full access is their execution mode; no edits is a task constraint,
not an OS sandbox guarantee. The launcher supplies the same prompt template to
the executor on both bootstrap and resume.

The executor treats reviewers as **interns**, not peers or decision-makers.
After collecting every reply, independently confirm each finding against the
plan and code. Apply a fix only when it fits the existing plan and the executor
finds the feedback valid; reject unsupported feedback rather than blindly
following it or taking a vote. Correcting confirmed implementation mismatches is
authorized. New plans/designs/scope choices remain exclusively Manager's job—ask
when one is needed. Inspect the final diff after fixes, then send the ordinary
single completion report to Manager, including reviewer IDs, accepted/rejected
findings with reasons, fixes and unresolved questions. Do not send interim reviews.

One batch of two parallel reviewers per completed implementation/revision round
on this machine. Do not repeat
until consensus or apply the loop to exploration-only/review-only actors or the
reviewers themselves. Failed/missing reviewer replies are not clean reviews.
These are instructions for the native agent workflow, not a separate tmux review
service or proof that the reviews occurred; Manager checks the reported evidence.

Worker executes the agreed handoff; Manager owns decisions. Include this policy in
every handoff: no interim reports, progress updates, milestone summaries,
acknowledgements, or periodic check-ins. Worker sends one completion report only
after the entire assignment is finished, then waits for review. Each requested
revision round ends with one updated completion report after all revisions are
finished. Approval ends the exchange without a reply.

When blocked or needing planning input, a decision, or clarification, Worker must
use `ask-question` and wait for Manager's answer before doing the affected work.
Worker supplies facts and the question; it must not choose a plan, resolve ambiguity,
change scope/design/requirements, or make tradeoffs independently. A blocker is
a question, not an interim report. Answer with explicit direction; if the decision
requires owner input, ask the owner and leave the affected work paused. Do not
request progress reports from actors. The launcher supplies this policy on new
launches and resumes; already running actors need an inbox instruction to adopt it.

Queue notifications contain no full body. They only say to read `inbox next`;
several messages for one recipient coalesce into one wake-up across review threads.
The command returns full messages and an expiring claim token, bound to the current
native Codex thread and home. Process only returned IDs and acknowledge exactly the
IDs processed with `inbox acknowledge --token TOKEN --message-id ID`. Save the token
and IDs before lengthy work. After acknowledging each processed batch, continue
calling `inbox next` until it returns empty. This is a bounded drain of messages
already available in a notification-triggered turn, not periodic polling for
future work. Once empty, end the turn immediately; do not sleep and pull again.
An empty inbox alone is not a reason to send a waiting/status message. The default
batch is 20 (maximum 100); claims expire after 15 minutes. An empty inbox or delayed
duplicate wake-up requires no chat response and no actor message. Do not act on a
delayed full envelope from an older workflow until reconciling its message ID with
`inbox next --message-id ID`. Thread history remains read-only. A `--reply-to ID`
on send, question, or approval atomically consumes only that exact incoming message;
approval does not mark any other history handled.

Before acting, read the run status and latest thread history: messages can arrive out of order.
Ignore reports for already approved actors and superseded earlier reports; never
reopen work just because a delayed message arrives.

When Worker reports, inspect the actual diff and meaningful verification evidence.
Send concrete numbered corrections on that actor's review thread:

```bash
python3 /absolute/plugin/acla_cli.py send --thread-id '<review-thread UUID>' \
  --body-file '/absolute/review.md' --idempotency-key '<saved unique key>' [--reply-to ID]
```

Use `MANAGER_REVIEW` at the start of requested revisions. Answer questions on the
same thread. Do not ask the human to relay anything. Keep reviewing each actor
until the acceptance criteria are met. Your ordinary final chat message does not
send feedback: use the helper for every actor response.

Approve each completed actor explicitly, with review evidence in the body file:

```bash
python3 /absolute/plugin/acla_cli.py approve --thread-id '<review-thread UUID>' \
  --body-file '/absolute/approval.md' --idempotency-key '<saved unique key>'
```

This queues the established `ASTRA_APPROVED` wire marker for compatibility,
closes that Worker's workstream, and marks the run
approved once every actor is approved. Tell the owner what changed, what passed,
and what remains. Approval does not merge or deploy code. The actor should stop
working on approval without generating an acknowledgement/report loop.

## Recovery and status

```bash
python3 /absolute/plugin/acla_cli.py status --run-id '<saved UUID>'
python3 /absolute/plugin/acla_cli.py messages --thread-id '<review-thread UUID>'
```

Repeat the **same** run-start command to reuse an actor or resume its saved executor
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

For an uncertain coalesced wake-up, inspect that exact Codex task before running
`resolve-notification --recipient-id ID --delivered` or `--retry`. The command
works only from the recipient's own bound Codex task.
On upgrade, pending or safely claimed-but-not-dispatched legacy messages are
eligible for coalesced wake-ups; legacy delivered history is not replayed. Legacy
uncertain message deliveries remain quarantined until `resolve-delivery --retry`.

For an owner-requested stop or controlled restart, `stop-actor --worker-id '<ID>'`
checks the saved tmux ownership and retains the conversation and messages.
Actor helper commands `bind-session`, `send-reply`, and `ask-question` bind to
that actor's saved backend identity; bootstrap gives the actor their exact use.

### Reviewer model default

New native Claude Code actors use two parallel **Sonnet** reviewers (`reviewer_model=sonnet`)
in one review batch per completed implementation/revision round. Execution stays
on Sonnet 5.5 with xhigh effort. The runner pins `CLAUDE_CODE_SUBAGENT_MODEL` and the
review prompt names the saved reviewer model. All reviewers still receive identical
plan/worktree input and may only read and comment. `reviewLoop=false` disables it.
Existing actors keep their saved policy (older rows inherit their executor model).
Explicit Codex and custom routed actors retain their executor model for reviews;
any model-specific review limitations must be recorded in private local config. Status reports the effective reviewer model.

## Repair a stopped Worker's Claude launcher

A changed machine default does not update saved Workers. If resuming with a new
`--claude-command` fails with “different destination command”, the bound Manager
must use the supported update command after stopping the Worker:

```bash
python3 /absolute/plugin/acla_cli.py set-worker-launcher --worker-id '<saved Worker UUID>' --claude-command '/absolute/path/to/claude'
```

This preserves the model, handoff, inbox and session; it does not launch or message
the Worker. Repeat the saved `run-start` afterward. If the previous launcher used
a different Claude configuration directory, first ensure the replacement can
access the saved native session. Never reset session identity or edit SQLite to
bypass this check.
