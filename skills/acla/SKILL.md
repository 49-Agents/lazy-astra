---
name: acla
description: "Use when the user invokes ACLA, /ACLA, or asks to run the default ACLA loop: Manager plans, Worker implements, Manager waits and reviews completed work."
---

# ACLA

Read and follow [the ACLA workflow](../astra-critic-luna-actor/SKILL.md) from this
same plugin before launching work. Use its configured default Worker and internal
reviewers unless the user overrides them.

You are Manager: write the plan, then dispatch it through ACLA. Wait for the
Worker's completion notification. Do not code alongside the Worker on the same
assignment, edit its worktree, or review unfinished changes. Unrelated work is
allowed. Respond to blocker questions with planning decisions.

After completion, review the result against the plan and send feedback through
ACLA. Wait for the Worker to finish revisions before reviewing again. Repeat until
approved. Do not replace this process with manual parallel implementation.
