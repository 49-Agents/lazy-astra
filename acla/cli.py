from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shlex
import sys
import time
from pathlib import Path

from .store import Store, new_id
from .tmux import (DEFAULT_SOCKET, bind, current_session, deliver, launch, pane_path,
                   safe_session, wait_destination)

ROOT = Path(__file__).resolve().parent.parent


def read_body(args) -> str:
    if args.body_file:
        return sys.stdin.read() if args.body_file == "-" else Path(args.body_file).read_text()
    if args.body is None:
        raise ValueError("provide --body or --body-file")
    return args.body


def state_path(store: Store) -> str:
    return str(store.path.resolve())


def luna_command(args) -> str:
    if args.command:
        command = args.command
        if "--model" not in shlex.split(command):
            command = shlex.join([*shlex.split(command), "--model", args.luna_model])
        return command
    return shlex.join(["codex", "--model", args.luna_model])


def discover_astra(args, persisted=None) -> dict:
    session = args.astra_session or (persisted["tmux_session"] if persisted else None)
    socket = args.astra_socket or (persisted["tmux_socket"] if persisted else None)
    workspace = args.astra_workspace or (persisted["workspace"] if persisted else None)
    command = args.astra_command or (persisted["command"] if persisted else "codex")
    if not session:
        session, socket = current_session()
    if session and not socket:
        socket = DEFAULT_SOCKET
    if session and not workspace:
        workspace = pane_path(session, socket)
    if not session:
        raise ValueError("Astra has no tmux destination; run inside tmux or pass --astra-session")
    if not workspace:
        raise ValueError("could not determine Astra workspace; pass --astra-workspace")
    return {"session": session, "socket": socket, "workspace": workspace, "command": command}


def bootstrap_message(result: dict, store: Store, model: str) -> str:
    cli = shlex.join([sys.executable, str(ROOT / "acla_cli.py")])
    state = state_path(store)
    return f"""You are Luna, actor {result['luna_id']} in run {result['run_id']}.

Your only Astra critic is {result['astra_id']}. Your only review thread is {result['thread_id']}.
The local state store is {state}. The selected Luna model is {model}.

Reply to Astra with the complete report by running:
  {cli} --state {shlex.quote(state)} send-reply --luna-id {result['luna_id']} --body-file -
and pipe the report on stdin. Ask Astra a blocking question with:
  {cli} --state {shlex.quote(state)} ask-question --luna-id {result['luna_id']} --body-file -
These commands are bound to your one Astra/thread; do not invent another recipient.

Work only in the supplied workspace and scope. Do not merge, deploy, or approve your own work.
"""


def start_watcher(store: Store, interval: int) -> dict:
    digest = hashlib.sha256(state_path(store).encode()).hexdigest()[:12]
    session = safe_session("acla-watcher-" + digest)
    identity = "watcher-" + digest
    run_id = "watcher-" + digest
    command = shlex.join([sys.executable, str(ROOT / "acla_cli.py"), "--state", state_path(store),
                          "watch", "--interval", str(interval)])
    created = launch(session, str(store.path.parent), command, agent_id=identity, run_id=run_id,
                     role="watcher", socket=DEFAULT_SOCKET,
                     env={"ACLA_STATE": state_path(store)})
    wait_destination(session, identity, run_id, "watcher", DEFAULT_SOCKET, command, timeout=10)
    return {"session": session, "created": created}


def cmd_init(args) -> None:
    store = Store(args.state)
    result = {"state": state_path(store)}
    store.close()
    print(json.dumps(result, indent=2))


def cmd_run_start(args) -> None:
    store = Store(args.state)
    run_id = args.run_id or new_id()
    persisted_astra = store.run_astra(run_id)
    astra = discover_astra(args, persisted_astra)
    existing_luna = store.existing_luna(run_id, args.luna_name)
    command = luna_command(args)
    luna_workspace = str(Path(args.workspace).expanduser().resolve())
    if existing_luna:
        luna_id = existing_luna["id"]
        luna_session = existing_luna["tmux_session"]
        luna_socket = existing_luna["tmux_socket"] or DEFAULT_SOCKET
        command = existing_luna["command"] or command
    else:
        luna_id = args.luna_id or new_id()
        suffix = f"{run_id[:8]}-{luna_id[:8]}"
        luna_session = safe_session(f"{args.luna_session or args.luna_name}-{suffix}")
        luna_socket = DEFAULT_SOCKET
    result = store.start_run(
        goal=args.goal, run_id=run_id, astra_id=args.astra_id or (persisted_astra["id"] if persisted_astra else None),
        astra_name=args.astra_name, astra_workspace=astra["workspace"], astra_session=astra["session"],
        astra_socket=astra["socket"], astra_command=astra["command"], luna_name=args.luna_name,
        luna_id=luna_id, luna_workspace=luna_workspace, luna_session=luna_session,
        luna_socket=luna_socket, luna_command=command)
    luna = store.agent(result["luna_id"])
    created = launch(luna["tmux_session"], luna["workspace"], luna["command"], agent_id=luna["id"],
                     run_id=result["run_id"], role="luna", socket=luna["tmux_socket"],
                     env={"ACLA_STATE": state_path(store), "ACLA_THREAD_ID": result["thread_id"],
                          "ACLA_ASTRA_ID": result["astra_id"], "ACLA_LUNA_ID": result["luna_id"]})
    wait_destination(luna["tmux_session"], luna["id"], result["run_id"], "luna", luna["tmux_socket"], luna["command"])
    watcher = start_watcher(store, max(1, args.interval))
    if not store.bootstrap_sent(luna["id"]):
        deliver(luna["tmux_session"], bootstrap_message(result, store, args.luna_model),
                agent_id=luna["id"], run_id=result["run_id"], role="luna",
                socket=luna["tmux_socket"], command=luna["command"])
        store.mark_bootstrap_sent(luna["id"])
    result.update({"tmux_session": luna["tmux_session"], "launched": created,
                   "luna_model": args.luna_model, "watcher": watcher})
    print(json.dumps(result, indent=2))
    store.close()


