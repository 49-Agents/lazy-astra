# Verification notes

Runtime: Linux, Python 3, tmux, Codex CLI 0.155.1, GPT-6 model.

## Automated checks

`python3 -m unittest discover -s tests -v`: 24 passing tests at the final
integration checkpoint. These use temporary databases, mocked queue outcomes,
and an actual isolated tmux server with harmless processes. They cover:

- Native Manager binding without tmux and complete Worker bootstrap.
- Stable run/actor identities, payload conflicts, and separate actors.
- Native actor binding and rejection of replies from another Codex task.
- Exact-conversation resume and two-way routed message envelopes.
- Concurrent message claims, pre-dispatch retry, uncertain dispatch reconciliation.
- Approval per actor and overall run completion.
- Additive database migration and immutable tmux ownership.
- Watcher source upgrades and delivery of pending final approvals after restart.

## Live model acceptance

Two CLI actors ran concurrently in disposable Git workspaces. Manager remained in
the existing desktop task. Run, actor, and native thread identifiers are omitted.

1. The launcher supplied each full handoff as the initial CLI prompt.
2. Each actor bound its actual CODEX_THREAD_ID, wrote a small requested fixture,
   read it back, and used the bound send-reply command.
3. Both full reports were accepted by `codex queue` for Manager's exact task UUID.
   While its current turn remained active, Manager inspected those reports in the
   inbox history and checked both actual fixture files. A read-only query of
   Codex's own queue, filtered to this exact task, confirmed all four reports
   persisted there. All four subsequently arrived as full user inputs in the
   active desktop conversation. Their arrival order differed from creation order;
   the Manager reconciled them against the already approved current state.
4. Manager sent corrections through the plugin. Both actors received the complete
   envelope and changed their own fixture from version 1 to version 2.
5. Before receiving its correction, alpha's owned tmux session was stopped. Its
   correction stayed pending. Repeating run-start resumed the same native Codex
   conversation, which then received the pending correction and reported back.
6. Both version 2 results were read and verified. Manager sent approval through
   the plugin. All eight messages were accepted, the run became approved, and
   both actors stopped work without sending another report.

Both actors retained their native conversations across restart. The visible
runtime showed the selected model for both. Actors were stopped using their saved
ownership identities. A watcher source upgrade was also exercised.

## Limits

These scratch workspaces initially showed Codex's directory-trust prompt. The
reviewer accepted it only after checking that each was the newly created empty
fixture. This evidence predates the automatic workspace trust update: the current
launcher passes a workspace-specific trust override at startup. A subsequent
owner-requested update makes every launch/resume use danger-full-access with
approval policy never; login and hook trust remain separate. The earlier acceptance run does not validate
the new trust override; no new runtime checks were requested for that update.

Queue success proves Codex accepted the message, not that the model completed
its turn. Delivery timing for an active Manager is controlled by the Codex host;
message arrival order is not treated as revision order. This is a local-host CLI
integration, not evidence of cross-machine support or an exactly-once transport.
