---
name: astra-critic-luna-actor
description: "Invoke when the user says 'run the astra critic luna actor' or explicitly requests the Astra–Luna review workflow. Keep Astra in the current Codex app or CLI task; launch Codex or Claude Code actors in tmux, exchange full inbox messages, and review until approved."
---

# Astra Critic Luna Actor

**New actor default: Claude Code, `claude-opus-5-5`, medium effort, one self-review round with one Sonnet reviewer.**
Use `--executor-backend codex` to explicitly select Codex (DeepSeek by default).
Existing actors retain their saved backend, model and effort on resume.

The secret phrase is **run the astra critic luna actor**, ignoring case and hyphens.
This is a standalone plugin. It needs Python 3.10+, tmux, a signed-in Codex CLI
with `codex queue`, and the requested model (default `claude-opus-5-5`). No external business platform
service, business identity, or API key setup is involved.

Act as the critic in this existing Codex task. Do not start another Astra task.
The actor runs in tmux; the critic can remain in the Codex app. This workflow
explicitly authorizes sending the handoff, questions, reports, and review feedback
between the actors and this critic. Repository, merge, and deployment authority
still comes from the user's request and repository instructions.

## Subagent role boundary — mandatory

**Subagents may explore, implement, and review. They must never plan or design.**
This applies to Luna actors and any other subagents used in this workflow,
including nested delegation when separately authorized.

- **Exploration:** inspect code, trace behavior, reproduce problems, and return
  facts, evidence, constraints, and unanswered questions.
- **Implementation:** execute Astra's already-decided plan and design within the
  supplied scope and acceptance criteria.
- **Review:** inspect an existing implementation or Astra-authored plan/design
  against supplied requirements; report defects, risks, and supporting evidence.
  Reviewing a plan does not authorize writing or redesigning one.
- **Astra only:** author or revise plans, designs, architecture, task breakdowns,
  implementation sequences, scope, acceptance criteria, and tradeoff decisions.
  Astra also owns final approval and escalates owner decisions to the user.

Never assign "create an implementation plan", "design the solution", "choose an
architecture", or equivalent work to a subagent, even under an exploration or
review label. If exploration/review exposes a planning or design gap, the subagent
returns the finding or asks Astra a question and pauses affected work. Astra
supplies the decision before implementation continues. Include the allowed role,
expected output, and this boundary in every handoff. Do not delegate planning or
design to another subagent to bypass the restriction.

## Locate the installed helper

Resolve this loaded SKILL.md's path. The plugin root is **two directories above
its containing skill directory** and contains `acla_cli.py` and `acla/`.
Use that absolute root for all commands, including after compaction. Do not assume
`~/astra-critic-luna-actor` contains the same installed version.
For this local deployment only, `/home/example/plugins/astra-critic-luna-actor` is a
documented helper alias; generic package code must not depend on that path. If an
older task has a historical helper path in its bootstrap, first check the current
installed skill root and use its helper for new operations.

In the examples, replace `/absolute/plugin` with that root. Use quoted paths.
The helper binds Astra from `CODEX_THREAD_ID`; it never guesses a task by its name.
If that environment variable is absent, use a known exact task UUID through
`--astra-thread`; do not invent an ID. Require the local CLI to access that task.

## Start the work

1. Read the code and instructions. Astra authors the plan/design and a thorough
   handoff naming the actor's role (exploration, implementation, or review), outcome,
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

   The launcher selects Claude Code with Opus 5.5 at medium effort, supplies the complete handoff in the initial
   CLI prompt, and starts one watcher. It returns the run, actor, review-thread,
   and tmux identities. Save them. `awaiting_actor_binding` means the process
   started but has not yet registered its actual Codex conversation. Check status;
   login, hook trust, or model errors may require opening the returned terminal.
   Do not claim the actor is ready before it binds.
4. For another workstream, use the same run ID/goal with a different name,
   worktree, and handoff file. `--luna-model` selects an explicitly requested
   alternative. `--interval` changes the shared state store's polling interval;
   the default is five minutes. Empty polls do not invoke a model.

### Choose the executor backend

`--executor-backend codex|claude-code` selects the CLI harness independently of
`--luna-model`. New Codex actors default to Local model. New Claude Code actors default to
`claude-opus-5-5` with `--luna-effort medium`. Saved backend,
model, executable and native session identity are retained on resume; switching
backends requires a new actor and a reconciled handoff, never conversion in place.

For Claude Code, add `--executor-backend claude-code` (Opus 5.5, medium effort).
Explicit `--luna-model local-model` uses the installed
`claude-deepseek` executable; `--claude-command /absolute/executable` overrides it.
A native Claude model can be explicitly selected with `--luna-model sonnet` and
uses `claude` by default. Never silently fall back to a different model/provider.
Claude authentication/gateway setup must already work.

