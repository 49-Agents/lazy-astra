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

The collector can be run manually or scheduled with a user-owned timer. A
scheduled installation is optional and is not installed by the plugin. Keep its
service definition, collection interval, retention, and output path in local
configuration rather than publishing machine-specific unit files or reports.

## Token usage and local comparison

Snapshots also report each bound Codex thread's cumulative `tokens_used` counter,
and the latest cumulative `modelUsage` per Claude actor session. Repeated Claude
result records are cumulative; the collector keeps the latest one per actor to
avoid double-counting. Claude-reported `costUSD` is retained by model. Codex's
local thread index has no per-chat USD field, so no Codex dollar cost is inferred.

The observer summarizes ACLA agents by role, backend, and model, then groups
unbound Codex threads on this machine by provider, model, and client source. Those
threads are descriptive controls, not task-matched examples. ACLA's run message
counts estimate back-and-forth volume; they do not grade correctness or quality.
Do not treat token, cost, approval, or elapsed-time differences as causal savings
without comparable tasks and an independent quality measure. Missing usage is
reported as unknown, not zero. These local reports may contain thread IDs and
model names; keep them private and out of Git.

Telemetry is opt-in local observation. Review the generated report before
sharing it; aggregate records can still reveal private model names, work timing,
or thread metadata.
