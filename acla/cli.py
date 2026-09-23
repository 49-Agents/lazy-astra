from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .store import Store, new_id
from .tmux import deliver, launch, safe_session

ROOT = Path(__file__).resolve().parent.parent


def read_body(args) -> str:
    if args.body_file:
        return sys.stdin.read() if args.body_file == "-" else Path(args.body_file).read_text()
    if args.body is None:
        raise ValueError("provide --body or --body-file")
    return args.body


def cmd_run_start(args) -> None:
    store = Store(args.state)
    run_id = store.create_run(args.goal, args.run_id)
    astra_id = store.ensure_agent(args.astra_id, "astra", args.astra_name)
    existing = store.agent(args.luna_id) if args.luna_id else store.existing_luna(run_id, args.luna_name)
    if existing:
        luna_id = existing["id"]
        session = existing["tmux_session"]
    else:
        session = safe_session(args.luna_session or args.luna_name)
        luna_id = store.register_agent("luna", args.luna_name, workspace=str(Path(args.workspace).expanduser()),
                                       tmux_session=session, command=args.command)
    store.pair(run_id, astra_id, luna_id)
    thread_id = store.thread(run_id, astra_id, luna_id)
    created = launch(session, str(Path(args.workspace).expanduser()), args.command)
    print(json.dumps({"run_id": run_id, "astra_id": astra_id, "luna_id": luna_id,
                      "thread_id": thread_id, "tmux_session": session, "launched": created}, indent=2))
    store.close()


def cmd_send(args) -> None:
    store = Store(args.state)
    key = args.idempotency_key or new_id()
    message = store.send(args.thread_id, args.sender_id, read_body(args), key)
    print(json.dumps(message, indent=2))
    store.close()


def cmd_poll(args) -> None:
    store = Store(args.state)
    delivered = []
    for row in store.pending(args.limit):
        recipient = store.agent(row["recipient_id"])
        session = recipient["tmux_session"]
        if not session:
            continue
        try:
            deliver(session, row["body"])
        except (RuntimeError, OSError):
            continue
        store.mark_delivered(row["id"])
        delivered.append(row["id"])
    print(json.dumps({"delivered": delivered, "checked": args.limit}))
    store.close()


def cmd_watch(args) -> None:
    interval = max(1, args.interval)
    while True:
        cmd_poll(argparse.Namespace(state=args.state, limit=args.limit))
        if args.once:
            return
        time.sleep(interval)


def cmd_status(args) -> None:
    store = Store(args.state)
    print(json.dumps(store.status(args.run_id), indent=2))
    store.close()


def cmd_messages(args) -> None:
    store = Store(args.state)
    print(json.dumps(store.messages(args.thread_id), indent=2))
    store.close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="acla", description="Astra Critic Luna Actor local coordinator")
    p.add_argument("--state", help="SQLite state path; defaults to ~/.astra-critic-luna-actor/state.sqlite3")
    sub = p.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="initialize the local inbox store")
    init.set_defaults(func=lambda a: (Store(a.state).close(), print(json.dumps({"state": a.state or "default"}))))
    start = sub.add_parser("run-start", help="create a run, pair, thread, and Luna tmux session")
    start.add_argument("--goal", required=True); start.add_argument("--workspace", required=True)
    start.add_argument("--astra-name", default="Astra Critic"); start.add_argument("--astra-id")
    start.add_argument("--luna-name", required=True); start.add_argument("--luna-session"); start.add_argument("--luna-id")
    start.add_argument("--command"); start.add_argument("--run-id"); start.set_defaults(func=cmd_run_start)
    send = sub.add_parser("send", help="send a full message on a thread")
    send.add_argument("--thread-id", required=True); send.add_argument("--sender-id", required=True)
    send.add_argument("--body"); send.add_argument("--body-file"); send.add_argument("--idempotency-key")
    send.set_defaults(func=cmd_send)
    poll = sub.add_parser("poll", help="deliver pending messages once")
    poll.add_argument("--limit", type=int, default=100); poll.set_defaults(func=cmd_poll)
    watch = sub.add_parser("watch", help="poll and deliver messages repeatedly")
    watch.add_argument("--interval", type=int, default=300); watch.add_argument("--limit", type=int, default=100)
    watch.add_argument("--once", action="store_true"); watch.set_defaults(func=cmd_watch)
    status = sub.add_parser("status", help="show a run and its Luna mapping")
    status.add_argument("--run-id", required=True); status.set_defaults(func=cmd_status)
    messages = sub.add_parser("messages", help="read complete messages on a thread")
    messages.add_argument("--thread-id", required=True); messages.set_defaults(func=cmd_messages)
    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
        return 0
    except (ValueError, RuntimeError, OSError) as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