Claude actors run serial noninteractive turns in a persistent tmux runner,
resuming an exact saved Claude session. Their own inbox is polled locally every
2 seconds while idle; this does not invoke a model when empty. Astra notifications
still use the shared Codex watcher interval. Claude actors use `bypassPermissions`,
sandbox disabled and fast mode disabled on every turn. The Codex-specific launch
flags described below apply only to Codex. Claude requires workspace trust
`trusted`; `configured` is rejected because this runner cannot present a trust UI.

Claude inbox helpers validate the saved session/actor environment and Codex home,
within the same-OS-user coordination boundary. They do not require CODEX_THREAD_ID.
The review gate uses native Claude Code Agent children, using the saved reviewer
model and inheriting executor permissions; identical-input/read-only/500-word rules remain mandatory.
If delegation is unavailable, ask Astra instead of skipping review.

Inspect `status` for `executor_backend`, `claude_session_id`, `claude_initialized`
and `runtime_error`. A reserved session ID is not proof of a ready actor.
Watch stream output with `tmux -L acla attach -t '<returned session>'`.
Failed Claude turns stop the runner. Stream logs survive under
`<state directory>/claude-runtime/<actor UUID>.jsonl` (private files). Inspect its terminal/native transcript and
outstanding claims before repeating run-start; do not blindly replay a plan.
A resume pulls outstanding inbox work without reissuing the initial handoff.
Claude session history stays in the launcher's configured Claude directory.

### Model speed, permissions, and workspace trust

**Use Local model for new Codex actors; Opus 5.5 medium for new Claude Code actors.** The historical Luna name and
`--luna-model` flag remain for compatibility. Astra stays the critic. Use the
selected executor backend (Claude Code by default). Codex uses its configured Local provider router. This machine routes `local-model` through
`http://127.0.0.1:18445/v1` to the friend's hosted DeepSeek server. The router and
model catalog must already be configured in the actor's CODEX_HOME. An explicit
`--luna-model` (or ACLA_LUNA_MODEL for new actors) is an intentional override;
never silently fall back to an OpenAI model if DeepSeek is unavailable.

For **Local model**, omit `--luna-effort`. The adapter does not map high/xhigh/max
to upstream reasoning; an explicit effort flag is rejected. The launcher uses
Codex catalog value `none` to override inherited GPT effort and reports
`reasoning_effort_supported=false`, `reasoning_mode=upstream-default-unmapped`.
This does not claim that the upstream model performs no reasoning.

Resumes without an explicit model preserve the actor's saved model. Never silently
convert an existing GPT conversation to DeepSeek: OpenAI encrypted compaction is
not transferable. If migration is requested, Astra must prepare a new bounded
handoff/task and reconcile outstanding inbox work before retiring the old actor.
DeepSeek is text-only with a 65,536-token advertised context. Hosted tools and
remote Responses compaction are unsupported; do not promise GPT feature parity.

For explicitly selected GPT actors, reasoning effort has a floor of **high**. Astra chooses the effort when
preparing the handoff: `high` for ordinary bounded implementation, `xhigh` for
complex multi-step work or long plans, and `max` for the hardest reasoning-heavy
assignments. Pass `--luna-effort high|xhigh|max` to `run-start`. Never select low
or medium. Plan length is a signal; consider dependencies and ambiguity too.
The launcher pins `model_reasoning_effort` on both launch and resume. New GPT actors
default to high; omitting the flag on resume preserves the last saved launch
effort, including xhigh/max. Normal service speed remains mandatory at all levels.

An existing terminal does not change configuration when `run-start` reuses it.
Check `effort_restart_required`: true means a controlled stop/resume is needed.
Arrange that at a safe boundary with the bound critic, preserving the exact run,
actor, workspace, handoff and native task. Repeat `run-start` with the chosen
`--luna-effort` after `stop-actor`. `luna_launch_effort` records the launcher setting,
not proof of the latest model turn; inspect runtime telemetry to confirm adoption.

Luna must use normal speed, never fast mode. The launcher explicitly sets
`service_tier="default"` on every launch and resume. Do not enable fast/priority
mode. Existing sessions need a controlled resume to adopt changed launch settings.

The owner requires full access for every Luna: the launcher always passes
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
use the updated helper's `stop-actor --luna-id '<ID>'`, then repeat its original
`run-start` with the same state, identities, model, workspace, and handoff. This
controlled restart applies full access and the trust override while retaining
any bound conversation.
Do not inject keystrokes into interactive prompts. If a trust prompt persists
after one restart with the updated launcher, report the exact blocker.

