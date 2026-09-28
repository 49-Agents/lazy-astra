---
name: acla
description: "Use when the user invokes ACLA, /ACLA, or asks to run the default ACLA loop: Manager plans, Worker implements, Manager ends its turn after handoff and reviews only after completion notification."
---

# ACLA

Read and follow [the ACLA workflow](../astra-critic-luna-actor/SKILL.md) from this
same plugin before launching work. Use its configured default Worker and internal
reviewers unless the user overrides them. The default builder is Sonnet 5.5 at
fixed xhigh effort. Do not adjust effort based on task complexity or plan length;
only an explicit owner request authorizes changing it.

You are Manager: write the plan, then dispatch it through ACLA. After launch,
send one brief handoff confirmation and END YOUR TURN. The background watcher
will notify you of completion or a blocker. Do not stay active waiting, repeatedly
call `inbox next` or `status`, sleep in a loop, or post waiting/progress messages. Do not code alongside the Worker on the same
assignment, edit its worktree, or review unfinished changes. Unrelated work is
allowed. Respond to blocker questions with planning decisions.

After completion, review the result against the plan and send feedback through
ACLA. After sending feedback, END YOUR TURN again; resume on the next completion
or blocker notification. Repeat until
approved. Do not replace this process with manual parallel implementation.

If the user says “handoff and stop”, end the turn immediately without another
inbox/status call. Leave the Worker running unless the user explicitly asks to
stop the Worker. “Without review” disables the internal review loop using
`--reviewLoop false`; it never requires active monitoring by Manager. Honor any
explicit instruction to skip Manager review as well.
