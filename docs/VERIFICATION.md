# Verification — 2026-09-23

Runtime: Linux, Python 3, tmux, Codex CLI 0.155.1, GPT-6 Luna.

## Automated checks

`python3 -m unittest discover -s tests -v`: 21 passing tests at the first
integration checkpoint. These use temporary databases, mocked queue outcomes,
and an actual isolated tmux server with harmless processes. They cover:

- Native Astra binding without tmux and complete GPT-6 Luna bootstrap.
- Stable run/actor identities, payload conflicts, and separate actors.
- Native actor binding and rejection of replies from another Codex task.
- Exact-conversation resume and two-way routed message envelopes.
- Concurrent message claims, pre-dispatch retry, uncertain dispatch reconciliation.
- Approval per actor and overall run completion.
- Additive database migration and immutable tmux ownership.

## Live model acceptance

Two GPT-6 Luna actors ran concurrently in separate disposable Git workspaces.
Astra remained in the existing desktop Codex task, with no tmux destination.
The run was `11111111-1111-4111-8111-111111111111`.

1. The launcher supplied each full handoff as the initial CLI prompt.
2. Each actor bound its actual CODEX_THREAD_ID, wrote a small requested fixture,
   read it back, and used the bound send-reply command.
3. Both full reports were accepted by `codex queue` for Astra's exact task UUID.
   While its current turn remained active, Astra inspected those reports in the
   inbox history and checked both actual fixture files.
4. Astra sent corrections through the plugin. Both actors received the complete
   envelope and changed their own fixture from version 1 to version 2.
5. Before receiving its correction, alpha's owned tmux session was stopped. Its
   correction stayed pending. Repeating run-start resumed the same native Codex
   conversation, which then received the pending correction and reported back.
6. Both version 2 results were read and verified. Astra sent approval through
   the plugin; delivery and actor stop behavior were inspected afterward.

Alpha retained Codex task `22222222-2222-4222-8222-222222222222` across restart;
beta used `33333333-3333-4333-8333-333333333333`. The visible runtime showed
`gpt-6-luna` for both. No external business platform resources were used.

## Limits

These scratch workspaces initially showed Codex's directory-trust prompt. The
reviewer accepted it only after checking that each was the newly created empty
fixture. The plugin does not auto-accept directory trust or login prompts.

Queue success proves Codex accepted the message, not that the model completed
its turn. Delivery to an already active critic is queued for a subsequent turn;
this check did not interrupt the active critic turn. This is a local-host CLI
integration, not evidence of cross-machine support or an exactly-once transport.
