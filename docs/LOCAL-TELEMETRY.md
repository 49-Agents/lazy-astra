# Local run telemetry

`scripts/local_telemetry.py` is a standalone, standard-library observer. It opens
the ACLA database and native Codex thread index read-only. It reads only user
envelope markers and runtime configuration from native JSONL transcripts; it does
not write to Codex, send messages, run models, or change inbox handling.

Run once:

```sh
python3 scripts/local_telemetry.py
python3 scripts/local_telemetry.py --report
```

The default output is `~/.astra-critic-luna-actor/telemetry/latest.json` plus daily
JSONL snapshots, retained for 30 days. Output permissions are private to the OS
user. No message bodies, handoffs, goals, reasoning, or credentials are copied.
Agent names, IDs, timestamps and aggregate measurements are included.

## Reading the measurements

- Run completion, duration, actors approved, message volume, question count,
  explicit replies and review-request prefixes describe workflow activity.
- `luna_report_candidates` counts Luna messages other than question-prefixed
  messages. It is an estimate, not a semantic classification or correctness score.
- Unhandled backlog, age and handling latency use the new inbox protocol.
  `legacy_handling_unknown` keeps old delivered history separate. Acknowledging a
  question does not prove it was answered; explicit reply coverage is separate.
- Per-agent notification state and an error flag show delivery trouble. Error
  bodies are not copied. Use ACLA status for operational diagnosis.
- Transcript duplicate counts count repeated visible ACLA message IDs or wake-up
  IDs within each task. They are cumulative across that transcript. Compare
  snapshots to see new duplicates after deployment. They do not establish that
  the model executed work twice.
- Legacy first-arrival delay is measured from inbox creation to its first visible
  full-message envelope. Wake-ups do not carry message IDs, so the observer does
  not invent per-message arrival latency for them.
- Latest native turn configuration reports actual model and effort; it can differ
  from machine defaults. A missing service tier or other field means unknown.
- Missing native history is marked unavailable. Native `state_5.sqlite` and JSONL
  parsing are best effort and may require an update after Codex format changes.

Snapshots can miss short-lived failures and retries between polls. They do not
grade code quality, prove answer correctness, or measure exact token cost. Full
transcripts are scanned each collection; the collector has no persistent event
cursor. This keeps the first implementation simple at the cost of scan time.

## This machine's scheduled installation

The owner-requested installation uses a user systemd timer:

```sh
systemctl --user status acla-telemetry.timer
journalctl --user -u acla-telemetry.service -n 20
python3 ~/.local/lib/acla-telemetry/current/local_telemetry.py --report
systemctl --user disable --now acla-telemetry.timer  # stop future collections
```

It collects every five minutes, independently of the inbox watcher. The service
runs a pinned copy under `~/.local/lib/acla-telemetry/`, so plugin cache cleanup or
task-worktree removal does not break it. `current` points to that installed copy;
it is not automatically updated by a plugin reinstall. No network listener or
third-party telemetry service is involved. A failed collection leaves the prior
latest report intact and reports a service failure; check `observed_at` for
freshness. The unit has a two-minute execution limit and low CPU/I/O priority.
