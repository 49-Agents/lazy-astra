"""Serial Claude Code turns inside an owned tmux pane; SQLite remains the inbox."""
from __future__ import annotations

import json
import os
import uuid
import shlex
import signal
import subprocess
import tempfile
import time

from .store import Store


def run(args):
    from .cli import bootstrap_message, envelope, helper, review_loop_policy, LUNA_REPORTING_POLICY, state_lock
    store = Store(args.state)
    child = None
    log_dir = store.path.parent / 'claude-runtime'
    log_dir.mkdir(mode=0o700, exist_ok=True)
    log_path = log_dir / (str(uuid.UUID(args.luna_id)) + '.jsonl')
    log = os.fdopen(os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), 'a')

    def stop(signum, frame):
        if child is not None and child.poll() is None:
            os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
        raise SystemExit(128 + signum)

    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)}
    try:
        with state_lock(store, 'claude-' + args.luna_id, blocking=False):
            luna = store.agent(args.luna_id)
            if luna['executor_backend'] != 'claude-code':
                raise ValueError('Actor does not use Claude Code')
            thread = store.thread_for_luna(luna['id'])
            identity = {'luna_id': luna['id'], 'run_id': thread['run_id'], 'thread_id': thread['id']}
            env = dict(os.environ)
            env.pop('CODEX_THREAD_ID', None)
            env.pop('CLAUDECODE', None)
            env.update(ACLA_STATE=str(store.path.resolve()), ACLA_LUNA_ID=luna['id'],
                       ACLA_CLAUDE_SESSION_ID=luna['claude_session_id'], CODEX_HOME=luna['codex_home'])
            first = True
            while True:
                luna = store.agent(luna['id'])
                notification = store.db.execute("SELECT * FROM inbox_notifications WHERE recipient_id=? AND state='pending'",
                                                (luna['id'],)).fetchone()
                if not first and notification is None:
                    time.sleep(2)
                    continue
                if notification and not store.notification_dispatching(luna['id'], notification['notification_id']):
                    continue
                prompt = (bootstrap_message(identity, store) if not luna['claude_initialized'] else
                          LUNA_REPORTING_POLICY + '\n\n' + review_loop_policy(luna) +
                          '\nContinue only outstanding inbox instructions; do not repeat completed work.\n' +
                          f'Run {helper(store)} inbox next; drain available batches, remaining silent if empty.')
                if notification:
                    prompt += '\n' + envelope(store, notification)
                argv = shlex.split(luna['command']) + ['--permission-mode', 'bypassPermissions',
                        '--settings', json.dumps({'fastMode': False, 'fastModePerSessionOptIn': True,
                                                  'sandbox': {'enabled': False},
                                                  **({'alwaysThinkingEnabled': False} if luna['model'] == 'local-model' else {})}),
                        '--output-format', 'stream-json', '--verbose',
                        '--resume' if luna['claude_initialized'] else '--session-id', luna['claude_session_id'], '-p']
                if luna['model'] != 'local-model':
                    argv += ['--effort', luna['reasoning_effort'] or 'medium']
                # Use the saved reviewer model; migrated actors retain model inheritance.
                env['CLAUDE_CODE_SUBAGENT_MODEL'] = luna['reviewer_model'] or luna['model']
                env['CLAUDE_CODE_DISABLE_FAST_MODE'] = '1'
                store.db.execute('UPDATE agents SET runtime_error=NULL WHERE id=?', (luna['id'],))
                store.db.commit()
                result = None
                try:
                    # A file avoids pipe deadlock with a large plan and streaming output.
                    with tempfile.TemporaryFile(mode='w+') as input_file:
                        input_file.write(prompt)
                        input_file.seek(0)
                        child = subprocess.Popen(argv, cwd=luna['workspace'], env=env, stdin=input_file,
                                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
                        for line in child.stdout:
                            log.write(line)
                            log.flush()
                            print(line, end='', flush=True)
                            try:
                                event = json.loads(line)
                            except ValueError:
                                continue
                            if event.get('type') == 'system' and event.get('subtype') == 'init':
                                if event.get('permissionMode') != 'bypassPermissions':
                                    raise RuntimeError('Claude did not confirm bypassPermissions mode')
                                if event.get('session_id') != luna['claude_session_id']:
                                    raise RuntimeError('Claude returned an unexpected session ID')
                                store.db.execute('UPDATE agents SET claude_initialized=1 WHERE id=?', (luna['id'],))
                                store.db.commit()
                                if notification:
                                    store.notification_result(luna['id'], notification['notification_id'], 'sent')
                            if event.get('type') == 'result':
                                result = event
                        code = child.wait()
                    if code or result is None or result.get('is_error') or result.get('permission_denials'):
                        raise RuntimeError(f'Claude turn failed (exit={code}); inspect the tmux output/native Claude transcript before resuming')
                    if not store.agent(luna['id'])['claude_initialized']:
                        raise RuntimeError('Claude did not confirm its session identity')
                except (OSError, RuntimeError) as exc:
                    if child is not None and child.poll() is None:
                        os.killpg(child.pid, signal.SIGTERM)
                        try:
                            child.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            os.killpg(child.pid, signal.SIGKILL)
                            child.wait()
                    if notification:
                        store.notification_result(luna['id'], notification['notification_id'], 'uncertain', str(exc))
                    store.db.execute('UPDATE agents SET runtime_error=? WHERE id=?', (str(exc), luna['id']))
                    store.db.commit()
                    raise
                first = False
                approved = store.db.execute('SELECT approved FROM pairs WHERE luna_id=?', (luna['id'],)).fetchone()[0]
                outstanding = store.db.execute('SELECT 1 FROM messages WHERE recipient_id=? AND handled_at IS NULL AND legacy=0 LIMIT 1',
                                               (luna['id'],)).fetchone()
                if approved and outstanding is None:
                    return
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        log.close()
        store.close()
