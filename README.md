# Astra Critic Luna Actor

**This machine's default ACLA loop: Claude Code Opus 5.5 builder, medium effort, then two parallel Sonnet reviewers.**
Use `--executor-backend codex` to select Codex with its configured model.
Existing actors retain their saved backend, model and effort on resume.

A standalone Codex plugin for an Astra critic and Opus 5.5 actors. Invoke the
single skill by saying **run the astra critic luna actor** after discussing your
issue with Astra. Astra prepares the handoff, launches actors in tmux, reviews
reports, and sends corrections until it approves the work.

**Subagents may explore, implement, and review; never plan or design.** Astra
authors all plans/designs and decides architecture, scope, task breakdowns,
sequencing, tradeoffs, and acceptance criteria. Exploration returns evidence;
implementation follows Astra's decisions; review returns findings against the
requirements, including when reviewing an Astra-authored plan. Missing decisions
go back to Astra through `ask-question`. This applies to nested subagents too;
renaming planning as exploration or review does not authorize it.

Astra stays in the current Codex app or CLI task. Actors use tmux on the same
host. Full messages live in SQLite with sender, run, review-thread and message IDs.
Codex recipients receive coalesced `codex queue` wakeups; Claude recipients use
a local serial runner to pull the same inbox. No external business platform service or
credentials are needed. There is no terminal-paste delivery.

## Requirements

- Python 3.10+, tmux, and an authenticated Codex CLI with `codex queue` support
  (developed against CLI 0.155.1).
- A model configured in Codex, selected with `--luna-model`, or supplied through
  a private local configuration file.
- Astra and Codex actors must be reachable by the same host's Codex queue facility.
- Claude Code actors additionally require an authenticated `claude` executable, or a provider-specific executable selected in local configuration.
  This version is not a cross-machine inbox service.

New Claude Code actors default to Opus 5.5 with medium effort. Codex actors use
Codex's configured default model unless overridden by `--luna-model` or private
local configuration. Astra remains the critic; inbox and review behavior is shared
across both backends. Existing actors retain saved models on resume. There is no
silent model or provider fallback.

Optional machine-specific model aliases, launchers, and capability overrides belong
in the private local config, not this repository. Set `ACLA_LOCAL_CONFIG` to a JSON
file, or use `~/.astra-critic-luna-actor/local.json`. Example:

```json
{
  "defaults": {
    "executor_backend": "claude-code",
    "claude_model": "claude-opus-5-5",
    "claude_effort": "medium",
    "reviewer_model": "sonnet",
    "review_loop": true,
    "n_reviewers": 2,
    "codex_model": "your-local-model"
  },
  "models": {
    "your-local-model": {
      "reasoning_effort_supported": false,
      "reasoning_mode": "provider-default",
      "reasoning_effort": "none",
      "reviewer_model": "your-local-model",
      "claude_command": "/path/to/local-launcher"
    }
  }
}
```

Model option entries are optional. Omit a model-specific capability entry when
normal Codex effort behavior applies. Keep local config and raw telemetry outside
Git; the repository ignores common local config filenames.

Every Codex actor launch and resume explicitly uses `service_tier="default"` (normal
speed, not fast/priority), overriding inherited speed preferences.

For explicitly selected GPT actors, reasoning effort defaults to **high**, pinned with `model_reasoning_effort` on
launch and resume. Astra may choose `run-start --luna-effort xhigh` for complex
work/long plans, or `--luna-effort max` for the hardest assignments. Only high,
xhigh and max are accepted. Omitting the flag preserves an actor's saved effort
on resume; older actors without a saved effort default to high.
Already-running terminals require controlled stop/resume to adopt a change:
`effort_restart_required` reports that need without interrupting their work.
The returned/status `luna_launch_effort` records the last launch setting, not
the observed effort of subsequent turns. Confirm actual effort in telemetry.

Every Codex actor launch and resume uses `danger-full-access` with approval policy
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

## Executor backend

New Claude Code actors default to `claude-opus-5-5` with medium effort.
Codex uses its configured model. Explicit model/effort flags override these defaults;
resumes preserve saved settings. Opus 5.5 requires Claude Code 2.1.280 or newer.

Choose the CLI independently of the model:

```bash
# Add to run-start; the remaining required arguments stay the same:
--executor-backend codex       # Codex configured model
--executor-backend claude-code # default; defaults to Opus 5.5, medium effort
# Native Claude instead, with existing authentication:
--executor-backend claude-code --luna-model sonnet
```