def cmd_send(args) -> None:
    store = Store(args.state)
    key = args.idempotency_key or new_id()
    message = store.send(args.thread_id, args.sender_id, read_body(args), key)
    print(json.dumps(message, indent=2))
    store.close()


def cmd_luna_message(args, marker: str) -> None:
    store = Store(args.state)
    thread = store.thread_for_luna(args.luna_id, args.thread_id)
    body = read_body(args)
    if marker:
        body = marker + "\n" + body
    key = args.idempotency_key or new_id()
    message = store.send(thread["id"], args.luna_id, body, key)
    print(json.dumps(message, indent=2))
    store.close()


def cmd_poll(args) -> None:
    store = Store(args.state)
    worker = args.worker_id or new_id()
    delivered = []
    rows = store.claim_pending(worker, args.limit, stale_after=max(30, args.interval * 3))
    for row in rows:
        recipient = store.agent(row["recipient_id"])
        try:
            if not recipient["tmux_session"] or not recipient["tmux_socket"]:
                raise RuntimeError("recipient has no bound tmux destination")
            deliver(recipient["tmux_session"], row["body"], agent_id=recipient["id"],
                    run_id=store.db.execute("SELECT run_id FROM threads WHERE id=?", (row["thread_id"],)).fetchone()["run_id"],
                    role=recipient["kind"], socket=recipient["tmux_socket"], command=recipient["command"])
        except (RuntimeError, OSError, ValueError):
            store.release_claim(row["id"], worker)
            continue
        store.mark_delivered(row["id"], worker)
        delivered.append(row["id"])
    print(json.dumps({"delivered": delivered, "checked": len(rows)}))
    store.close()


def cmd_watch(args) -> None:
    lock_path = Path(args.state or os.environ.get("ACLA_STATE", str(Path.home() / ".astra-critic-luna-actor" / "state.sqlite3"))).expanduser()
    lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with lock_path.with_name(lock_path.name + ".watch.lock").open("w") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another ACLA watcher already owns this state store") from exc
        while True:
            cmd_poll(argparse.Namespace(state=args.state, limit=args.limit, interval=args.interval,
                                        worker_id=args.worker_id))
            if args.once:
                return
            time.sleep(max(1, args.interval))


def cmd_status(args) -> None:
    store = Store(args.state)
    print(json.dumps(store.status(args.run_id), indent=2))
    store.close()


def cmd_messages(args) -> None:
    store = Store(args.state)
    print(json.dumps(store.messages(args.thread_id), indent=2))
    store.close()


def add_state(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--state", default=argparse.SUPPRESS)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="acla", description="Astra Critic Luna Actor local coordinator")
    p.add_argument("--state", help="SQLite state path; defaults to ~/.astra-critic-luna-actor/state.sqlite3")
    sub = p.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="initialize the local inbox store"); add_state(init); init.set_defaults(func=cmd_init)
    start = sub.add_parser("run-start", help="create or reuse a run, pair, thread, Luna, and watcher"); add_state(start)
    start.add_argument("--goal", required=True); start.add_argument("--workspace", required=True)
    start.add_argument("--astra-name", default="Astra Critic"); start.add_argument("--astra-id")
    start.add_argument("--astra-session"); start.add_argument("--astra-socket"); start.add_argument("--astra-workspace")
    start.add_argument("--astra-command", default="codex")
    start.add_argument("--luna-name", required=True); start.add_argument("--luna-session"); start.add_argument("--luna-id")
    start.add_argument("--luna-model", default=os.environ.get("ACLA_LUNA_MODEL", "gpt-5.6-sol"))
    start.add_argument("--command"); start.add_argument("--run-id"); start.add_argument("--interval", type=int, default=300)
    start.set_defaults(func=cmd_run_start)
    send = sub.add_parser("send", help="send a full message on a thread"); add_state(send)
    send.add_argument("--thread-id", required=True); send.add_argument("--sender-id", required=True)
    send.add_argument("--body"); send.add_argument("--body-file"); send.add_argument("--idempotency-key")
    send.set_defaults(func=cmd_send)
    for name, marker, help_text in (("send-reply", "", "Luna reply bound to its one Astra"), ("ask-question", "LUNA_QUESTION", "Luna question bound to its one Astra")):
        command = sub.add_parser(name, help=help_text); add_state(command)
        command.add_argument("--luna-id", required=True); command.add_argument("--thread-id")
        command.add_argument("--body"); command.add_argument("--body-file"); command.add_argument("--idempotency-key")
        command.set_defaults(func=lambda a, m=marker: cmd_luna_message(a, m))
    poll = sub.add_parser("poll", help="deliver pending full messages once"); add_state(poll)
    poll.add_argument("--limit", type=int, default=100); poll.add_argument("--interval", type=int, default=300); poll.add_argument("--worker-id")
    poll.set_defaults(func=cmd_poll)
    watch = sub.add_parser("watch", help="poll and deliver messages repeatedly"); add_state(watch)
    watch.add_argument("--interval", type=int, default=300); watch.add_argument("--limit", type=int, default=100)
    watch.add_argument("--worker-id"); watch.add_argument("--once", action="store_true"); watch.set_defaults(func=cmd_watch)
    status = sub.add_parser("status", help="show a run and its Luna mapping"); add_state(status)
    status.add_argument("--run-id", required=True); status.set_defaults(func=cmd_status)
    messages = sub.add_parser("messages", help="read complete messages on a thread"); add_state(messages)
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