## Review the inbox

### Optional executor review loop

`run-start --reviewLoop true --n_reviewers 1` enables the executor's native
review loop. `reviewLoop` is a boolean (new-actor default **true**);
`n_reviewers` is a positive integer (default **1**). Both are saved per actor,
shown by run-start/status, and preserved when omitted on resume. The equivalent
kebab-case flags are `--review-loop` and `--n-reviewers`. Stop/resume the same
actor before changing these settings for an existing live terminal.

When enabled, after implementation and before reporting to Astra, the executor
creates exactly `n_reviewers` **native subagents of its executor backend**, never tmux/ACLA actors.
Codex uses its native subagent tools; Claude Code uses its native Agent tool.
They use the saved reviewer model and full-access/never-approval configuration.
New Codex executors and their reviewers default to Local model; Claude Code
executors default to Opus 5.5 and their reviewers to Sonnet. Do not select a
GPT reviewer for a DeepSeek executor. `--reviewLoop false` explicitly opts out.
Use the default native agent role, not a restricted/custom review role. If native
delegation or the required permission inheritance is unavailable, ask Astra;
do not silently bypass the configured review. Limited slots allow sequential
batches, not a reduced total count. This explicitly authorizes these review-only
children; it does not authorize planning/design delegation or recursive reviews.

Give all reviewers **identical self-contained input**: the full current
Astra-authored plan, including explicit amendments, and the absolute implementation
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
authorized. New plans/designs/scope choices remain exclusively Astra's job—ask
when one is needed. Inspect the final diff after fixes, then send the ordinary
single completion report to Astra, including reviewer IDs, accepted/rejected
findings with reasons, fixes and unresolved questions. Do not send interim reviews.

One reviewer batch per completed implementation/revision round. Do not repeat
until consensus or apply the loop to exploration-only/review-only actors or the
reviewers themselves. Failed/missing reviewer replies are not clean reviews.
These are instructions for the native agent workflow, not a separate tmux review
service or proof that the reviews occurred; Astra checks the reported evidence.

Luna executes the agreed handoff; Astra owns decisions. Include this policy in
every handoff: no interim reports, progress updates, milestone summaries,
acknowledgements, or periodic check-ins. Luna sends one completion report only
after the entire assignment is finished, then waits for review. Each requested
revision round ends with one updated completion report after all revisions are
finished. Approval ends the exchange without a reply.

When blocked or needing planning input, a decision, or clarification, Luna must
use `ask-question` and wait for Astra's answer before doing the affected work.
Luna supplies facts and the question; it must not choose a plan, resolve ambiguity,
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
calling `inbox next` until it returns empty. The default
batch is 20 (maximum 100); claims expire after 15 minutes. An empty inbox or delayed
duplicate wake-up requires no chat response and no actor message. Do not act on a
delayed full envelope from an older workflow until reconciling its message ID with
`inbox next --message-id ID`. Thread history remains read-only. A `--reply-to ID`
on send, question, or approval atomically consumes only that exact incoming message;
approval does not mark any other history handled.

Before acting, read the run status and latest thread history: messages can arrive out of order.
Ignore reports for already approved actors and superseded earlier reports; never
reopen work just because a delayed message arrives.

When Luna reports, inspect the actual diff and meaningful verification evidence.
Send concrete numbered corrections on that actor's review thread:

```bash
python3 /absolute/plugin/acla_cli.py send --thread-id '<review-thread UUID>' \
  --body-file '/absolute/review.md' --idempotency-key '<saved unique key>' [--reply-to ID]
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

For an owner-requested stop or controlled restart, `stop-actor --luna-id '<ID>'`
checks the saved tmux ownership and retains the conversation and messages.
Actor helper commands `bind-session`, `send-reply`, and `ask-question` bind to
that actor's saved backend identity; bootstrap gives the actor their exact use.

### Reviewer model default

New native Claude Code actors use one **Sonnet** reviewer (`reviewer_model=sonnet`)
for one review batch per completed implementation/revision round. Execution stays
on Opus 5.5 with medium effort. The runner pins `CLAUDE_CODE_SUBAGENT_MODEL` and the
review prompt names the saved reviewer model. All reviewers still receive identical
plan/worktree input and may only read and comment. `reviewLoop=false` disables it.
Existing actors keep their saved policy (older rows inherit their executor model).
Explicit Codex and DeepSeek-gateway actors retain their executor model for reviews;
the DeepSeek gateway cannot serve Sonnet. Status reports the effective reviewer model.
