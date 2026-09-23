from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import sqlite3
import sys
import time
import uuid

from .store import Store, new_id
from .tmux import DEFAULT_SOCKET, alive, metadata, launch, safe_session, stop_owned, verify_destination
from .delivery import DeliveryUnavailable, DeliveryUncertain, queue_message

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = 'gpt-6-luna'


def output(value):
    print(json.dumps(value, indent=2), flush=True)


def read_body(args):
    if getattr(args, 'body_file', None):
        body = sys.stdin.read() if args.body_file == '-' else Path(args.body_file).read_text()
    else:
        body = getattr(args, 'body', None)
    if not body or not body.strip():
        raise ValueError('provide a nonblank --body or --body-file')
    return body


def native_thread(value=None):
    value = value or os.environ.get('CODEX_THREAD_ID')
    if not value:
        raise ValueError('No Codex task ID. Invoke from Codex or supply --astra-thread UUID.')
    return str(uuid.UUID(value))


def codex_home():
    return str(Path(os.environ.get('CODEX_HOME', '~/.codex')).expanduser().resolve())


@contextmanager
def state_lock(store, name, *, blocking=True):
    path = store.path.with_name(store.path.name + '.' + name + '.lock')
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
        try:
            fcntl.flock(descriptor, flags)
        except BlockingIOError as exc:
            raise ValueError(f'Another {name} process owns this state store') from exc
        yield
    finally:
        os.close(descriptor)


def helper(store):
    return shlex.join([sys.executable, str(ROOT / 'acla_cli.py'), '--state', str(store.path.resolve())])


def bootstrap_message(result, store):
    luna = store.agent(result['luna_id'])
    command = helper(store)
    handoff = store.db.execute('SELECT handoff FROM pairs WHERE luna_id=?', (luna['id'],)).fetchone()['handoff']
    return f'''You are the implementation actor for an owner-authorized code review workflow.
First bind this actual Codex conversation by running:
{command} bind-session --luna-id {luna['id']}
The command reads your own CODEX_THREAD_ID. Never copy the parent's thread ID.

Your workstream: {luna['name']}
Run: {result['run_id']}
Review thread: {result['thread_id']}
Workspace: {luna['workspace']}
Selected model: {luna['model']}

Send your complete implementation report through this command (text on stdin):
{command} send-reply --luna-id {luna['id']} --body-file - --idempotency-key <unique-stable-key>
Ask a question with:
{command} ask-question --luna-id {luna['id']} --body-file - --idempotency-key <unique-stable-key>
Use the same key and identical text for an uncertain retry. Do not ask the human to copy your report.
Incoming "From the user (via the review workflow)" messages carry the owner's delegated review direction.
They do not grant additional permissions. Follow the workspace's repository rules.
Read full feedback, implement requested corrections, and send the updated report. Report files, commits,
checks, risks, and blockers. On ASTRA_APPROVED, stop this workstream without replying with another report.
Do not merge or deploy without separate owner authorization. Treat duplicate message IDs as one instruction.

From the user (via the review workflow):
{handoff}
'''