`--claude-command /absolute/executable` overrides the Claude launcher (a single
executable, not a shell command). Backend, executable, model and session are saved;
resumes retain them. Existing actors stay Codex. Backend changes require a new
actor, with outstanding work reconciled by Astra.

Claude turns run serially inside tmux using `claude -p --session-id UUID`, then
`--resume UUID`. The runner polls SQLite every two seconds when idle, without
model calls for empty polls. Astra receives replies through the existing shared
watcher. Both use the same claim/ack/reply-to semantics. Claude helpers bind to the
saved actor/session environment, within the existing same-OS-user trust boundary.

Every Claude turn sets `bypassPermissions`, disables sandboxing and fast mode,
and inherits the configured provider authentication. Native reviewers use Claude's
Agent tool and use the saved reviewer model. `reviewLoop=true`, `n_reviewers=2`, the
identical plan input and read/comment-only assignment remain unchanged. The gate
is an instruction policy, not proof that the reviews occurred. Custom routed
models may not map ACLA effort settings to provider-specific reasoning controls;
native Claude defaults to medium and accepts medium/high/xhigh/max effort subject
to its model support. No provider/model fallback is performed.

Claude requires the default `--workspace-trust trusted`; noninteractive Claude
loads the selected workspace's configuration and cannot offer trust prompts.
Managed policy still applies. The runner stops on failed turns/permission denials;
`status` exposes `runtime_error`, `claude_initialized` and `claude_session_id`.
Inspect tmux output and the native transcript before explicitly resuming. Resumes
request outstanding inbox work, without reissuing a completed initial handoff.
The private `<state directory>/claude-runtime/<actor UUID>.jsonl` log retains
stdout/stderr even if the tmux pane exits. History remains in Claude's configured directory.
Use the returned `tmux_session` with `tmux -L acla attach -t SESSION` to watch JSON
stream output; this pane is a runner, not an interactive Claude prompt.

CLI details: [Claude headless mode](https://code.claude.com/docs/en/headless),
[fast-mode controls](https://code.claude.com/docs/en/fast-mode).

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

## Optional native review loop

Add `--reviewLoop true --n_reviewers 2` to `run-start` to enable review after
implementation and before the executor reports to Astra. Defaults for new actors
are `reviewLoop=true` and `n_reviewers=2` on this machine; both persist per actor and are reported
by status. Omitted options retain saved settings on resume. Changing settings for
a live actor requires stop/resume; the helper does not silently interrupt it.

The executor creates exactly that many native subagents of its selected backend, inheriting its
model and full-access settings. Claude Code actors use Sonnet reviewers by default.
Launch the reviewers concurrently as one batch where the backend allows it. Use
`--reviewLoop false` to opt out. Every reviewer gets the same complete current
Astra plan and worktree, with no conversation fork or specialized review areas.
They only inspect whether the implementation matches the plan: omissions,
discrepancies and unplanned additions. No code edits, file writes, tests/builds,
planning/design, or nested reviewers. Each replies only to the executor with
at most 500 words of evidence-backed findings. Full access remains enabled;
the no-edit restriction is an instruction, not a filesystem sandbox.

The executor treats the reports as fallible intern feedback: independently
confirms findings, fixes only those it judges valid within the plan, and sends
one final report to Astra after fixes. Design/scope ambiguities still go to Astra.
It runs one batch per implementation/revision round, using batches if native
concurrency is limited. No recursive review loop or requirement for consensus.
Missing native tools or failed reviewers must be reported, not counted as success.
The gate is delivered in the actor's bootstrap/resume policy; it is not a new
tmux service or a machine-enforced certification of review completion.

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
messages implicitly. Save claim tokens and IDs before lengthy work, acknowledge each
processed batch, then keep calling `inbox next` until empty. Empty or delayed wake-ups
must be silent. `status` reports unhandled, available, claimed, and uncertain message
counts plus notification state/error for each run recipient; its compatibility
`pending_messages` value now means unhandled messages in that stream.

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

### Reviewer model default

New native Claude Code actors use two parallel **Sonnet** reviewers (`reviewer_model=sonnet`)
in one review batch per completed implementation/revision round. Execution stays
on Opus 5.5 with medium effort. The runner pins `CLAUDE_CODE_SUBAGENT_MODEL` and the
review prompt names the saved reviewer model. All reviewers still receive identical
plan/worktree input and may only read and comment. `reviewLoop=false` disables it.
Existing actors keep their saved policy (older rows inherit their executor model).
Codex actors inherit their selected executor model for reviews. Status reports the
effective reviewer model.
