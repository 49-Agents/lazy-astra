"""Close owned, approved Worker terminals after a full day of inbox silence."""
from datetime import datetime, timezone

from .tmux import alive, stop_owned

IDLE_SECONDS = 24 * 60 * 60


def reap_approved_workers(store, clock=None):
    # Serialize with run-start; the database transaction also excludes new messages
    # between checking eligibility and closing the terminal.
    from .cli import state_lock
    clock = clock or datetime.now(timezone.utc)
    stopped, errors = [], []
    with state_lock(store, 'startup'):
        workers = store.db.execute("SELECT luna_id FROM pairs WHERE approved=1").fetchall()
        for row in workers:
            worker_id = row['luna_id']
            store.db.execute('BEGIN IMMEDIATE')
            try:
                worker = store.agent(worker_id)
                pair = store.db.execute('SELECT * FROM pairs WHERE luna_id=?', (worker_id,)).fetchone()
                if not pair['approved']:
                    continue
                thread = store.thread_for_luna(worker_id)
                messages = store.db.execute('SELECT * FROM messages WHERE thread_id=?', (thread['id'],)).fetchall()
                # Missing history or unhandled/leased/uncertain work is not idle.
                if not messages or any(m['inbox_token'] or
                        (m['handled_at'] is None and (not m['legacy'] or not m['delivered_at'])) or
                        m['delivery_state'] in ('claimed', 'dispatching', 'uncertain') for m in messages):
                    continue
                note = store.db.execute('SELECT state FROM inbox_notifications WHERE recipient_id=?', (worker_id,)).fetchone()
                if note and note['state'] in ('pending', 'dispatching', 'claimed', 'uncertain'):
                    continue
                stamps = [datetime.fromisoformat(m[field].replace('Z', '+00:00'))
                          for m in messages for field in ('created_at', 'delivered_at', 'read_at', 'handled_at') if m[field]]
                if any(stamp.tzinfo is None for stamp in stamps):
                    continue
                if (clock - max(stamps)).total_seconds() < IDLE_SECONDS:
                    continue
                if not alive(worker['tmux_session'], worker['tmux_socket']):
                    continue
                stop_owned(worker['tmux_session'], agent_id=worker_id, run_id=pair['run_id'],
                           role='luna', socket=worker['tmux_socket'])
                stopped.append(worker_id)
            except (OSError, RuntimeError, ValueError) as exc:
                errors.append({'worker_id': worker_id, 'error_type': type(exc).__name__})
            finally:
                store.db.rollback()  # No inbox/history mutations, even after a successful close.
    return {'stopped': stopped, 'errors': errors}