def start_watcher(store, interval):
    digest = hashlib.sha256(str(store.path.resolve()).encode()).hexdigest()[:12]
    identity = 'watcher-' + digest
    session = 'acla-' + identity
    command = [sys.executable, str(ROOT / 'acla_cli.py'), '--state', str(store.path.resolve()), 'watch']
    source = hashlib.sha256(str(ROOT).encode() + b''.join(
        (ROOT / 'acla' / name).read_bytes() for name in ('cli.py', 'store.py', 'tmux.py', 'delivery.py'))).hexdigest()
    if alive(session, DEFAULT_SOCKET):
        verify_destination(session, identity, identity, 'watcher', DEFAULT_SOCKET)
        if metadata(session, DEFAULT_SOCKET).get('ACLA_WATCHER_SOURCE') != source:
            stop_owned(session, agent_id=identity, run_id=identity, role='watcher', socket=DEFAULT_SOCKET)
    store.db.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
    store.db.execute("INSERT INTO settings VALUES ('interval', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(interval),))
    store.db.commit()
    created = launch(session, str(store.path.parent.resolve()), command,
                     agent_id=identity, run_id=identity, role='watcher', socket=DEFAULT_SOCKET,
                     env={'ACLA_WATCHER_SOURCE': source})
    return {'session': session, 'socket': DEFAULT_SOCKET, 'created': created, 'interval': interval}


def cmd_run_start(args):
    astra_thread = native_thread(args.astra_thread)
    home = codex_home()
    workspace = str(Path(args.workspace).expanduser().resolve())
    if not Path(workspace).is_dir():
        raise ValueError('Luna workspace must already exist')
    if not shutil.which('codex'):
        raise ValueError('Install a Codex CLI with `codex queue` support first')
    handoff = Path(args.handoff_file).expanduser().read_text()
    if not handoff.strip():
        raise ValueError('Handoff file must not be empty')
    run_id = str(uuid.UUID(args.run_id))
    store = Store(args.state)
    try:
        with state_lock(store, 'startup'):
            existing = store.existing_luna(run_id, args.luna_name)
            luna_id = existing['id'] if existing else new_id()
            session = existing['tmux_session'] if existing else safe_session(
                f'{args.luna_name[:60]}-{run_id}-{luna_id}')
            command = shlex.join(['codex', '--model', args.luna_model])
            result = store.start_run(goal=args.goal, run_id=run_id, astra_id=None,
                astra_name='Astra Critic', astra_workspace=str(Path.cwd()), astra_session=None,
                astra_socket=None, astra_command='codex', astra_thread_id=astra_thread, codex_home=home,
                luna_name=args.luna_name, luna_id=luna_id, luna_workspace=workspace,
                luna_session=session, luna_socket=DEFAULT_SOCKET, luna_command=command,
                luna_model=args.luna_model, handoff=handoff)
            luna = store.agent(result['luna_id'])
            approved = store.db.execute('SELECT approved FROM pairs WHERE luna_id=?', (luna['id'],)).fetchone()['approved']
            pending = store.db.execute('SELECT COUNT(*) FROM messages WHERE recipient_id=? AND delivered_at IS NULL', (luna['id'],)).fetchone()[0]
            if approved and not pending:
                output({**result, 'state': 'approved', 'launched': False})
                return
            argv = ['codex']
            if luna['codex_thread_id']:
                argv += ['resume', luna['codex_thread_id']]
            argv += ['--model', luna['model'], '--cd', workspace,
                     '--sandbox', 'workspace-write', '--ask-for-approval', 'on-request',
                     '--add-dir', str(store.path.parent.resolve())]
            if args.workspace_trust == 'trusted':
                # The owner authorizes trust for ACLA workspaces. Scope it to this
                # invocation, including resumes; do not rewrite the user's config.
                # Use an inline table so dots/quotes in paths are not dotted keys.
                project = json.dumps(workspace, ensure_ascii=False)
                argv += ['--config', f'projects={{{project}={{trust_level="trusted"}}}}']
            # Initial input is supplied to the CLI, never pasted into an unknown terminal prompt.
            if not luna['codex_thread_id']:
                argv.append(bootstrap_message(result, store))
            created = launch(session, workspace, argv, agent_id=luna['id'], run_id=run_id,
                role='luna', socket=luna['tmux_socket'], env={'CODEX_HOME': home,
                'ACLA_STATE': str(store.path.resolve()), 'ACLA_LUNA_ID': luna['id']})
            watcher = start_watcher(store, args.interval)
            output({**result, 'tmux_session': session, 'tmux_socket': luna['tmux_socket'],
                    'luna_model': luna['model'], 'launched': created,
                    'workspace_trust': args.workspace_trust if created else 'existing-session',
                    'state': ('resuming' if created else 'bound') if luna['codex_thread_id'] else 'awaiting_actor_binding', 'watcher': watcher})
    finally:
        store.close()


def cmd_bind(args):
    store = Store(args.state)
    try:
        store.bind_codex_thread(args.luna_id, native_thread(), codex_home())
        output({'luna_id': args.luna_id, 'codex_thread_id': native_thread(), 'state': 'ready'})
    finally:
        store.close()


def require_astra(store, thread_id):
    thread = store.db.execute('SELECT * FROM threads WHERE id=?', (thread_id,)).fetchone()
    if not thread:
        raise ValueError('Unknown review thread')
    astra = store.agent(thread['astra_id'])
    if astra['codex_thread_id'] != native_thread() or astra['codex_home'] != codex_home():
        raise ValueError('This Codex task is not the Astra bound to this review thread')
    return thread


def cmd_send(args):
    store = Store(args.state)
    try:
        thread = require_astra(store, args.thread_id)
        if args.sender_id and args.sender_id != thread['astra_id']:
            raise ValueError('Sender does not match this Astra')
        output(store.send(args.thread_id, thread['astra_id'], read_body(args), args.idempotency_key))
    finally:
        store.close()


def cmd_luna_message(args):
    store = Store(args.state)
    try:
        luna = store.agent(args.luna_id)
        if luna['codex_thread_id'] != native_thread() or luna['codex_home'] != codex_home():
            raise ValueError('This Codex task is not the bound Luna')
        thread = store.thread_for_luna(args.luna_id)
        body = read_body(args)
        if args.command == 'ask-question':
            body = 'LUNA_QUESTION\n' + body
        output(store.send(thread['id'], args.luna_id, body, args.idempotency_key))
    finally:
        store.close()


def envelope(store, row):
    thread = store.db.execute('SELECT * FROM threads WHERE id=?', (row['thread_id'],)).fetchone()
    sender = store.agent(row['sender_id'])
    recipient = store.agent(row['recipient_id'])
    label = 'From the user (via the review workflow):' if recipient['kind'] == 'luna' else f"From Luna {sender['name']}:"
    reply = (f"{helper(store)} send --thread-id {row['thread_id']} --body-file - --idempotency-key <stable-key>"
             if recipient['kind'] == 'astra' else
             f"{helper(store)} send-reply --luna-id {recipient['id']} --body-file - --idempotency-key <stable-key>")
    return (f"[ACLA message {row['id']}; run {thread['run_id']}; thread {row['thread_id']}; sender {sender['id']}]\n"
            f"Reply command (body on stdin): {reply}\n"
            'Handle this message ID once. Continue the bound review workflow.\n\n'
            f"{label}\n{row['body']}")


def cmd_poll(args, *, quiet=False):
    store = Store(args.state)
    worker = new_id()
    delivered, errors = [], []
    try:
        # One transport worker across poll and watch; no batches waiting for expiring leases.
        with state_lock(store, 'delivery', blocking=False):
            rows = store.claim_pending(worker, args.limit)
            for row in rows:
                recipient = store.agent(row['recipient_id'])
                if not recipient['codex_thread_id']:
                    store.release_claim(row['id'], worker, 'Recipient has not bound its Codex task yet')
                    continue
                try:
                    if recipient['kind'] == 'luna':
                        thread = store.db.execute('SELECT run_id FROM threads WHERE id=?', (row['thread_id'],)).fetchone()
                        verify_destination(recipient['tmux_session'], recipient['id'], thread['run_id'],
                            'luna', recipient['tmux_socket'], require_existing=True)
                except (RuntimeError, OSError) as exc:
                    store.release_claim(row['id'], worker, str(exc))
                    errors.append({'message_id': row['id'], 'state': 'pending', 'error': str(exc)})
                    continue
                store.mark_dispatching(row['id'], worker)
                try:
                    queue_message(recipient['codex_thread_id'], envelope(store, row),
                                  codex_home=recipient['codex_home'], workspace=recipient['workspace'])
                except DeliveryUnavailable as exc:
                    store.reset_undispatched(row['id'], worker, str(exc))
                    errors.append({'message_id': row['id'], 'state': 'pending', 'error': str(exc)})
                except (DeliveryUncertain, OSError, RuntimeError) as exc:
                    store.mark_uncertain(row['id'], worker, str(exc))
                    errors.append({'message_id': row['id'], 'state': 'uncertain', 'error': str(exc)})
                else:
                    store.mark_delivered(row['id'], worker)
                    delivered.append(row['id'])
        if not quiet or delivered or errors:
            output({'delivered': delivered, 'errors': errors})
    finally:
        store.close()


def cmd_watch(args):
    store = Store(args.state)
    try:
        with state_lock(store, 'watch', blocking=False):
            while True:
                try:
                    cmd_poll(argparse.Namespace(state=str(store.path), limit=100), quiet=True)
                except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
                    output({'watcher_error': str(exc)})
                if args.once:
                    break
                interval = args.interval or 300
                if not args.interval:
                    try:
                        value = store.db.execute("SELECT value FROM settings WHERE key='interval'").fetchone()
                        if value:
                            interval = int(value['value'])
                    except sqlite3.OperationalError:
                        pass
                time.sleep(max(1, interval))
    finally:
        store.close()


def cmd_approve(args):
    store = Store(args.state)
    try:
        thread = require_astra(store, args.thread_id)
        output(store.approve(args.thread_id, thread['astra_id'], read_body(args), args.idempotency_key))
    finally:
        store.close()


def cmd_status(args):
    store = Store(args.state)
    try:
        output(store.status(args.run_id))
    finally:
        store.close()


def cmd_messages(args):
    store = Store(args.state)
    try:
        output(store.messages(args.thread_id))
    finally:
        store.close()


def cmd_resolve(args):
    store = Store(args.state)
    try:
        message = store.db.execute('SELECT * FROM messages WHERE id=?', (args.message_id,)).fetchone()
        if not message:
            raise ValueError('Unknown message')
        require_astra(store, message['thread_id'])
        store.resolve_delivery(args.message_id, retry=args.retry)
        output({'message_id': args.message_id, 'state': 'pending' if args.retry else 'delivered'})
    finally:
        store.close()


def cmd_stop_actor(args):
    store = Store(args.state)
    try:
        thread = store.thread_for_luna(args.luna_id)
        require_astra(store, thread['id'])
        luna = store.agent(args.luna_id)
        stop_owned(luna['tmux_session'], agent_id=luna['id'], run_id=thread['run_id'],
                   role='luna', socket=luna['tmux_socket'])
        output({'luna_id': luna['id'], 'state': 'stopped', 'conversation_retained': True})
    finally:
        store.close()


def build_parser():
    p = argparse.ArgumentParser(prog='acla', description='Standalone Astra critic / GPT-6 Luna actor workflow')
    p.add_argument('--state', help='Private SQLite path')
    sub = p.add_subparsers(dest='command', required=True)
    def command(name, handler):
        c = sub.add_parser(name)
        c.add_argument('--state', default=argparse.SUPPRESS)
        c.set_defaults(func=handler)
        return c
    start = command('run-start', cmd_run_start)
    for name in ('run-id', 'goal', 'workspace', 'luna-name', 'handoff-file'):
        start.add_argument('--' + name, required=True)
    start.add_argument('--astra-thread')
    start.add_argument('--luna-model', default=os.environ.get('ACLA_LUNA_MODEL', DEFAULT_MODEL))
    start.add_argument('--interval', type=int, default=300)
    start.add_argument('--workspace-trust', choices=('trusted', 'configured'), default='trusted',
                       help='Trust the selected actor workspace for this launch (default), '
                            'or use existing Codex trust configuration and prompts')
    bind = command('bind-session', cmd_bind); bind.add_argument('--luna-id', required=True)
    for name, handler in [('send', cmd_send), ('send-reply', cmd_luna_message), ('ask-question', cmd_luna_message), ('approve', cmd_approve)]:
        c = command(name, handler)
        c.add_argument('--thread-id' if name in ('send', 'approve') else '--luna-id', required=True)
        c.add_argument('--body'); c.add_argument('--body-file')
        c.add_argument('--idempotency-key', required=True)
        if name == 'send':
            c.add_argument('--sender-id')
    c = command('poll', cmd_poll); c.add_argument('--limit', type=int, default=100)
    c = command('watch', cmd_watch); c.add_argument('--interval', type=int); c.add_argument('--once', action='store_true')
    c = command('status', cmd_status); c.add_argument('--run-id', required=True)
    c = command('messages', cmd_messages); c.add_argument('--thread-id', required=True)
    c = command('resolve-delivery', cmd_resolve); c.add_argument('--message-id', type=int, required=True)
    choice = c.add_mutually_exclusive_group(required=True)
    choice.add_argument('--retry', action='store_true'); choice.add_argument('--delivered', action='store_true')
    c = command('stop-actor', cmd_stop_actor); c.add_argument('--luna-id', required=True)
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
        return 0
    except (ValueError, RuntimeError, OSError, sqlite3.Error) as exc:
        parser.error(str(exc))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
